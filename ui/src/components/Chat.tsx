// Unified activity feed: user/assistant text, the model's reasoning, and
// tool activity all interleaved in conversation order.
//
// Interleaving skeleton comes from the AG-UI messages (assistant messages
// carry their top-level toolCalls in order). The protocol is also the
// authority on a top-level call's LIFECYCLE: the agent loop cannot continue
// until every call has a tool result, so "result message exists" ⇔ the
// call finished — success or error (a ModelRetry arrives as a retry prompt
// ending in "Fix the errors and try again."). The side-channel bridge feed
// only enriches — args/code, result preview, duration, error text — and
// contributes the NESTED data fetches made inside the code tool, which the
// protocol never sees. Nested activities are grouped under the top-level
// call whose execution window contains them.

import { useEffect, useMemo, useRef, useState } from "react";
import type { Message } from "@ag-ui/client";
import ReactMarkdown, { type Components } from "react-markdown";
import type { ToolActivity } from "../api";
import CodeBlock from "./CodeBlock";

/** Fenced code blocks in assistant markdown get highlighted (```python,
 *  ```json, …); inline `code` is left to the default renderer. */
const MARKDOWN_COMPONENTS: Components = {
  pre: ({ children }) => <>{children}</>,
  code: ({ className, children, ...rest }) => {
    const match = /language-(\w+)/.exec(className ?? "");
    const text = String(children ?? "").replace(/\n$/, "");
    // Fenced blocks arrive wrapped in <pre> (we unwrap above) and are the
    // only ones with a language class or a newline in them.
    if (match || text.includes("\n")) {
      return <CodeBlock code={text} language={match?.[1] ?? "auto"} format={match?.[1] === "json"} />;
    }
    return (
      <code className={className} {...rest}>
        {children}
      </code>
    );
  },
};

interface Props {
  messages: readonly Message[];
  activities: Map<string, ToolActivity>;
  running: boolean;
  error: string | null;
  onDismissError: () => void;
  onSend: (text: string) => void;
  onNewConversation: () => void;
  placeholder?: string;
  hint?: string;
  dataSourceName?: string;
}

interface ToolCallRef {
  id: string;
  name: string;
  args?: string;
}

interface PlanTask {
  id: string;
  content: string;
  status: string;
  activeForm?: string;
}

type FeedItem =
  | { kind: "text"; key: string; role: "user" | "assistant"; text: string }
  | { kind: "reasoning"; key: string; text: string; live: boolean }
  | { kind: "tools"; key: string; calls: ToolCallRef[] }
  | { kind: "plan"; key: string; tasks: PlanTask[] };

/** Planning-capability tools whose calls we fold into checklist cards. */
const PLAN_TOOLS = new Set([
  "write_plan",
  "add_task",
  "update_task_status",
  "update_task_statuses",
  "remove_task",
  "read_plan",
  "read_plan_tree",
]);

/** One rendered plan line as the server prints it (write_plan reply, plan
 *  reminder, read_plan): `n. [~] [d709dc01] text`. The bracketed id is what
 *  update_task_statuses expects; ids are auto-generated server-side, so the
 *  write_plan *args* never carry them. */
const PLAN_LINE = /^\s*\d+\.\s+\[([ ~x\-!])\]\s+(?:\[([^\]\s]+)\]\s+)?(.+?)\s*$/;
const PLAN_ICON_STATUS: Record<string, string> = {
  " ": "pending",
  "~": "in_progress",
  x: "completed",
  "-": "cancelled",
  "!": "blocked",
};

/** Rebuild the plan from a plan tool's *result* text — the authoritative
 *  state with real ids and statuses. Returns false when the text holds no
 *  plan lines (e.g. an update summary or an error). */
function applyPlanResult(plan: Map<string, PlanTask>, result: string | undefined): boolean {
  if (!result) return false;
  const parsed: PlanTask[] = [];
  for (const raw of result.split("\n")) {
    const m = PLAN_LINE.exec(raw);
    if (!m) continue;
    const [, icon, id, content] = m;
    parsed.push({ id: id ?? String(parsed.length + 1), content, status: PLAN_ICON_STATUS[icon] ?? "pending" });
  }
  if (parsed.length === 0) return false;
  // Keep active_form labels (only present in write_plan args) across rebuilds.
  const previous = [...plan.values()];
  plan.clear();
  for (const t of parsed) {
    const old = previous.find((p) => p.id === t.id) ?? previous.find((p) => p.content === t.content);
    plan.set(t.id, { ...t, activeForm: old?.activeForm });
  }
  return true;
}

