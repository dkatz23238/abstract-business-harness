import { shortModelName, type ThreadSummary } from "../api";

interface Props {
  threads: ThreadSummary[];
  activeId: string;
  onSelect: (id: string) => void;
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

export default function Threads({ threads, activeId, onSelect, onNew, onDelete }: Props) {
  return (
    <aside className="sidebar">
      <button className="new-thread" onClick={onNew}>
        + New conversation
      </button>
      <div className="thread-list">
        {threads.map((t) => (
          <div
            key={t.id}
            className={`thread-item ${t.id === activeId ? "active" : ""}`}
            onClick={() => onSelect(t.id)}
            title={t.title}
          >
            <div className="thread-item-main">
              <span className="thread-title">{t.title || "(untitled)"}</span>
              <span className="thread-meta">
                <span className="thread-when">{when(t.updated_at)}</span>
                {t.model && (
                  <span className="model-tag" title={t.model}>
                    {shortModelName(t.model)}
                  </span>
                )}
              </span>
            </div>
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
          </div>
        ))}
        {threads.length === 0 && <p className="hint">Past conversations appear here.</p>}
      </div>
    </aside>
  );
}
