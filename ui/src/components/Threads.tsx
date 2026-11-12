import { shortModelName, type ThreadSummary } from "../api";
import { maybeRedact, useRedactGeneration } from "../redact";

interface Props {
  threads: ThreadSummary[];
  /** Signed-in username; threads of anyone else are listed read-only under their owner. */
  me: string;
  activeId: string;
  /** Owner of the open thread; null when it is your own. */
  activeOwner: string | null;
  onSelect: (id: string, owner: string | null) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
}

function when(ts: number): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  return d.toDateString() === today.toDateString()
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString();
}

export default function Threads({ threads, me, activeId, activeOwner, onSelect, onNew, onDelete }: Props) {
  useRedactGeneration();
  const mine = threads.filter((t) => t.owner === me);
  const others = new Map<string, ThreadSummary[]>();
  for (const t of threads) {
    if (t.owner === me) continue;
    others.set(t.owner, [...(others.get(t.owner) ?? []), t]);
  }

  const row = (t: ThreadSummary, own: boolean) => {
    const active = t.id === activeId && (own ? activeOwner === null : activeOwner === t.owner);
    return (
      <div
        key={`${t.owner}/${t.id}`}
        className={`thread-item ${active ? "active" : ""}`}
        onClick={() => onSelect(t.id, own ? null : t.owner)}
        title={maybeRedact(t.title)}
      >
        <div className="thread-item-main">
          <span className="thread-title">{maybeRedact(t.title) || "(untitled)"}</span>
          <span className="thread-meta">
            <span className="thread-when">{when(t.updated_at)}</span>
            {t.model && (
              <span className="model-tag" title={t.model}>
                {shortModelName(t.model)}
              </span>
            )}
          </span>
        </div>
        {own && (
          <button
            className="thread-delete"
            title="Delete conversation"
            onClick={(e) => {
              e.stopPropagation();
              onDelete(t.id);
            }}
          >
            ×
          </button>
        )}
      </div>
    );
  };

  return (
    <aside className="sidebar">
      <button className="new-thread" onClick={onNew}>
        + New conversation
      </button>
      <div className="thread-list">
        {others.size > 0 && <p className="thread-section">Mine</p>}
        {mine.map((t) => row(t, true))}
        {mine.length === 0 && <p className="hint">Past conversations appear here.</p>}
        {[...others.entries()].map(([owner, list]) => (
          <div key={owner}>
            <p className="thread-section">
              <span className="thread-owner">{owner}</span> · read-only
            </p>
            {list.map((t) => row(t, false))}
          </div>
        ))}
      </div>
    </aside>
  );
}
