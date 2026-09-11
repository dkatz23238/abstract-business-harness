// The AG-UI client agent. Conversations are stored SERVER-side per thread
// (PUT /threads/{id} after each run, plus a server-side write at run end),
// so past conversations can be listed and reopened from any tab. Each tab
// remembers its own active thread in sessionStorage; switching threads
// re-points the same HttpAgent instance.
//
// Runs execute DETACHED on the server. Normally we stream them via the
// POST /agui response; after a page reload we re-attach to the live run via
// GET /agui/{thread}/attach. The attach stream is consumed by the same
// HttpAgent event pipeline (messages materialize identically) — we just
// swap the fetch target for the duration of that one runAgent call.

import { HttpAgent, type AgentSubscriber, type Message } from "@ag-ui/client";
import { API_URL, loadThreadMessages, saveThreadMessages } from "./api";

const THREAD_KEY = "bizharness-thread";

export function initialThreadId(): string {
  let id = sessionStorage.getItem(THREAD_KEY);
  if (!id) {
    id = crypto.randomUUID();
    sessionStorage.setItem(THREAD_KEY, id);
  }
  return id;
}

let attachTarget: string | null = null;

export const agent = new HttpAgent({
  url: `${API_URL}/agui`,
  threadId: initialThreadId(),
  fetch: (url, init) => {
    if (attachTarget) {
      return fetch(attachTarget, {
        method: "GET",
        headers: { Accept: "text/event-stream" },
        signal: init?.signal ?? null,
      });
    }
    return fetch(url, init);
  },
});

/** Re-attach to the thread's server-side run: replay buffered AG-UI events
 *  and follow live until the run ends. Throws if the stream can't be
 *  consumed (caller falls back to status polling). */
export async function attachToRun(subscriber?: AgentSubscriber): Promise<void> {
  attachTarget = `${API_URL}/agui/${agent.threadId}/attach`;
  try {
    await agent.runAgent(undefined, subscriber);
  } finally {
    attachTarget = null;
  }
}

/** Point the agent at another thread and load its stored conversation. */
export async function activateThread(threadId: string): Promise<Message[]> {
  const messages = await loadThreadMessages<Message>(threadId);
  agent.threadId = threadId;
  agent.setMessages(messages);
  sessionStorage.setItem(THREAD_KEY, threadId);
  return messages;
}

/** Persist the current conversation server-side. */
export async function persistConversation(): Promise<void> {
  await saveThreadMessages(agent.threadId, agent.messages);
}

export function freshThreadId(): string {
  const id = crypto.randomUUID();
  sessionStorage.setItem(THREAD_KEY, id);
  return id;
}
