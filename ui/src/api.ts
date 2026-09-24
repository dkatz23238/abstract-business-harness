// Server endpoints and helpers shared by the panes.

/** Same-origin during `vite`/`vite preview` so any localhost UI port works
 *  without CORS. `VITE_API_URL` is the proxy *target* (see vite.config.ts).
 *  Production builds that are not served through Vite still use it as an
 *  absolute origin. */
export const API_URL: string = import.meta.env.DEV
  ? ""
  : ((import.meta.env.VITE_API_URL as string | undefined) ?? "");

export interface ProfileConfig {
  id: string;
  name: string;
  title: string;
  data_calls_label: string;
  data_source_name: string;
  placeholder: string;
  hint?: string;
  code_tool: string;
  model: string;
  /** Profile default; the chat picker starts here on a new thread. */
  default_effort?: EffortLevel;
  effort_levels?: EffortLevel[];
  /** Names and phrases masked in the UI when `?redact=1`. */
  redact_terms?: string[];
}

export const EFFORT_LEVELS = ["low", "medium", "high", "xhigh"] as const;
export type EffortLevel = (typeof EFFORT_LEVELS)[number];
export const DEFAULT_EFFORT: EffortLevel = "medium";

const EFFORT_SET = new Set<string>(EFFORT_LEVELS);

export function coerceEffort(value: unknown, fallback: EffortLevel = DEFAULT_EFFORT): EffortLevel {
  return typeof value === "string" && EFFORT_SET.has(value) ? (value as EffortLevel) : fallback;
}

export async function fetchProfile(): Promise<ProfileConfig> {
  const res = await fetch(`${API_URL}/profile`);
  if (!res.ok) throw new Error(`profile failed: ${res.status}`);
  return (await res.json()) as ProfileConfig;
}

export interface WorkspaceFile {
  path: string;
  size: number;
  mtime: number;
  kind: string;
}

export async function fetchWorkspaceFiles(threadId: string): Promise<WorkspaceFile[]> {
  const res = await fetch(`${API_URL}/workspace/${threadId}/files`);
  if (!res.ok) throw new Error(`workspace listing failed: ${res.status}`);
  const data = await res.json();
  return data.files as WorkspaceFile[];
}

export function rawFileUrl(threadId: string, path: string): string {
  return `${API_URL}/workspace/raw/${threadId}/${path}`;
}

// ---- detached run status ----

/** Runs execute server-side, detached from the request; this is the truth
 *  about whether a thread is still working (survives page reloads). */
export interface RunStatus {
  active: boolean;
  run_id?: string | null;
  started_at?: number;
  finished_at?: number | null;
  error?: string | null;
}

export async function fetchRunStatus(threadId: string): Promise<RunStatus> {
  const res = await fetch(`${API_URL}/runs/${threadId}`);
  if (!res.ok) throw new Error(`run status failed: ${res.status}`);
  return (await res.json()) as RunStatus;
}

// ---- conversation store (past threads) ----

export interface ThreadSummary {
  id: string;
  title: string;
  updated_at: number;
  message_count: number;
  /** Model that last answered in this thread; null for threads saved before models were recorded. */
  model: string | null;
  /** Reasoning effort last used in this thread; null for threads saved before effort was recorded. */
  effort: EffortLevel | null;
}

export interface ThreadListing {
  threads: ThreadSummary[];
  /** Model a new (not yet run) thread will use. */
  default_model: string;
  /** Effort a new thread starts at. */
  default_effort: EffortLevel;
}

export async function listThreads(): Promise<ThreadListing> {
  const res = await fetch(`${API_URL}/threads`);
  if (!res.ok) throw new Error(`thread listing failed: ${res.status}`);
  const data = await res.json();
  return {
    threads: data.threads as ThreadSummary[],
    default_model: data.default_model ?? "",
    default_effort: coerceEffort(data.default_effort),
  };
}

/** "openai:gpt-5.6-luna" -> "gpt-5.6-luna" for compact display. */
export function shortModelName(model: string | null | undefined): string {
  if (!model) return "";
  const i = model.indexOf(":");
  return i >= 0 ? model.slice(i + 1) : model;
}

export interface ThreadDetail<M = unknown> {
  id: string;
  title: string;
  messages: M[];
  model?: string | null;
  effort?: string | null;
}

export async function loadThread<M>(threadId: string): Promise<ThreadDetail<M>> {
  const res = await fetch(`${API_URL}/threads/${threadId}`);
  if (!res.ok) throw new Error(`thread load failed: ${res.status}`);
  const data = (await res.json()) as ThreadDetail<M>;
  return {
    id: data.id ?? threadId,
    title: data.title ?? "",
    messages: (data.messages ?? []) as M[],
    model: data.model,
    effort: data.effort,
  };
}

export async function loadThreadMessages<M>(threadId: string): Promise<M[]> {
  return (await loadThread<M>(threadId)).messages;
}

