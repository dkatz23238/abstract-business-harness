import { useCallback, useEffect, useState } from "react";
import { fetchWorkspaceFiles, rawFileUrl, type WorkspaceFile } from "../api";
import { redactEnabled, redactHtml, useRedactGeneration } from "../redact";

interface Props {
  running: boolean;
  threadId: string;
}

function prettySize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function groupOf(path: string): string {
  const top = path.split("/")[0];
  return top === "reports" || top === "data" ? top : "other";
}

function HtmlPreview({
  threadId,
  path,
  onClose,
}: {
  threadId: string;
  path: string;
  onClose: () => void;
}) {
  const redact = redactEnabled();
  const redactGen = useRedactGeneration();
  const raw = rawFileUrl(threadId, path);
  const [html, setHtml] = useState<string | null>(null);
  const [blobUrl, setBlobUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!redact) return;
    let revoked = false;
    let created: string | null = null;
    fetch(raw)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.text();
      })
      .then((text) => {
        if (revoked) return;
        const masked = redactHtml(text);
        setHtml(masked);
        created = URL.createObjectURL(new Blob([masked], { type: "text/html" }));
        setBlobUrl(created);
      })
      .catch(() => {
        if (!revoked) setHtml("<p>Could not load report.</p>");
      });
    return () => {
      revoked = true;
      if (created) URL.revokeObjectURL(created);
    };
  }, [redact, redactGen, raw]);

  const openHref = redact ? (blobUrl ?? raw) : raw;

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header>
          <span>{path}</span>
          <div>
            <a href={openHref} target="_blank" rel="noreferrer">
              Open in tab
            </a>
            <button className="ghost" onClick={onClose}>
              Close
            </button>
          </div>
        </header>
        {redact ? (
          <iframe srcDoc={html ?? ""} title={path} />
        ) : (
          <iframe src={raw} title={path} />
        )}
      </div>
    </div>
  );
}

export default function Assets({ running, threadId }: Props) {
  const [files, setFiles] = useState<WorkspaceFile[]>([]);
  const [preview, setPreview] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setFiles(await fetchWorkspaceFiles(threadId));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [threadId]);

  useEffect(() => {
    refresh();
    const interval = setInterval(refresh, running ? 5000 : 30000);
    return () => clearInterval(interval);
  }, [refresh, running]);

  const groups: Record<string, WorkspaceFile[]> = { reports: [], data: [], other: [] };
  for (const f of files) groups[groupOf(f.path)].push(f);
  for (const g of Object.values(groups)) g.sort((a, b) => b.mtime - a.mtime);

  return (
    <section className="pane assets-pane">
      <header className="pane-header">
        <span>Workspace assets</span>
        <button className="ghost" onClick={refresh}>
          Refresh
        </button>
      </header>
      <div className="assets-scroll">
        {error && <p className="hint error">{error}</p>}
        {(["reports", "data", "other"] as const).map(
          (group) =>
            groups[group].length > 0 && (
              <div key={group} className="asset-group">
                <div className="group-title">{group}</div>
                {groups[group].map((f) => (
                  <div key={f.path} className="asset-row">
                    {redactEnabled() && f.kind === "html" ? (
                      <button
                        type="button"
                        className="asset-name"
                        title={f.path}
                        onClick={() => setPreview(f.path)}
                      >
                        {f.path.split("/").slice(1).join("/") || f.path}
                      </button>
                    ) : (
                      <a
                        className="asset-name"
                        href={rawFileUrl(threadId, f.path)}
                        target="_blank"
                        rel="noreferrer"
                        title={f.path}
                      >
                        {f.path.split("/").slice(1).join("/") || f.path}
                      </a>
                    )}
                    <span className="asset-meta">
                      {prettySize(f.size)} · {new Date(f.mtime * 1000).toLocaleTimeString()}
                    </span>
                    {f.kind === "html" && (
                      <button className="ghost" onClick={() => setPreview(f.path)}>
                        Preview
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ),
        )}
        {files.length === 0 && !error && (
          <p className="hint">Files the agent saves (raw data, HTML reports) appear here.</p>
        )}
      </div>
      {preview && <HtmlPreview threadId={threadId} path={preview} onClose={() => setPreview(null)} />}
    </section>
  );
}