/** Apply one plan tool call to the running plan state. Returns false when
 *  the args can't be interpreted (e.g. still streaming) so the caller falls
 *  back to a generic tool card. */
function applyPlanCall(plan: Map<string, PlanTask>, name: string, argsJson?: string): boolean {
  let args: Record<string, unknown>;
  try {
    args = JSON.parse(argsJson || "{}");
  } catch {
    return false;
  }
  if (name === "write_plan") {
    const items = args.items;
    if (!Array.isArray(items)) return false;
    plan.clear();
    for (const it of items as Array<Record<string, unknown>>) {
      const id = String(it.id ?? plan.size + 1);
      plan.set(id, {
        id,
        content: String(it.content ?? ""),
        status: String(it.status ?? "pending"),
        activeForm: typeof it.active_form === "string" ? it.active_form : undefined,
      });
    }
    return true;
  }
  if (name === "add_task") {
    if (typeof args.content !== "string") return false;
    const id = `task-${plan.size + 1}`;
    plan.set(id, {
      id,
      content: args.content,
      status: "pending",
      activeForm: typeof args.active_form === "string" ? args.active_form : undefined,
    });
    return true;
  }
  if (name === "update_task_status") {
    const t = plan.get(String(args.task_id));
    if (t && typeof args.status === "string") t.status = args.status;
    return true;
  }
  if (name === "update_task_statuses") {
    if (!Array.isArray(args.updates)) return false;
    for (const u of args.updates as Array<Record<string, unknown>>) {
      const t = plan.get(String(u.task_id));
      if (t && typeof u.status === "string") t.status = u.status;
    }
    return true;
  }
  if (name === "remove_task") {
    plan.delete(String(args.task_id));
    return true;
  }
  // read_plan / read_plan_tree: no state change, no card needed.
  return true;
}

function textOf(message: Message): string {
  const content = (message as { content?: unknown }).content;
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .map((part) => (typeof part === "string" ? part : (part as { text?: string }).text ?? ""))
      .join("");
  }
  return "";
}

function toolCallsOf(message: Message): ToolCallRef[] {
  const calls = (message as { toolCalls?: unknown }).toolCalls;
  if (!Array.isArray(calls)) return [];
  return calls
    .filter((c): c is { id: string; function?: { name?: string; arguments?: string } } =>
      Boolean(c && typeof (c as { id?: unknown }).id === "string"),
    )
    .map((c) => ({
      id: c.id,
      name: c.function?.name ?? "tool",
      args: c.function?.arguments,
    }));
}

/** Build the ordered feed and assign nested bridge activities to parents. */
/** Text a ModelRetry becomes in the protocol (RetryPromptPart.model_response). */
const RETRY_MARKER = /Fix the errors and try again\.?\s*$/;

/** What the protocol says about one top-level call. */
interface ProtocolResult {
  text: string;
  failed: boolean;
}