export async function saveThreadMessages(
  threadId: string,
  messages: unknown,
  extra?: { effort?: EffortLevel },
): Promise<void> {
  await fetch(`${API_URL}/threads/${threadId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ messages, ...(extra?.effort ? { effort: extra.effort } : {}) }),
  });
}

/** Delete a conversation plus its tool events, plan and workspace files. */
export async function deleteThread(threadId: string): Promise<void> {
  const res = await fetch(`${API_URL}/threads/${threadId}`, { method: "DELETE" });
  if (!res.ok) {
    const detail = await res.json().then((d) => d.detail).catch(() => res.status);
    throw new Error(`delete failed: ${detail}`);
  }
}

/** One tool execution, merged from the bridge's start/end events. */
export interface ToolActivity {
  callId: string;
  tool: string;
  agent: string;
  seq: number;
  startedAt: number;
  code?: string;
  args?: string;
  durationMs?: number;
  result?: string;
  /** Set on the end event when the tool raised (ModelRetry, exception) or was cancelled. */
  error?: string;
  status?: "ok" | "error" | "cancelled";
  /** "data" = one individual data-source read; "control" = agent machinery. */
  kind?: "data" | "control";
  done: boolean;
}

/** Whole-thread counters the server sends first on subscribe (they cover
 *  the full stored log, not just the replayed tail). */
export interface ToolFeedSummary {
  dataCalls: number;
  codeBlocks: number;
  lastTs: number;
  usage: UsageTotals;
}

/** Token/cost totals of one conversation (server-side store). */
export interface UsageTotals {
  requests: number;
  /** Requests reconstructed from the transcript (pre-metering) rather than metered. */
  estimatedRequests: number;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  costUsd: number;
  /** False when some request had no known price (model missing from the table). */
  costKnown: boolean;
  models: Record<string, { requests: number; input_tokens: number; output_tokens: number; cost_usd: number }>;
  lastTs: number;
}

/** One metered model request, published live while a run is going. */
export interface UsageRecord {
  ts: number;
  model: string;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  costUsd: number | null;
}

function toUsageTotals(u: Record<string, unknown> | undefined): UsageTotals {
  const n = (k: string) => Number((u as Record<string, unknown> | undefined)?.[k] ?? 0);
  return {
    requests: n("requests"),
    estimatedRequests: n("estimated_requests"),
    inputTokens: n("input_tokens"),
    cacheReadTokens: n("cache_read_tokens"),
    outputTokens: n("output_tokens"),
    reasoningTokens: n("reasoning_tokens"),
    costUsd: n("cost_usd"),
    costKnown: (u?.cost_known as boolean | undefined) ?? true,
    models: (u?.models as UsageTotals["models"] | undefined) ?? {},
    lastTs: n("last_ts"),
  };
}

export type ToolEventHandler = (activity: ToolActivity) => void;

/** Subscribe to the nested tool-call SSE feed for a thread. Returns cleanup. */
export function subscribeToolEvents(
  threadId: string,
  onUpdate: (updater: (prev: Map<string, ToolActivity>) => Map<string, ToolActivity>) => void,
  onSummary?: (summary: ToolFeedSummary) => void,
  onUsage?: (record: UsageRecord) => void,
): () => void {
  const source = new EventSource(`${API_URL}/events/${threadId}`);
  source.onmessage = (msg) => {
    const ev = JSON.parse(msg.data);
    if (ev.phase === "summary") {
      onSummary?.({
        dataCalls: ev.data_calls ?? 0,
        codeBlocks: ev.code_blocks ?? 0,
        lastTs: ev.last_ts ?? 0,
        usage: toUsageTotals(ev.usage),
      });
      return;
    }
    if (ev.phase === "usage") {
      onUsage?.({
        ts: ev.ts ?? 0,
        model: ev.model ?? "?",
        inputTokens: ev.input_tokens ?? 0,
        cacheReadTokens: ev.cache_read_tokens ?? 0,
        outputTokens: ev.output_tokens ?? 0,
        reasoningTokens: ev.reasoning_tokens ?? 0,
        costUsd: ev.cost_usd ?? null,
      });
      return;
    }
    onUpdate((prev) => {
      const next = new Map(prev);
      const existing = next.get(ev.call_id);
      if (ev.phase === "start") {
        // A replayed/duplicated start must never un-finish a call whose
        // end already arrived (replay + live can deliver an event twice).
        if (existing?.done) return prev;
        next.set(ev.call_id, {
          callId: ev.call_id,
          tool: ev.tool,
          agent: ev.agent,
          seq: ev.seq,
          startedAt: ev.ts,
          code: ev.code,
          args: ev.args,
          kind: ev.kind,
          done: false,
        });
      } else if (ev.phase === "end") {
        next.set(ev.call_id, {
          ...(existing ?? {
            callId: ev.call_id,
            tool: ev.tool,
            agent: ev.agent,
            seq: ev.seq,
            startedAt: ev.ts,
            kind: ev.kind,
          }),
          durationMs: ev.duration_ms,
          result: ev.result,
          error: ev.error,
          status: ev.status ?? (ev.error ? "error" : "ok"),
          done: true,
        });
      }
      return next;
    });
  };
  return () => source.close();
}
