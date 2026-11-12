import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { AgentSubscriber, Message } from "@ag-ui/client";
import {
  activateThread,
  agent,
  attachToRun,
  freshThreadId,
  persistConversation,
} from "./agent";
import {
  changePassword,
  deleteThread,
  fetchMe,
  fetchRunStatus,
  fetchProfile,
  getApiKey,
  listThreads,
  login,
  rotateMyApiKey,
  setApiKey,
  setUnauthorizedHandler,
  subscribeApiKey,
  shortModelName,
  subscribeToolEvents,
  coerceEffort,
  DEFAULT_EFFORT,
  type EffortLevel,
  type ProfileConfig,
  type RunStatus,
  type ThreadSummary,
  type ToolActivity,
  type ToolFeedSummary,
  type UsageRecord,
  type UserInfo,
} from "./api";
import Chat from "./components/Chat";
import Threads from "./components/Threads";
import Assets from "./components/Assets";
import Admin from "./components/Admin";
import Users from "./components/Users";
import Modal, { btnDanger, btnGhost, btnPrimary, inputClass, SecretInput } from "./components/Modal";
import { redactEnabled, setRedactTerms } from "./redact";
import "./App.css";

/** Human-readable error text: unwrap Error objects, keep full detail. */
function errText(e: unknown): string {
  if (e instanceof Error) return e.message || String(e);
  return String(e);
}

/** HttpAgent treats a protocol RUN_ERROR as a finished stream, not a thrown
 *  fetch failure — so the chat pane has to listen for it or the turn looks empty. */
function liveSubscriber(onMessages: () => void, onError: (message: string) => void): AgentSubscriber {
  return {
    onEvent: () => onMessages(),
    onRunErrorEvent: ({ event }) => {
      if (event.message) onError(event.message);
    },
  };
}

function fmtTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 100_000 ? 0 : 1)}k`;
  return String(n);
}

function fmtUsd(n: number): string {
  return n < 0.01 && n > 0 ? "<$0.01" : `$${n.toFixed(2)}`;
}

interface UsageView {
  requests: number;
  estimatedRequests: number;
  inputTokens: number;
  cacheReadTokens: number;
  outputTokens: number;
  reasoningTokens: number;
  costUsd: number;
  costKnown: boolean;
  models: Record<string, { requests: number; input_tokens: number; output_tokens: number; cost_usd: number }>;
}

function usageTitle(u: UsageView): string {
  const lines = [
    `${u.requests.toLocaleString()} model requests in this conversation`,
    `Input ${u.inputTokens.toLocaleString()} tokens (${u.cacheReadTokens.toLocaleString()} read from cache)`,
    `Output ${u.outputTokens.toLocaleString()} tokens (${u.reasoningTokens.toLocaleString()} reasoning)`,
    u.costKnown
      ? `Cost ≈ $${u.costUsd.toFixed(4)} at list price (${u.estimatedRequests > 0 ? "estimated" : "exact"} tokens, list-price estimate)`
      : "Cost unknown: no list price for one of the models (set [model.prices] on the profile)",
  ];
  for (const [model, m] of Object.entries(u.models)) {
    lines.push(
      `  ${shortModelName(model)}: ${m.requests} req · ${m.input_tokens.toLocaleString()} in · ${m.output_tokens.toLocaleString()} out · $${m.cost_usd.toFixed(4)}`,
    );
  }
  if (u.estimatedRequests > 0) {
    lines.push(
      u.estimatedRequests === u.requests
        ? "All requests predate metering: tokens reconstructed from the transcript (input ±10%, output ±40%)."
        : `${u.estimatedRequests} of ${u.requests} requests predate metering and are reconstructed from the transcript.`,
    );
  }
  return lines.join("\n");
}

export default function App() {
  const [threadId, setThreadId] = useState<string>(agent.threadId);
  // Owner of the open thread when it is someone else's (read-only); null for your own.
  const [viewOwner, setViewOwner] = useState<string | null>(null);
  const [messages, setMessages] = useState<readonly Message[]>([]);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [defaultModel, setDefaultModel] = useState<string>("");
  const [modelChoices, setModelChoices] = useState<string[]>([]);
  // Empty means the profile default.
  const [model, setModel] = useState<string>("");
  const [defaultEffort, setDefaultEffort] = useState<EffortLevel>(DEFAULT_EFFORT);
  const [effort, setEffort] = useState<EffortLevel>(DEFAULT_EFFORT);
  const [running, setRunning] = useState(false);
  const [activities, setActivities] = useState<Map<string, ToolActivity>>(new Map());
  const [feedSummary, setFeedSummary] = useState<ToolFeedSummary | null>(null);
  const [runError, setRunError] = useState<string | null>(null);

  const [profile, setProfile] = useState<ProfileConfig | null>(null);
  const [view, setView] = useState<"chat" | "users" | "admin">("chat");
  const [pendingDelete, setPendingDelete] = useState<string | null>(null);
  const [signedIn, setSignedIn] = useState(() => !!getApiKey());
  const [me, setMe] = useState<UserInfo | null>(null);
  const [apiKeyEpoch, setApiKeyEpoch] = useState(0);
  const [loginMode, setLoginMode] = useState<"password" | "key">("password");
  const [usernameInput, setUsernameInput] = useState("");
  const [passwordInput, setPasswordInput] = useState("");
  const [keyInput, setKeyInput] = useState("");
  const [loginError, setLoginError] = useState<string | null>(null);
  const [signingIn, setSigningIn] = useState(false);

  const [accountOpen, setAccountOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [nextPassword, setNextPassword] = useState("");
  const [accountNotice, setAccountNotice] = useState<string | null>(null);
  const [accountError, setAccountError] = useState<string | null>(null);
  const [myNewKey, setMyNewKey] = useState<string | null>(null);

  useEffect(() => subscribeApiKey(() => setApiKeyEpoch((n) => n + 1)), []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setApiKey("");
      setSignedIn(false);
      setMe(null);
      setLoginError("Your session ended. Sign in again.");
    });
  }, []);

  useEffect(() => {
    if (!signedIn) return;
    fetchMe()
      .then(setMe)
      .catch(() => {});
    fetchProfile()
      .then((p) => {
        setRedactTerms(p.redact_terms ?? []);
        setProfile(p);
        document.title = p.title;
        const fromProfile = coerceEffort(p.default_effort);
        setDefaultEffort(fromProfile);
      })
      .catch(() => {});
  }, [signedIn]);

  // Data-call counter for the banner: every individual data-source read in
  // this thread (each function inside a code-tool block counts, not the
  // block). The server's summary covers the whole stored log; on top of it
  // we count only the live calls it hadn't stored yet.
  const dataStats = useMemo(() => {
    const base = feedSummary ?? { dataCalls: 0, codeBlocks: 0, lastTs: 0 };
    let calls = base.dataCalls;
    let blocks = base.codeBlocks;
    let live = 0;
    const codeTool = profile?.code_tool ?? "run_code";
    for (const a of activities.values()) {
      if (a.startedAt <= base.lastTs) continue;
      if (a.kind === "data") calls += 1;
      else if (a.tool === codeTool || a.tool === "run_code") blocks += 1;
    }
    for (const a of activities.values()) {
      if (a.kind === "data" && !a.done) live += 1;
    }
    return { calls, blocks, live };
  }, [activities, feedSummary, profile]);

  // Token/cost counter: the server's stored totals plus the live records it
  // hadn't stored when we subscribed (same last_ts dedupe as the calls).
  const [liveUsage, setLiveUsage] = useState<UsageRecord[]>([]);
  const usage = useMemo(() => {
    const base = feedSummary?.usage;
    const t = {
      requests: base?.requests ?? 0,
      estimatedRequests: base?.estimatedRequests ?? 0,
      inputTokens: base?.inputTokens ?? 0,
      cacheReadTokens: base?.cacheReadTokens ?? 0,
      outputTokens: base?.outputTokens ?? 0,
      reasoningTokens: base?.reasoningTokens ?? 0,
      costUsd: base?.costUsd ?? 0,
      costKnown: base?.costKnown ?? true,
      models: { ...(base?.models ?? {}) },
    };
    for (const r of liveUsage) {
      if (r.ts <= (base?.lastTs ?? 0)) continue;
      t.requests += 1;
      t.inputTokens += r.inputTokens;
      t.cacheReadTokens += r.cacheReadTokens;
      t.outputTokens += r.outputTokens;
      t.reasoningTokens += r.reasoningTokens;
      if (r.costUsd === null) t.costKnown = false;
      else t.costUsd += r.costUsd;
      const m = (t.models[r.model] ??= { requests: 0, input_tokens: 0, output_tokens: 0, cost_usd: 0 });
      m.requests += 1;
      m.input_tokens += r.inputTokens;
      m.output_tokens += r.outputTokens;
      m.cost_usd += r.costUsd ?? 0;
    }
    return t;
  }, [feedSummary, liveUsage]);

  const canViewOthers = me?.can_view_others ?? false;
  const refreshThreads = useCallback(() => {
    listThreads(canViewOthers ? "all" : "mine")
      .then(({ threads, default_model, model_choices, default_effort }) => {
        setThreads(threads);
        setDefaultModel(default_model);
        setModelChoices(model_choices);
        setDefaultEffort(coerceEffort(default_effort));
      })
      .catch((e) => setRunError(`Could not load past conversations — is the backend running?\n${errText(e)}`));
  }, [canViewOthers]);

  // Model shown in the header: the one picked for this thread, else the one
  // that last answered in it, else the server default.
  const activeModel =
    model ||
    (threads.find((t) => t.id === threadId && t.owner === (viewOwner ?? me?.username))?.model ??
      defaultModel);
  const pickModel = (stored: string | null) =>
    setModel(stored && modelChoices.includes(stored) ? stored : "");

  // Runs are detached: they live on the server, not in this tab's request.
  // When we lost the original POST stream (page reload, dropped connection),
  // re-attach to the buffered AG-UI stream so text/reasoning/tools keep
  // flowing live. If the attach stream can't be consumed, fall back to
  // polling the run status and loading the persisted conversation at the end.
  const watchingRef = useRef<string | null>(null);
  // Runs this tab deliberately stopped following (the user navigated to
  // another conversation mid-run). The run itself continues on the server;
  // we just must not treat the aborted local stream as "the run finished".
  const abandonedRef = useRef<Set<string>>(new Set());
  const watchRun = useCallback(
    async (id: string, owner: string | null = null) => {
      if (watchingRef.current === id) return;
      watchingRef.current = id;
      abandonedRef.current.delete(id);
      setRunning(true);
      try {
        await attachToRun(
          liveSubscriber(
            () => setMessages([...agent.messages]),
            (msg) => setRunError(msg),
          ),
          owner,
        );
        if (abandonedRef.current.has(id)) return;
        if (agent.threadId === id) {
          setMessages([...agent.messages]);
          setRunning(false);
          if (!owner) persistConversation().catch(() => {});
        }
        refreshThreads();
      } catch {
        let status: RunStatus | null = null;
        for (;;) {
          await new Promise((r) => setTimeout(r, 2000));
          if (abandonedRef.current.has(id)) return;
          status = await fetchRunStatus(id, owner).catch(() => null);
          if (status && !status.active) break;
        }
        if (agent.threadId === id) {
          setMessages(await activateThread(id, owner).then((t) => t.messages).catch(() => agent.messages));
          if (status?.error) setRunError(`The run failed on the server:\n${status.error}`);
          setRunning(false);
        }
        refreshThreads();
      } finally {
        watchingRef.current = null;
      }
    },
    [refreshThreads],
  );

  /** If the thread has a live server-side run, reflect it and follow it. */
  const syncRunState = useCallback(
    async (id: string, owner: string | null = null) => {
      const status = await fetchRunStatus(id, owner).catch(() => null);
      if (status?.active) watchRun(id, owner);
    },
    [watchRun],
  );

  // On mount: restore this tab's conversation from the server + thread list,
  // and pick up a run that survived the reload.
  const restoredRef = useRef(false);
  useEffect(() => {
    if (!signedIn) {
      restoredRef.current = false;
      return;
    }
    if (restoredRef.current) return;
    restoredRef.current = true;
    activateThread(agent.threadId)
      .then(({ messages, effort: stored, model: storedModel }) => {
        setMessages(messages);
        setEffort(coerceEffort(stored));
        setModel(storedModel ?? "");
        return syncRunState(agent.threadId);
      })
      .catch((e) => setRunError(`Could not restore this conversation:\n${errText(e)}`));
  }, [signedIn, syncRunState]);

  useEffect(() => {
    if (signedIn) refreshThreads();
  }, [signedIn, refreshThreads]);

  // Bridge tool-event feed follows the active thread (server replays the
  // stored tail on subscribe, so tool detail survives refreshes too).
  useEffect(() => {
    if (!signedIn) return;
    setFeedSummary(null);
    setLiveUsage([]);
    return subscribeToolEvents(
      threadId,
      setActivities,
      setFeedSummary,
      (r) => setLiveUsage((prev) => [...prev, r]),
      viewOwner,
    );
  }, [signedIn, threadId, viewOwner, apiKeyEpoch]);

  const signIn = async (e: FormEvent) => {
    e.preventDefault();
    setSigningIn(true);
    setLoginError(null);
    try {
      if (loginMode === "password") {
        setMe(await login(usernameInput.trim(), passwordInput));
      } else {
        setApiKey(keyInput.trim());
        setMe(await fetchMe());
      }
      const p = await fetchProfile();
      setRedactTerms(p.redact_terms ?? []);
      setProfile(p);
      document.title = p.title;
      setDefaultEffort(coerceEffort(p.default_effort));
      setPasswordInput("");
      setKeyInput("");
      setSignedIn(true);
    } catch (err) {
      setApiKey("");
      setLoginError(loginMode === "password" ? errText(err) : "That API key was rejected.");
    } finally {
      setSigningIn(false);
    }
  };

  const signOut = () => {
    setApiKey("");
    setSignedIn(false);
    setMe(null);
    setProfile(null);
    setView("chat");
    setAccountOpen(false);
    setLoginError(null);
    setPasswordInput("");
    setKeyInput("");
  };

  const closeAccount = () => {
    setAccountOpen(false);
    setCurrentPassword("");
    setNextPassword("");
    setAccountNotice(null);
    setAccountError(null);
    setMyNewKey(null);
  };

  const submitPasswordChange = async (e: FormEvent) => {
    e.preventDefault();
    setAccountError(null);
    setAccountNotice(null);
    try {
      await changePassword(currentPassword, nextPassword);
      setCurrentPassword("");
      setNextPassword("");
      setAccountNotice("Password changed. Other sessions were signed out.");
    } catch (err) {
      setAccountError(errText(err));
    }
  };

  const rotateMine = async () => {
    setAccountError(null);
    setAccountNotice(null);
    try {
      setMyNewKey(await rotateMyApiKey());
    } catch (err) {
      setAccountError(errText(err));
    }
  };

  /** Stop following the current thread's run in this tab. The run keeps
   *  executing detached on the server; coming back re-attaches to it. */
  const leaveRun = () => {
    if (!running) return;
    abandonedRef.current.add(agent.threadId);
    agent.abortRun();
    setRunning(false);
  };

  const switchThread = async (id: string, owner: string | null = null) => {
    if (id === threadId && owner === viewOwner) return;
    leaveRun();
    try {
      const { messages, effort: stored, model: storedModel } = await activateThread(id, owner);
      setMessages(messages);
      setEffort(coerceEffort(stored, defaultEffort));
      pickModel(storedModel);
    } catch (e) {
      setRunError(`Could not open that conversation:\n${errText(e)}`);
      syncRunState(threadId, viewOwner).catch(() => {}); // still here: follow its run again
      return;
    }
    setActivities(new Map());
    setRunError(null);
    setThreadId(id);
    setViewOwner(owner);
    syncRunState(id, owner).catch(() => {});
  };

  const removeThread = async (id: string) => {
    // The server refuses anyway while a run is active; avoid the round-trip
    // for the common case (delete button on the currently-running thread).
    if (running && id === threadId && viewOwner === null) return;
    setPendingDelete(id);
  };

  const confirmDeleteThread = async () => {
    const id = pendingDelete;
    if (!id) return;
    setPendingDelete(null);
    try {
      await deleteThread(id);
    } catch (e) {
      setRunError(`Could not delete the conversation:\n${errText(e)}`);
      return;
    }
    if (id === threadId && viewOwner === null) {
      newThread();
    }
    refreshThreads();
  };

  const newThread = () => {
    leaveRun();
    const id = freshThreadId();
    agent.threadId = id;
    agent.setMessages([]);
    setMessages([]);
    setActivities(new Map());
    setRunError(null);
    setThreadId(id);
    setViewOwner(null);
    setEffort(defaultEffort);
    setModel("");
  };

  const send = async (text: string) => {
    if (viewOwner) return;
    setRunError(null);
    const id = agent.threadId;
    abandonedRef.current.delete(id);
    agent.addMessage({ id: crypto.randomUUID(), role: "user", content: text });
    setMessages([...agent.messages]);
    setRunning(true);
    // Persist the prompt right away so a mid-run refresh still shows it, and
    // list the thread so the user can navigate away and back while it runs.
    const chosen = model || undefined;
    persistConversation({ effort, model: chosen }).then(refreshThreads).catch(() => {});

    const finishAttached = () => {
      setRunning(false);
      setMessages([...agent.messages]);
      persistConversation({ effort, model: chosen }).then(refreshThreads).catch(() => {});
    };

    try {
      await agent.runAgent(
        { forwardedProps: { effort, ...(chosen ? { model: chosen } : {}) } },
        liveSubscriber(
          () => setMessages([...agent.messages]),
          (msg) => setRunError(msg),
        ),
      );
      // The user navigated away mid-run: the aborted local stream is not
      // the run finishing. The thread re-attaches when they come back.
      if (abandonedRef.current.has(id)) return;
      finishAttached();
    } catch (e) {
      if (abandonedRef.current.has(id)) return;
      // A dropped stream doesn't mean the run stopped: it executes detached
      // on the server. If it's still going, re-attach (or poll) and pick up
      // the result when it finishes.
      const status = await fetchRunStatus(id).catch(() => null);
      if (status?.active) {
        watchRun(id);
      } else {
        setRunError(status?.error ? `The run failed on the server:\n${status.error}` : errText(e));
        finishAttached();
      }
    }
  };

  if (!signedIn) {
    const ready =
      loginMode === "password" ? !!usernameInput.trim() && !!passwordInput : !!keyInput.trim();
    return (
      <div className="app flex items-center justify-center p-8">
        <form
          className="w-full max-w-[420px] rounded-[10px] border border-line bg-pane p-6 shadow-[0_1px_2px_rgba(24,38,32,0.05),0_2px_8px_rgba(24,38,32,0.04)]"
          onSubmit={signIn}
        >
          <h1 className="mb-3 text-[19px] font-semibold text-ink">Sign in</h1>
          {loginMode === "password" ? (
            <div className="flex flex-col gap-2">
              <input
                className={inputClass}
                placeholder="Username"
                autoComplete="username"
                value={usernameInput}
                onChange={(e) => setUsernameInput(e.target.value)}
                autoFocus
              />
              <input
                className={inputClass}
                type="password"
                placeholder="Password"
                autoComplete="current-password"
                value={passwordInput}
                onChange={(e) => setPasswordInput(e.target.value)}
              />
            </div>
          ) : (
            <>
              <p className="mb-3 text-[15px] leading-relaxed text-muted">
                Paste your API key. It is the same key scripts send as a Bearer token.
              </p>
              <SecretInput value={keyInput} onChange={setKeyInput} placeholder="API key" />
            </>
          )}
          <button type="submit" className={`${btnPrimary} mt-3 w-full`} disabled={signingIn || !ready}>
            Sign in
          </button>
          <button
            type="button"
            className="mt-2 w-full border-none bg-transparent text-[14px] text-muted underline"
            onClick={() => {
              setLoginMode(loginMode === "password" ? "key" : "password");
              setLoginError(null);
            }}
          >
            {loginMode === "password" ? "Use an API key instead" : "Use username and password"}
          </button>
          {loginError && <p className="mt-3 mb-0 text-[14px] text-danger">{loginError}</p>}
        </form>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="app-header">
        <h1>{profile?.title ?? "Analysis Agent"}</h1>
        {redactEnabled() && (
          <span
            className="demo-redact"
            title="?redact=1 — figures and profile redact terms are masked for display"
          >
            {(profile?.redact_terms?.length ?? 0) > 0 ? "names hidden" : "numbers hidden"}
          </span>
        )}
        {view === "chat" && (
          <>
            <span className="thread">
              {viewOwner ? `${viewOwner} · ` : ""}thread {threadId.slice(0, 8)}
            </span>
            {activeModel && (
              <span className="model-tag header" title={`Model: ${activeModel}`}>
                {shortModelName(activeModel)}
              </span>
            )}
            {feedSummary && (
            <span
              key={dataStats.calls}
              className={`data-counter${dataStats.live > 0 ? " live" : ""}`}
                title={`Individual ${profile?.data_source_name ?? "data"} API calls in this conversation, across ${dataStats.blocks.toLocaleString()} ${profile?.code_tool ?? "code"} block${dataStats.blocks === 1 ? "" : "s"}${dataStats.live > 0 ? ` — ${dataStats.live} running now` : ""}`}
              >
                <span className="data-counter-value">{dataStats.calls.toLocaleString()}</span>
                <span className="data-counter-label">{profile?.data_calls_label ?? "API calls"}</span>
                {dataStats.live > 0 && <span className="data-counter-live">{dataStats.live} running</span>}
              </span>
            )}
            {feedSummary && usage.requests > 0 && (
              <span className="usage-counter" title={usageTitle(usage)}>
                <span className="usage-part">
                  <span className="usage-value">{fmtTokens(usage.inputTokens)}</span> in
                </span>
                <span className="usage-part">
                  <span className="usage-value">{fmtTokens(usage.outputTokens)}</span> out
                </span>
                <span className="usage-part cost">
                  {usage.costKnown ? `≈ ${fmtUsd(usage.costUsd)}` : "cost n/a"}
                  {usage.estimatedRequests > 0 && <span className="usage-est">est.</span>}
                </span>
              </span>
            )}
          </>
        )}
        <nav className="header-nav">
          <button
            className={`ghost ${view === "chat" ? "active" : ""}`}
            onClick={() => setView("chat")}
          >
            Chat
          </button>
          {me?.role === "admin" && (
            <>
              <button
                className={`ghost ${view === "users" ? "active" : ""}`}
                onClick={() => setView("users")}
              >
                Users
              </button>
              <button
                className={`ghost ${view === "admin" ? "active" : ""}`}
                onClick={() => setView("admin")}
              >
                Admin
              </button>
            </>
          )}
          <button className="ghost" onClick={() => setAccountOpen(true)} title="Account">
            {me?.username ?? "Account"}
          </button>
          <button className="ghost" onClick={signOut}>
            Sign out
          </button>
        </nav>
      </header>
      {view === "admin" ? (
        <main className="layout admin-layout">
          <Admin />
        </main>
      ) : view === "users" ? (
        <main className="layout admin-layout">
          <Users me={me?.username ?? ""} />
        </main>
      ) : (
      <main className="layout">
        <Threads
          threads={threads}
          me={me?.username ?? ""}
          activeId={threadId}
          activeOwner={viewOwner}
          onSelect={switchThread}
          onNew={newThread}
          onDelete={removeThread}
        />
        <div className="panes">
          <Chat
            key={`${viewOwner ?? ""}/${threadId}`}
            messages={messages}
            activities={activities}
            running={running}
            error={runError}
            onDismissError={() => setRunError(null)}
            onSend={send}
            onNewConversation={newThread}
            effort={effort}
            onEffortChange={(next) => {
              setEffort(next);
              if (agent.messages.length > 0) persistConversation({ effort: next }).catch(() => {});
            }}
            model={activeModel}
            modelChoices={modelChoices}
            onModelChange={(next) => {
              setModel(next);
              if (agent.messages.length > 0) persistConversation({ model: next }).catch(() => {});
            }}
            placeholder={profile?.placeholder}
            hint={profile?.hint}
            dataSourceName={profile?.data_source_name}
            readOnlyOwner={viewOwner}
          />
          <Assets running={running} threadId={threadId} owner={viewOwner} />
        </div>
      </main>
      )}
      <Modal
        open={pendingDelete !== null}
        title="Delete conversation?"
        onClose={() => setPendingDelete(null)}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={() => setPendingDelete(null)}>
              Cancel
            </button>
            <button type="button" className={btnDanger} onClick={confirmDeleteThread}>
              Delete
            </button>
          </>
        }
      >
        This removes the thread, its tool history, and workspace files.
      </Modal>
      <Modal
        open={accountOpen}
        title={`Account · ${me?.username ?? ""}`}
        onClose={closeAccount}
        footer={
          <button type="button" className={btnGhost} onClick={closeAccount}>
            Close
          </button>
        }
      >
        {me && (
          <p className="mt-0 text-[14px] text-muted">
            Role <strong>{me.role}</strong> ·{" "}
            {me.can_view_others ? "can read other users' conversations" : "sees only your own conversations"}
          </p>
        )}
        <form className="flex flex-col gap-2" onSubmit={submitPasswordChange}>
          <h3 className="m-0 text-[15px] font-semibold text-ink">Change password</h3>
          <input
            className={inputClass}
            type="password"
            placeholder="Current password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
          />
          <input
            className={inputClass}
            type="password"
            placeholder="New password (8+ characters)"
            autoComplete="new-password"
            value={nextPassword}
            onChange={(e) => setNextPassword(e.target.value)}
          />
          <button
            type="submit"
            className={`${btnPrimary} self-start`}
            disabled={!currentPassword || nextPassword.length < 8}
          >
            Change password
          </button>
        </form>
        <div className="mt-4 flex flex-col gap-2">
          <h3 className="m-0 text-[15px] font-semibold text-ink">API key</h3>
          <p className="m-0 text-[14px] text-muted">
            For scripts: send it as <code>Authorization: Bearer …</code>. A new key replaces the old one.
          </p>
          {myNewKey ? (
            <input className={inputClass} readOnly value={myNewKey} onFocus={(e) => e.target.select()} />
          ) : (
            <button type="button" className={`${btnDanger} self-start`} onClick={rotateMine}>
              Generate new API key
            </button>
          )}
          {myNewKey && <p className="m-0 text-[13px] text-muted">Shown once. Copy it now.</p>}
        </div>
        {accountNotice && <p className="mt-3 mb-0 text-[14px] text-ink">{accountNotice}</p>}
        {accountError && <p className="mt-3 mb-0 text-[14px] text-danger">{accountError}</p>}
      </Modal>
    </div>
  );
}