function buildFeed(
  messages: readonly Message[],
  activities: Map<string, ToolActivity>,
  running: boolean,
): {
  items: FeedItem[];
  nestedByParent: Map<string, ToolActivity[]>;
  orphans: ToolActivity[];
  protocolResults: Map<string, ProtocolResult>;
} {
  const items: FeedItem[] = [];
  const topLevelIds = new Set<string>();
  const plan = new Map<string, PlanTask>();
  // Signature of the last checklist card pushed, so a call that changed
  // nothing (failed status update, plan rewritten with identical content)
  // doesn't render the same card again.
  let lastPlanSig = "";

  // Authoritative tool results: the protocol's tool messages, by call id.
  // Present ⇔ the call finished (the agent loop can't proceed otherwise).
  const protocolResults = new Map<string, ProtocolResult>();
  for (const m of messages) {
    if ((m.role as string) !== "tool") continue;
    const callId = (m as { toolCallId?: unknown }).toolCallId;
    if (typeof callId !== "string") continue;
    const text = textOf(m);
    protocolResults.set(callId, { text, failed: RETRY_MARKER.test(text) });
  }

  // Result text for plan reconstruction: protocol first (full text), the
  // bridge feed's preview as fallback (JSON-encoded string, may be clipped).
  const resultsByCall = new Map<string, string>();
  for (const a of activities.values()) {
    if (a.result === undefined) continue;
    let text = a.result;
    try {
      const decoded = JSON.parse(text);
      if (typeof decoded === "string") text = decoded;
    } catch {
      /* not JSON — use as-is */
    }
    resultsByCall.set(a.callId, text);
  }
  for (const [callId, r] of protocolResults) {
    if (r.text !== "") resultsByCall.set(callId, r.text);
  }

  messages.forEach((m, i) => {
    const key = m.id ?? String(i);
    const role = m.role as string;
    if (role === "user" || role === "assistant") {
      const text = textOf(m);
      // The harness injects memory/reminders as user-role messages; they are
      // part of the persisted history but were never typed by the user.
      if (role === "user" && /^\s*<(memory|system-reminder)>/.test(text)) return;
      if (text.trim() !== "") {
        items.push({ kind: "text", key, role: role as "user" | "assistant", text });
      }
      if (role === "assistant") {
        const calls = toolCallsOf(m);
        calls.forEach((c) => topLevelIds.add(c.id));
        // Split the message's calls into generic tool groups and plan cards,
        // preserving order. Plan calls fold into the running plan state and
        // render as a checklist snapshot at the point they happened.
        let group: ToolCallRef[] = [];
        const flush = () => {
          if (group.length > 0) {
            items.push({ kind: "tools", key: `${key}-tools-${items.length}`, calls: group });
            group = [];
          }
        };
        for (const call of calls) {
          if (!PLAN_TOOLS.has(call.name)) {
            group.push(call);
            continue;
          }
          // Prefer the result (real ids + statuses); fall back to the args
          // while the result hasn't arrived yet.
          const applied =
            applyPlanResult(plan, resultsByCall.get(call.id)) ||
            applyPlanCall(plan, call.name, call.args);
          if (!applied) {
            group.push(call);
            continue;
          }
          flush();
          if (call.name === "read_plan" || call.name === "read_plan_tree" || plan.size === 0) continue;
          const tasks = [...plan.values()].map((t) => ({ ...t }));
          const sig = JSON.stringify(tasks.map((t) => [t.id, t.status, t.content]));
          if (sig === lastPlanSig) continue;
          lastPlanSig = sig;
          items.push({ kind: "plan", key: call.id, tasks });
        }
        flush();
      }
    } else if (role === "reasoning") {
      const text = textOf(m);
      if (text.trim() !== "") {
        const live = running && i === messages.length - 1;
        items.push({ kind: "reasoning", key, text, live });
      }
    }
    // role "tool" (results) intentionally skipped: previews come from the
    // bridge feed, which also covers nested calls the protocol can't see.
  });

  // Nested activity → parent top-level call whose window contains it.
  const parents = [...activities.values()]
    .filter((a) => topLevelIds.has(a.callId))
    .sort((a, b) => a.startedAt - b.startedAt);
  const nestedByParent = new Map<string, ToolActivity[]>();
  const orphans: ToolActivity[] = [];
  for (const a of activities.values()) {
    if (topLevelIds.has(a.callId)) continue;
    let parent: ToolActivity | undefined;
    for (const p of parents) {
      if (p.startedAt <= a.startedAt) parent = p;
      else break;
    }
    if (parent) {
      const list = nestedByParent.get(parent.callId) ?? [];
      list.push(a);
      nestedByParent.set(parent.callId, list);
    } else {
      orphans.push(a);
    }
  }
  for (const list of nestedByParent.values()) {
    list.sort((a, b) => a.startedAt - b.startedAt || a.seq - b.seq);
  }
  orphans.sort((a, b) => a.startedAt - b.startedAt || a.seq - b.seq);
  return { items, nestedByParent, orphans, protocolResults };
}

/** Lifecycle of a top-level call, decided in this order of authority:
 *  protocol result (finished, failed?) → bridge end event → "run is over,
 *  nothing can still be running" → otherwise running. */
function topLevelState(
  protocol: ProtocolResult | undefined,
  activity: ToolActivity | undefined,
  running: boolean,
): { done: boolean; failed: boolean } {
  if (protocol) return { done: true, failed: protocol.failed };
  if (activity?.done) return { done: true, failed: activity.status === "error" || activity.status === "cancelled" };
  if (!running) return { done: true, failed: activity !== undefined }; // interrupted mid-call
  return { done: false, failed: false };
}

function Duration({ ms }: { ms?: number }) {
  if (ms == null) return null;
  return <span className="duration">{ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`}</span>;
}

function dotClass(done: boolean, failed: boolean): string {
  return failed ? "error" : done ? "done" : "running";
}

/** A nested call inherits closure from its parent: once the parent call
 *  finished (protocol says so), none of its children can still be running,
 *  even if the bridge never delivered their end event. */
function NestedRow({ activity, parentDone }: { activity: ToolActivity; parentDone: boolean }) {
  const [open, setOpen] = useState(false);
  const done = activity.done || parentDone;
  const failed = activity.status === "error" || activity.status === "cancelled";
  return (
    <div className="nested-row">
      <button className="nested-head" onClick={() => setOpen(!open)}>
        <span className={`dot ${dotClass(done, failed)}`} />
        <span className="tool-name">{activity.tool}</span>
        {activity.agent !== "main" && <span className="agent-tag">{activity.agent}</span>}
        {failed && <span className="error-tag">{activity.status}</span>}
        <Duration ms={activity.durationMs} />
      </button>
      {open && (
        <div className="tool-body">
          {activity.code ? (
            <CodeBlock className="code" language="python" code={activity.code} />
          ) : (
            activity.args && <CodeBlock className="args" language="json" format code={activity.args} />
          )}
          {activity.error && (
            <>
              <div className="label">error</div>
              <pre className="result error">{activity.error}</pre>
            </>
          )}
          {activity.result && (
            <>
              <div className="label">result</div>
              <CodeBlock className="result" format code={activity.result} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function ToolCard({
  call,
  activity,
  nested,
  protocol,
  running,
}: {
  call: ToolCallRef;
  activity?: ToolActivity;
  nested: ToolActivity[];
  protocol?: ProtocolResult;
  running: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const { done, failed } = topLevelState(protocol, activity, running);
  const code = activity?.code;
  const args = activity?.args ?? call.args;
  const hasNested = nested.length > 0;
  // Error text: the bridge's (exception class + message) when it closed the
  // span, else the protocol's retry prompt — the full text the model saw.
  const errorText = failed ? activity?.error ?? protocol?.text : undefined;
  const resultText = failed ? undefined : activity?.result ?? protocol?.text;
  // A block whose nested data calls all returned but that still failed died in its
  // own code (after the data came back) — say so, or green sub-dots under a
  // red parent look like a bug.
  const nestedFailed = nested.filter((n) => n.status === "error" || n.status === "cancelled").length;
  const codeFailed = failed && hasNested && nestedFailed === 0 && activity?.status !== "cancelled";
  const errorTag = activity?.status === "cancelled" ? "cancelled" : codeFailed ? "code error" : "error";
  return (
    <div className={`tool-card ${dotClass(done, failed)}`}>
      <button className="tool-head" onClick={() => setOpen(!open)}>
        <span className={`dot ${dotClass(done, failed)}`} />
        <span className="tool-name">{call.name}</span>
        {hasNested && (
          <span className="nested-count">
            {nested.length} calls{codeFailed ? " ok" : nestedFailed > 0 ? `, ${nestedFailed} failed` : ""}
          </span>
        )}
        {failed && <span className="error-tag">{errorTag}</span>}
        <Duration ms={activity?.durationMs} />
        <span className="chevron">{open ? "▾" : "▸"}</span>
      </button>
      {/* Nested data fetches inside the code tool are always listed — the function
          names are the useful part; each row expands to its own args/result. */}
      {hasNested && (
        <div className="nested-list always">
          {nested.map((n) => (
            <NestedRow key={n.callId} activity={n} parentDone={done} />
          ))}
        </div>
      )}
      {open && (
        <div className="tool-body">
          {code ? (
            <div className="code-accordion">
              <button className="code-toggle" onClick={() => setShowCode(!showCode)}>
                <span className="chevron">{showCode ? "▾" : "▸"}</span> code
                <span className="code-lines">{code.split("\n").length} lines</span>
              </button>
              {showCode && <CodeBlock className="code" language="python" code={code} />}
            </div>
          ) : (
            args && <CodeBlock className="args" language="json" format code={args} />
          )}
          {errorText && (
            <>
              <div className="label">error</div>
              {codeFailed && (
                <div className="error-hint">
                  All {nested.length} {dataSourceName} calls returned; the block's own code failed afterwards, so the
                  agent had to re-run it.
                </div>
              )}
              <pre className="result error">{errorText}</pre>
            </>
          )}
          {resultText && (
            <>
              <div className="label">result</div>
              <CodeBlock className="result" format code={resultText} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

const PLAN_ICONS: Record<string, string> = {
  completed: "✓",
  in_progress: "●",
  cancelled: "✕",
  blocked: "⊘",
};

function PlanCard({ tasks }: { tasks: PlanTask[] }) {
  const done = tasks.filter((t) => t.status === "completed").length;
  const pct = tasks.length > 0 ? Math.round((100 * done) / tasks.length) : 0;
  return (
    <div className="plan-card">
      <div className="plan-head">
        <span className="plan-title">Plan</span>
        <div className="plan-bar">
          <div className="plan-bar-fill" style={{ width: `${pct}%` }} />
        </div>
        <span className="plan-progress">
          {done}/{tasks.length}
        </span>
      </div>
      <ul className="plan-list">
        {tasks.map((t) => (
          <li key={t.id} className={`plan-task ${t.status}`}>
            <span className="plan-icon">{PLAN_ICONS[t.status] ?? "○"}</span>
            <span className="plan-text">
              {t.status === "in_progress" && t.activeForm ? t.activeForm : t.content}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Reasoning({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  const preview = text.replace(/\s+/g, " ").trim();
  if (live) {
    return (
      <div className="reasoning live">
        <span className="reasoning-label">Reasoning</span>
        <p>{text}</p>
      </div>
    );
  }
  return (
    <div className="reasoning">
      <button className="reasoning-head" onClick={() => setOpen(!open)}>
        <span className="reasoning-label">Reasoning</span>
        {!open && <span className="reasoning-preview">{preview.slice(0, 100)}</span>}
        <span className="chevron">{open ? "▾" : "▸"}</span>
      </button>
      {open && <p>{text}</p>}
    </div>
  );
}

function ErrorBanner({ text, onDismiss }: { text: string; onDismiss: () => void }) {
  return (
    <div className="error-banner" role="alert">
      <div className="error-banner-head">
        <span className="error-banner-title">Something went wrong</span>
        <button
          className="ghost"
          onClick={() => navigator.clipboard?.writeText(text).catch(() => {})}
          title="Copy the full error"
        >
          Copy
        </button>
        <button className="ghost" onClick={onDismiss} title="Dismiss">
          ✕
        </button>
      </div>
      <pre className="error-banner-body">{text}</pre>
    </div>
  );
}

export default function Chat({
  messages,
  activities,
  running,
  error,
  onDismissError,
  onSend,
  onNewConversation,
  placeholder = "Ask a question…",
  hint,
  dataSourceName = "the data source",
}: Props) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const { items, nestedByParent, orphans, protocolResults } = useMemo(
    () => buildFeed(messages, activities, running),
    [messages, activities, running],
  );

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [items.length, activities.size, running, error]);

  const send = () => {
    const text = draft.trim();
    if (!text || running) return;
    setDraft("");
    onSend(text);
  };

  const liveReasoning = running && items.length > 0 && items[items.length - 1].kind === "reasoning";

  return (
    <section className="pane chat-pane">
      <header className="pane-header">
        <span>Conversation</span>
        <button className="ghost" onClick={onNewConversation} title="Start a fresh thread">
          New conversation
        </button>
      </header>
      <div className="chat-scroll">
        {items.length === 0 && !running && (
          <p className="hint">
            {hint ??
              "Ask the analysis agent anything. Its reasoning and every tool it runs will appear here, inline."}
          </p>
        )}
        {items.map((item) => {
          if (item.kind === "text") {
            return (
              <div key={item.key} className={`bubble ${item.role}`}>
                {item.role === "assistant" ? (
                  <ReactMarkdown components={MARKDOWN_COMPONENTS}>{item.text}</ReactMarkdown>
                ) : (
                  <p>{item.text}</p>
                )}
              </div>
            );
          }
          if (item.kind === "reasoning") {
            return <Reasoning key={item.key} text={item.text} live={item.live} />;
          }
          if (item.kind === "plan") {
            return <PlanCard key={item.key} tasks={item.tasks} />;
          }
          return (
            <div key={item.key} className="tool-group">
              {item.calls.map((call) => (
                <ToolCard
                  key={call.id}
                  call={call}
                  activity={activities.get(call.id)}
                  nested={nestedByParent.get(call.id) ?? []}
                  protocol={protocolResults.get(call.id)}
                  running={running}
                />
              ))}
            </div>
          );
        })}
        {running && orphans.length > 0 && (
          <div className="tool-group">
            {orphans.map((a) => (
              <NestedRow key={a.callId} activity={a} parentDone={false} />
            ))}
          </div>
        )}
        {running && !liveReasoning && <div className="bubble assistant thinking">Working…</div>}
        {error && <ErrorBanner text={error} onDismiss={onDismissError} />}
        <div ref={bottomRef} />
      </div>
      <footer className="chat-input">
        <textarea
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          rows={3}
        />
        <button onClick={send} disabled={running || draft.trim() === ""}>
          {running ? "Running…" : "Send"}
        </button>
      </footer>
    </section>
  );
}
