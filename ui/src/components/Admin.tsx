import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import {
  AdminHttpError,
  deleteAdminEnv,
  deleteAdminTool,
  fetchAdminProfile,
  fetchAdminText,
  getAdminToken,
  renderAdminInstructions,
  putAdminText,
  reloadAdminProfile,
  setAdminEnv,
  setAdminToken,
  type AdminProfile,
  type AdminSkill,
  type AdminTool,
  type AdminToolEntry,
  type EnvStatus,
} from "../adminApi";
import HighlightedEditor from "./HighlightedEditor";
import Markdown from "./Markdown";
import Modal, { btnDanger, btnGhost, btnPrimary, inputClass, SecretInput } from "./Modal";
import { highlightCode } from "./CodeBlock";

type Nav =
  | { kind: "overview" }
  | { kind: "env" }
  | { kind: "toml" }
  | { kind: "instructions" }
  | { kind: "tool"; name: string }
  | { kind: "skill"; name: string };

type Dialog =
  | { kind: "discard"; next: Nav }
  | { kind: "new-tool" }
  | { kind: "new-skill" }
  | { kind: "delete-tool"; name: string }
  | { kind: "delete-env"; name: string }
  | { kind: "set-env"; name: string }
  | null;

function navKey(n: Nav): string {
  if (n.kind === "tool") return `tool:${n.name}`;
  if (n.kind === "skill") return `skill:${n.name}`;
  return n.kind;
}

function filePath(n: Nav): string | null {
  if (n.kind === "toml") return "/admin/profile/profile.toml";
  if (n.kind === "instructions") return "/admin/profile/instructions.md";
  if (n.kind === "tool") return `/admin/profile/tools/${encodeURIComponent(n.name)}`;
  if (n.kind === "skill") return `/admin/profile/skills/${encodeURIComponent(n.name)}/SKILL.md`;
  return null;
}

function fileLabel(n: Nav): string {
  if (n.kind === "toml") return "profile.toml";
  if (n.kind === "instructions") return "instructions.md";
  if (n.kind === "tool") return `tools/${n.name}`;
  if (n.kind === "skill") return `skills/${n.name}/SKILL.md`;
  return "";
}

function editorLang(n: Nav): "python" | "toml" | "markdown" | null {
  if (n.kind === "toml") return "toml";
  if (n.kind === "tool") return "python";
  if (n.kind === "instructions" || n.kind === "skill") return "markdown";
  return null;
}

function errText(e: unknown): string {
  if (e instanceof AdminHttpError) return e.message;
  if (e instanceof Error) return e.message;
  return String(e);
}

const TOOL_TEMPLATE = `from pydantic_ai import FunctionToolset


def register(ctx):
    ts = FunctionToolset(max_retries=1)
    return ts
`;

const SKILL_TEMPLATE = `# New skill

Describe when the agent should load this skill, and the mechanics it needs.
`;

const FUNCTION_PREVIEW = 10;

function registeredFunctions(tools: AdminTool[]): (AdminToolEntry & { file: string })[] {
  const out: (AdminToolEntry & { file: string })[] = [];
  for (const tool of tools) {
    const entries =
      tool.entries && tool.entries.length
        ? tool.entries
        : (tool.names ?? []).map((name) => ({ name }));
    for (const entry of entries) out.push({ ...entry, file: tool.name });
  }
  return out;
}

function functionCount(tools: AdminTool[]): number {
  return tools.reduce((n, tool) => n + tool.functions, 0);
}

function splitSignature(fn: AdminToolEntry): { args: string; returns: string } {
  const signature = fn.signature ?? "";
  let args = "()";
  let returns = fn.returns ?? "";
  if (signature) {
    const body = signature.startsWith(fn.name) ? signature.slice(fn.name.length).trim() : signature;
    const idx = body.lastIndexOf(" -> ");
    if (idx >= 0) {
      args = body.slice(0, idx).trim() || "()";
      returns = body.slice(idx + 4).trim() || returns;
    } else if (body.startsWith("(")) {
      args = body;
    }
  } else if (fn.params?.length) {
    args = `(${fn.params
      .map((p) => {
        let s = p.type ? `${p.name}: ${p.type}` : p.name;
        if (p.optional) {
          const d = p.default === null || p.default === undefined ? "None" : String(p.default);
          s += ` = ${d}`;
        }
        return s;
      })
      .join(", ")})`;
  }
  return { args, returns };
}

function PyTokens({ code, className }: { code: string; className?: string }) {
  let html = highlightCode(code, "python");
  if (!html) html = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  if (!html.includes("token") && /^[A-Z][A-Za-z0-9_]*$/.test(code)) {
    html = `<span class="token class-name">${html}</span>`;
  }
  return (
    <span
      className={`language-python ${className ?? ""}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function splitExt(name: string): { stem: string; ext: string } {
  const i = name.lastIndexOf(".");
  if (i <= 0) return { stem: name, ext: "" };
  return { stem: name.slice(0, i), ext: name.slice(i) };
}

function OverviewCard({
  title,
  meta,
  onOpen,
  children,
  warn,
}: {
  title: string;
  meta?: string;
  onOpen: () => void;
  children?: ReactNode;
  warn?: boolean;
}) {
  return (
    <div
      className={`flex min-h-0 flex-col rounded-[10px] border ${
        warn ? "border-danger-border bg-danger-soft/40" : "border-line bg-accent-soft-2/70"
      }`}
    >
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full items-baseline justify-between gap-2 rounded-t-[10px] bg-transparent px-4 py-3 text-left font-normal text-ink hover:bg-accent-soft hover:text-ink"
      >
        <span className="text-[12px] font-semibold tracking-wide text-muted uppercase">{title}</span>
        {meta && <span className="text-[13px] font-medium text-ink">{meta}</span>}
      </button>
      {children && <div className="border-t border-line px-4 py-3">{children}</div>}
    </div>
  );
}

function Chip({
  onClick,
  children,
  title,
}: {
  onClick: () => void;
  children: ReactNode;
  title?: string;
}) {
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-line bg-pane px-2 py-1 text-left font-normal text-ink hover:border-accent hover:bg-accent-soft hover:text-ink"
    >
      {children}
    </button>
  );
}

function NavItem({
  active,
  onClick,
  children,
  badge,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
  badge?: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-1.5 rounded-lg border px-3 py-2 text-left text-[15px] font-normal ${
        active
          ? "border-line bg-pane text-ink shadow-[inset_3px_0_0_#0064fc]"
          : "border-transparent bg-transparent text-ink hover:border-line hover:bg-pane hover:text-ink"
      }`}
    >
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {badge}
    </button>
  );
}

function SkillLoadPill({ load }: { load: AdminSkill["load"] }) {
  const label = load === "inline" ? "inline" : load === "deferred" ? "deferred" : "unused";
  const title =
    load === "inline"
      ? "Always inlined into the system prompt"
      : load === "deferred"
        ? "Loaded on demand with load_capability"
        : "On disk, not listed in profile.toml [skills]";
  const cls =
    load === "inline"
      ? "bg-accent-soft text-accent"
      : load === "deferred"
        ? "bg-[#f5eedc] text-[#8a6a1a]"
        : "bg-danger-soft text-danger";
  return (
    <span
      title={title}
      className={`shrink-0 rounded-full px-1.5 py-0.5 text-[10px] font-semibold tracking-wide uppercase ${cls}`}
    >
      {label}
    </span>
  );
}

function ToolFnPill({ tool }: { tool: AdminTool }) {
  if (tool.error) {
    return (
      <span title={tool.error} className="shrink-0 rounded-full bg-danger-soft px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-danger uppercase">
        error
      </span>
    );
  }
  if (tool.helper) {
    return (
      <span title="Shared helper — no register()" className="shrink-0 rounded-full bg-[#f5eedc] px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-[#8a6a1a] uppercase">
        helper
      </span>
    );
  }
  const label = String(tool.functions);
  return (
    <span
      title={(tool.entries ?? []).map((e) => e.signature || e.name).join("\n") || `${tool.functions} functions`}
      className="shrink-0 rounded-full bg-accent-soft px-1.5 py-0.5 font-mono text-[10px] font-semibold text-accent"
    >
      {label}
    </span>
  );
}

function ToolsOverview({
  tools,
  onOpen,
}: {
  tools: AdminTool[];
  onOpen: (name: string) => void;
}) {
  const fns = registeredFunctions(tools);
  const shown = fns.slice(0, FUNCTION_PREVIEW);
  const rest = fns.length - shown.length;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-1.5">
        {tools.map((tool) => (
          <Chip
            key={tool.name}
            onClick={() => onOpen(tool.name)}
            title={tool.error || (tool.entries ?? []).map((e) => e.signature || e.name).join("\n")}
          >
            <span className="truncate font-mono text-[12px]">{tool.name}</span>
            <ToolFnPill tool={tool} />
          </Chip>
        ))}
      </div>
      {shown.length > 0 && (
        <div>
          <p className="mt-0 mb-2 text-[11px] font-semibold tracking-wide text-muted uppercase">
            Functions
          </p>
          <ol className="m-0 grid list-none grid-cols-1 gap-0.5 rounded-md border border-line bg-code p-1.5">
            {shown.map((fn, i) => {
              const { args, returns } = splitSignature(fn);
              return (
                <li key={`${fn.file}:${fn.name}`} className="min-w-0">
                  <button
                    type="button"
                    title={fn.signature || `${fn.name} · ${fn.file}`}
                    onClick={() => onOpen(fn.file)}
                    className="flex w-full min-w-0 flex-col gap-0.5 rounded-md border border-transparent bg-transparent px-1.5 py-1 text-left font-normal text-ink hover:border-line hover:bg-pane hover:text-ink"
                  >
                    <span className="flex min-w-0 items-baseline gap-2">
                      <span className="w-4 shrink-0 text-right font-mono text-[11px] text-muted tabular-nums">
                        {i + 1}
                      </span>
                      <code className="min-w-0 truncate font-mono text-[12px] leading-snug font-normal">
                        <span className="token function">{fn.name}</span>
                      </code>
                      {returns ? (
                        <code className="ml-auto shrink-0 font-mono text-[12px] leading-snug font-normal">
                          <span className="token operator">→</span>{" "}
                          <PyTokens code={returns} />
                        </code>
                      ) : null}
                    </span>
                    <code className="block min-w-0 truncate pl-6 font-mono text-[12px] leading-snug font-normal">
                      <PyTokens code={args} />
                    </code>
                  </button>
                </li>
              );
            })}
          </ol>
          {rest > 0 && <p className="mt-1.5 mb-0 pl-6 text-[12px] text-muted">+{rest} more</p>}
        </div>
      )}
    </div>
  );
}

export default function Admin() {
  const [tokenInput, setTokenInput] = useState("");
  const [gate, setGate] = useState<"locked" | "disabled" | "open">(
    getAdminToken() ? "open" : "locked",
  );
  const [profile, setProfile] = useState<AdminProfile | null>(null);
  const [nav, setNav] = useState<Nav>({ kind: "overview" });
  const [editor, setEditor] = useState("");
  const [saved, setSaved] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [problems, setProblems] = useState<string[]>([]);
  const [mdMode, setMdMode] = useState<"source" | "preview" | "rendered">("preview");
  const [rendered, setRendered] = useState("");
  const [renderProblems, setRenderProblems] = useState<string[]>([]);
  const [rendering, setRendering] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [formName, setFormName] = useState("");
  const [formSecret, setFormSecret] = useState("");
  const [formError, setFormError] = useState<string | null>(null);

  const dirty = filePath(nav) !== null && editor !== saved;
  const lang = editorLang(nav);

  const loadProfile = useCallback(async () => {
    const p = await fetchAdminProfile();
    setProfile(p);
    setGate("open");
    return p;
  }, []);

  const closeDialog = () => {
    setDialog(null);
    setFormName("");
    setFormSecret("");
    setFormError(null);
  };

  const unlock = async (e: FormEvent) => {
    e.preventDefault();
    setAdminToken(tokenInput.trim());
    setNotice(null);
    setProblems([]);
    try {
      await loadProfile();
      setTokenInput("");
    } catch (err) {
      setAdminToken("");
      if (err instanceof AdminHttpError && err.status === 404) {
        setGate("disabled");
        setNotice("Admin API is disabled. Set HARNESS_ADMIN_TOKEN on the server.");
      } else if (err instanceof AdminHttpError && err.status === 401) {
        setGate("locked");
        setNotice("That token was rejected.");
      } else {
        setGate("locked");
        setNotice(errText(err));
      }
    }
  };

  const lock = () => {
    setAdminToken("");
    setProfile(null);
    setGate("locked");
    setNotice(null);
    setProblems([]);
    setEditor("");
    setSaved("");
    setNav({ kind: "overview" });
    closeDialog();
  };

  useEffect(() => {
    if (gate !== "open" || !getAdminToken()) return;
    loadProfile().catch((err) => {
      if (err instanceof AdminHttpError && err.status === 404) {
        setGate("disabled");
        setNotice("Admin API is disabled. Set HARNESS_ADMIN_TOKEN on the server.");
      } else if (err instanceof AdminHttpError && err.status === 401) {
        setAdminToken("");
        setGate("locked");
        setNotice("Admin token expired or changed. Unlock again.");
      } else {
        setGate("locked");
        setNotice(errText(err));
      }
    });
  }, [gate, loadProfile]);

  useEffect(() => {
    const path = filePath(nav);
    if (!path || gate !== "open") {
      setEditor("");
      setSaved("");
      return;
    }
    let cancelled = false;
    setLoading(true);
    setProblems([]);
    setRendered("");
    setRenderProblems([]);
    if (nav.kind === "instructions") setMdMode("rendered");
    else if (editorLang(nav) === "markdown") setMdMode("preview");
    fetchAdminText(path)
      .then((text) => {
        if (cancelled) return;
        setEditor(text);
        setSaved(text);
      })
      .catch((err) => {
        if (!cancelled) setNotice(errText(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nav, gate]);

  useEffect(() => {
    if (nav.kind !== "instructions" || mdMode !== "rendered" || gate !== "open" || loading) return;
    let cancelled = false;
    setRendering(true);
    renderAdminInstructions(editor)
      .then((text) => {
        if (cancelled) return;
        setRendered(text);
        setRenderProblems([]);
      })
      .catch((err) => {
        if (cancelled) return;
        if (err instanceof AdminHttpError && err.problems?.length) {
          setRenderProblems(err.problems);
          setRendered("");
        } else {
          setNotice(errText(err));
        }
      })
      .finally(() => {
        if (!cancelled) setRendering(false);
      });
    return () => {
      cancelled = true;
    };
  }, [nav.kind, mdMode, editor, gate, loading]);

  const applyNav = (next: Nav) => {
    setNotice(null);
    setProblems([]);
    setNav(next);
  };

  const go = (next: Nav) => {
    if (dirty) {
      setDialog({ kind: "discard", next });
      return;
    }
    applyNav(next);
  };

  const saveFile = useCallback(async () => {
    const path = filePath(nav);
    if (!path) return;
    setSaving(true);
    setNotice(null);
    setProblems([]);
    try {
      await putAdminText(path, editor);
      setSaved(editor);
      await loadProfile();
      setNotice("Saved. New conversations pick this up; in-flight runs keep the previous profile.");
    } catch (err) {
      if (err instanceof AdminHttpError && err.problems?.length) {
        setProblems(err.problems);
        setNotice("Validation failed — nothing was written.");
      } else {
        setNotice(errText(err));
      }
    } finally {
      setSaving(false);
    }
  }, [editor, loadProfile, nav]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        if (!filePath(nav) || editor === saved) return;
        e.preventDefault();
        saveFile();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editor, nav, saveFile, saved]);

  const reload = async () => {
    setSaving(true);
    setNotice(null);
    try {
      await reloadAdminProfile();
      await loadProfile();
      setNotice("Reloaded from disk. Cached agents were dropped.");
    } catch (err) {
      setNotice(errText(err));
    } finally {
      setSaving(false);
    }
  };

  const submitNewTool = async (e: FormEvent) => {
    e.preventDefault();
    const raw = formName.trim();
    const name = raw.endsWith(".py") ? raw : `${raw}.py`;
    if (!raw || !/^[A-Za-z0-9_][A-Za-z0-9_.-]*\.py$/.test(name) || name.includes("/")) {
      setFormError("Use a Python module name like gamma_tools.py.");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await putAdminText(`/admin/profile/tools/${encodeURIComponent(name)}`, TOOL_TEMPLATE);
      await loadProfile();
      closeDialog();
      applyNav({ kind: "tool", name });
    } catch (err) {
      setFormError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  const submitNewSkill = async (e: FormEvent) => {
    e.preventDefault();
    const name = formName.trim();
    if (!/^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(name)) {
      setFormError("Use letters, numbers, hyphens or underscores.");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await putAdminText(
        `/admin/profile/skills/${encodeURIComponent(name)}/SKILL.md`,
        SKILL_TEMPLATE,
      );
      await loadProfile();
      closeDialog();
      applyNav({ kind: "skill", name });
    } catch (err) {
      setFormError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  const confirmDeleteTool = async () => {
    if (dialog?.kind !== "delete-tool") return;
    const { name } = dialog;
    setSaving(true);
    try {
      await deleteAdminTool(name);
      await loadProfile();
      closeDialog();
      if (nav.kind === "tool" && nav.name === name) applyNav({ kind: "overview" });
    } catch (err) {
      setFormError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  const submitEnv = async (e: FormEvent) => {
    e.preventDefault();
    if (dialog?.kind !== "set-env") return;
    if (!formSecret) {
      setFormError("Enter a value.");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      await setAdminEnv(dialog.name, formSecret);
      await loadProfile();
      closeDialog();
      setNotice(`${dialog.name} written to secrets.env (value not shown).`);
    } catch (err) {
      setFormError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  const confirmDeleteEnv = async () => {
    if (dialog?.kind !== "delete-env") return;
    setSaving(true);
    try {
      await deleteAdminEnv(dialog.name);
      await loadProfile();
      closeDialog();
    } catch (err) {
      setFormError(errText(err));
    } finally {
      setSaving(false);
    }
  };

  if (gate !== "open" || !profile) {
    return (
      <div className="flex h-full items-center justify-center p-8">
        <div className="w-full max-w-[420px] rounded-[10px] border border-line bg-pane p-6 shadow-[0_1px_2px_rgba(24,38,32,0.05),0_2px_8px_rgba(24,38,32,0.04)]">
          <h2 className="mb-1 text-[19px] font-semibold text-ink">Admin</h2>
          {gate === "open" ? (
            <p className="mt-2 text-[15px] text-muted">Loading profile…</p>
          ) : gate === "disabled" ? (
            <p className="mt-2 text-[15px] text-muted">
              The admin API is off. Start the server with <code>HARNESS_ADMIN_TOKEN</code> set.
            </p>
          ) : (
            <form className="mt-3" onSubmit={unlock}>
              <p className="mb-3 text-[15px] leading-relaxed text-muted">
                Enter the same token as <code>HARNESS_ADMIN_TOKEN</code>. It stays in this tab
                only.
              </p>
              <SecretInput
                value={tokenInput}
                onChange={setTokenInput}
                placeholder="Admin token"
              />
              <button type="submit" className={`${btnPrimary} mt-3 w-full`} disabled={!tokenInput.trim()}>
                Unlock
              </button>
            </form>
          )}
          {notice && <p className="mt-3 text-[14px] text-danger">{notice}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="grid h-full min-h-0 min-w-0 grid-cols-[260px_1fr]">
      <aside className="flex min-h-0 flex-col gap-1 overflow-y-auto border-r border-line bg-bg p-3">
        <NavItem active={nav.kind === "overview"} onClick={() => go({ kind: "overview" })}>
          Overview
        </NavItem>
        <NavItem active={nav.kind === "env"} onClick={() => go({ kind: "env" })}>
          Environment
        </NavItem>
        <p className="mt-3 mb-0.5 px-1 text-[11px] font-semibold tracking-wide text-muted uppercase">
          Files
        </p>
        <NavItem active={nav.kind === "toml"} onClick={() => go({ kind: "toml" })} badge={<span className="shrink-0 font-mono text-xs text-muted">.toml</span>}>
          profile
        </NavItem>
        <NavItem active={nav.kind === "instructions"} onClick={() => go({ kind: "instructions" })} badge={<span className="shrink-0 font-mono text-xs text-muted">.md</span>}>
          instructions
        </NavItem>
        <p className="mt-3 mb-0.5 px-1 text-[11px] font-semibold tracking-wide text-muted uppercase">
          Tools
        </p>
        {profile.tools.map((tool) => {
          const { stem } = splitExt(tool.name);
          return (
            <div key={tool.name} className="group flex items-center gap-0.5">
              <NavItem
                active={navKey(nav) === `tool:${tool.name}`}
                onClick={() => go({ kind: "tool", name: tool.name })}
                badge={<ToolFnPill tool={tool} />}
              >
                {stem}
              </NavItem>
              <button
                type="button"
                title={`Delete ${tool.name}`}
                className={`${btnGhost} hidden h-8 w-8 shrink-0 px-0 py-0 text-lg leading-none group-hover:inline-flex hover:border-danger-border hover:bg-danger-soft hover:text-danger`}
                onClick={() => setDialog({ kind: "delete-tool", name: tool.name })}
              >
                ×
              </button>
            </div>
          );
        })}
        <button
          type="button"
          className={`${btnGhost} mt-1 w-full`}
          disabled={saving}
          onClick={() => {
            setFormName("");
            setFormError(null);
            setDialog({ kind: "new-tool" });
          }}
        >
          + New tool
        </button>
        <p className="mt-3 mb-0.5 px-1 text-[11px] font-semibold tracking-wide text-muted uppercase">
          Skills
        </p>
        {profile.skills.map((skill) => (
          <NavItem
            key={skill.name}
            active={navKey(nav) === `skill:${skill.name}`}
            onClick={() => go({ kind: "skill", name: skill.name })}
            badge={<SkillLoadPill load={skill.load} />}
          >
            {skill.name}
          </NavItem>
        ))}
        <button
          type="button"
          className={`${btnGhost} mt-1 w-full`}
          disabled={saving}
          onClick={() => {
            setFormName("");
            setFormError(null);
            setDialog({ kind: "new-skill" });
          }}
        >
          + New skill
        </button>
        <div className="mt-auto pt-3">
          <button type="button" className={`${btnGhost} w-full`} onClick={lock}>
            Lock
          </button>
        </div>
      </aside>

      <section className="flex min-h-0 min-w-0 flex-col gap-3 bg-pane p-4">
        {notice && (
          <div
            className={`flex items-start justify-between gap-3 rounded-[10px] border px-3 py-2 text-[14px] ${
              problems.length ? "border-danger-border bg-danger-soft text-danger" : "border-line bg-accent-soft text-ink"
            }`}
          >
            <p className="m-0">{notice}</p>
            <button type="button" className={`${btnGhost} shrink-0 px-2 py-0.5`} onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        )}
        {problems.length > 0 && (
          <ul className="m-0 rounded-[10px] border border-danger-border bg-danger-soft py-2 pr-3 pl-7 text-[14px] text-danger">
            {problems.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        )}

        {nav.kind === "overview" && (
          <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-auto">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="m-0 text-[20px] font-semibold text-ink">
                  {profile.name || profile.id}
                </h2>
                {profile.description && (
                  <p className="mt-1 mb-0 text-[14px] leading-relaxed text-muted">{profile.description}</p>
                )}
                <p className="mt-1 mb-0 font-mono text-[12px] text-muted">
                  {profile.id} · {profile.hash.slice(0, 12)}
                </p>
              </div>
              <button type="button" className={btnPrimary} onClick={reload} disabled={saving}>
                Reload from disk
              </button>
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <OverviewCard title="Config" meta="profile.toml" onOpen={() => go({ kind: "toml" })}>
                <dl className="m-0 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[14px]">
                  <dt className="text-muted">Model</dt>
                  <dd className="m-0 truncate font-mono text-[13px]" title={profile.model}>
                    {(profile.model ?? "—").replace(/^[^:]+:/, "")}
                  </dd>
                  <dt className="text-muted">Code tool</dt>
                  <dd className="m-0 truncate font-mono text-[13px]">{profile.code_tool || "—"}</dd>
                </dl>
              </OverviewCard>
              <OverviewCard
                title="Instructions"
                meta={
                  profile.instructions_chars
                    ? `${profile.instructions_chars.toLocaleString()} chars`
                    : "prompt"
                }
                onOpen={() => go({ kind: "instructions" })}
              >
                <p className="m-0 text-[14px] leading-relaxed text-ink">
                  {profile.instructions_preview || "Empty prompt."}
                </p>
              </OverviewCard>
              <OverviewCard
                title="Environment"
                meta={`${profile.env.filter((e) => e.set).length}/${profile.env.length} set`}
                onOpen={() => go({ kind: "env" })}
                warn={profile.env.some((e) => e.required && !e.set)}
              >
                {profile.env.length === 0 ? (
                  <p className="m-0 text-[14px] text-muted">No names in [env].</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {profile.env.map((row) => (
                      <Chip key={row.name} onClick={() => go({ kind: "env" })} title={row.source}>
                        <span className="font-mono text-[12px]">{row.name}</span>
                        <span
                          className={`rounded-full px-1.5 text-[10px] font-semibold ${
                            row.set ? "bg-accent-soft text-accent" : "bg-danger-soft text-danger"
                          }`}
                        >
                          {row.set ? "set" : "unset"}
                        </span>
                      </Chip>
                    ))}
                  </div>
                )}
              </OverviewCard>
            </div>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
              <OverviewCard
                title="Tools"
                meta={(() => {
                  const n = functionCount(profile.tools);
                  const files = profile.tools.length;
                  if (n === 0) return `${files}`;
                  return `${n} function${n === 1 ? "" : "s"}`;
                })()}
                onOpen={() =>
                  profile.tools[0]
                    ? go({ kind: "tool", name: profile.tools[0].name })
                    : go({ kind: "toml" })
                }
              >
                {profile.tools.length === 0 ? (
                  <p className="m-0 text-[14px] text-muted">No tools/*.py files.</p>
                ) : (
                  <ToolsOverview tools={profile.tools} onOpen={(name) => go({ kind: "tool", name })} />
                )}
              </OverviewCard>
              <OverviewCard
                title="Skills"
                meta={`${profile.skills.filter((s) => s.load === "inline").length} inline · ${profile.skills.filter((s) => s.load === "deferred").length} deferred`}
                onOpen={() =>
                  profile.skills[0]
                    ? go({ kind: "skill", name: profile.skills[0].name })
                    : go({ kind: "toml" })
                }
              >
                {profile.skills.length === 0 ? (
                  <p className="m-0 text-[14px] text-muted">No skills/ directories.</p>
                ) : (
                  <div className="flex flex-wrap gap-1.5">
                    {profile.skills.map((skill) => (
                      <Chip
                        key={skill.name}
                        onClick={() => go({ kind: "skill", name: skill.name })}
                      >
                        <span className="truncate font-mono text-[12px]">{skill.name}</span>
                        <SkillLoadPill load={skill.load} />
                      </Chip>
                    ))}
                  </div>
                )}
              </OverviewCard>
            </div>

            <p className="m-0 text-[13px] leading-relaxed text-muted">
              Python in <code>tools/</code> runs with the same privileges as the engine. Saves are
              validated first; previous files go to <code>_history/</code>.
            </p>
          </div>
        )}

        {nav.kind === "env" && (
          <div className="min-h-0 overflow-auto">
            <p className="mt-0 mb-4 text-[14px] leading-relaxed text-muted">
              Values are never shown. Set writes <code>secrets.env</code> under the data root
              (mode 0600). Process environment still wins over the file.
            </p>
            <div className="flex flex-col gap-2">
              {profile.env.map((row: EnvStatus) => (
                <div
                  key={row.name}
                  className="flex flex-wrap items-center gap-3 rounded-lg border border-line px-3 py-2.5"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <code className="text-[14px]">{row.name}</code>
                      {row.required && (
                        <span className="rounded-full bg-danger-soft px-2 text-[11px] font-semibold text-danger">
                          required
                        </span>
                      )}
                      <span
                        className={`rounded-full px-2 text-[11px] font-semibold ${
                          row.set ? "bg-accent-soft text-accent" : "bg-danger-soft text-danger"
                        }`}
                      >
                        {row.set ? "set" : "unset"}
                      </span>
                      <span className="text-[13px] text-muted">{row.source.replace("_", " ")}</span>
                    </div>
                  </div>
                  <button
                    type="button"
                    className={btnPrimary}
                    disabled={saving}
                    onClick={() => {
                      setFormSecret("");
                      setFormError(null);
                      setDialog({ kind: "set-env", name: row.name });
                    }}
                  >
                    Set
                  </button>
                  {row.source === "secrets_file" && (
                    <button
                      type="button"
                      className={btnGhost}
                      disabled={saving}
                      onClick={() => setDialog({ kind: "delete-env", name: row.name })}
                    >
                      Delete
                    </button>
                  )}
                </div>
              ))}
              {profile.env.length === 0 && (
                <p className="text-[15px] text-muted">
                  No names declared in <code>[env]</code>.
                </p>
              )}
            </div>
          </div>
        )}

        {lang && (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="mb-2 flex items-center gap-3">
              <span className="min-w-0 flex-1 truncate font-mono text-[13px] text-muted">
                {fileLabel(nav)}
                {dirty && <span className="ml-2 font-sans font-semibold text-accent">unsaved</span>}
              </span>
              {nav.kind === "skill" && (
                <SkillLoadPill
                  load={profile.skills.find((s) => s.name === nav.name)?.load ?? "unused"}
                />
              )}
              {lang === "markdown" && nav.kind === "instructions" && (
                <div className="flex overflow-hidden rounded-lg border border-line">
                  <button
                    type="button"
                    className={`rounded-none px-3 py-1 text-[13px] font-medium ${
                      mdMode === "rendered"
                        ? "bg-accent-soft text-accent"
                        : "bg-pane text-muted hover:bg-accent-soft-2 hover:text-accent"
                    }`}
                    onClick={() => setMdMode("rendered")}
                  >
                    Rendered
                  </button>
                  <button
                    type="button"
                    className={`rounded-none border-l border-line px-3 py-1 text-[13px] font-medium ${
                      mdMode === "source"
                        ? "bg-accent-soft text-accent"
                        : "bg-pane text-muted hover:bg-accent-soft-2 hover:text-accent"
                    }`}
                    onClick={() => setMdMode("source")}
                  >
                    Raw
                  </button>
                </div>
              )}
              {lang === "markdown" && nav.kind === "skill" && (
                <div className="flex overflow-hidden rounded-lg border border-line">
                  <button
                    type="button"
                    className={`rounded-none px-3 py-1 text-[13px] font-medium ${
                      mdMode === "preview"
                        ? "bg-accent-soft text-accent"
                        : "bg-pane text-muted hover:bg-accent-soft-2 hover:text-accent"
                    }`}
                    onClick={() => setMdMode("preview")}
                  >
                    Preview
                  </button>
                  <button
                    type="button"
                    className={`rounded-none border-l border-line px-3 py-1 text-[13px] font-medium ${
                      mdMode === "source"
                        ? "bg-accent-soft text-accent"
                        : "bg-pane text-muted hover:bg-accent-soft-2 hover:text-accent"
                    }`}
                    onClick={() => setMdMode("source")}
                  >
                    Source
                  </button>
                </div>
              )}
              <button
                type="button"
                className={btnPrimary}
                onClick={saveFile}
                disabled={saving || loading || !dirty}
              >
                Save
              </button>
            </div>
            {lang === "markdown" && mdMode === "preview" ? (
              <div className="min-h-0 flex-1 overflow-auto rounded-[10px] border border-line bg-pane px-3 py-3">
                <Markdown className="md-body">{editor || "_Empty file._"}</Markdown>
              </div>
            ) : lang === "markdown" && mdMode === "rendered" ? (
              renderProblems.length > 0 ? (
                <ul className="m-0 rounded-[10px] border border-danger-border bg-danger-soft py-2 pr-3 pl-7 text-[14px] text-danger">
                  {renderProblems.map((problem) => (
                    <li key={problem}>{problem}</li>
                  ))}
                </ul>
              ) : (
                <div className="flex min-h-0 flex-1 flex-col gap-2">
                  <p className="m-0 text-[12px] text-muted">
                    Placeholders substituted. Inline skills are appended when a conversation starts.
                  </p>
                  <HighlightedEditor
                    value={rendering && !rendered ? "Rendering…" : rendered}
                    onChange={() => {}}
                    language="markdown"
                    wrap
                    disabled
                  />
                </div>
              )
            ) : (
              <HighlightedEditor
                value={editor}
                onChange={setEditor}
                language={lang}
                wrap={lang === "markdown"}
                disabled={loading}
              />
            )}
          </div>
        )}
      </section>

      <Modal
        open={dialog?.kind === "discard"}
        title="Unsaved changes"
        onClose={closeDialog}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={closeDialog}>
              Keep editing
            </button>
            <button
              type="button"
              className={btnDanger}
              onClick={() => {
                if (dialog?.kind === "discard") applyNav(dialog.next);
                closeDialog();
              }}
            >
              Discard
            </button>
          </>
        }
      >
        This file has edits that have not been saved.
      </Modal>

      <Modal
        open={dialog?.kind === "new-tool"}
        title="New tool"
        onClose={closeDialog}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={closeDialog}>
              Cancel
            </button>
            <button type="submit" form="admin-new-tool" className={btnPrimary} disabled={saving}>
              Create
            </button>
          </>
        }
      >
        <form id="admin-new-tool" onSubmit={submitNewTool}>
          <label className="mb-1.5 block text-sm font-medium text-muted">Filename</label>
          <input
            className={inputClass}
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            placeholder="gamma_tools.py"
          />
          {formError && <p className="mt-2 mb-0 text-[14px] text-danger">{formError}</p>}
        </form>
      </Modal>

      <Modal
        open={dialog?.kind === "new-skill"}
        title="New skill"
        onClose={closeDialog}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={closeDialog}>
              Cancel
            </button>
            <button type="submit" form="admin-new-skill" className={btnPrimary} disabled={saving}>
              Create
            </button>
          </>
        }
      >
        <form id="admin-new-skill" onSubmit={submitNewSkill}>
          <label className="mb-1.5 block text-sm font-medium text-muted">Directory name</label>
          <input
            className={inputClass}
            value={formName}
            onChange={(e) => setFormName(e.target.value)}
            placeholder="season-cashflow"
          />
          {formError && <p className="mt-2 mb-0 text-[14px] text-danger">{formError}</p>}
        </form>
      </Modal>

      <Modal
        open={dialog?.kind === "delete-tool"}
        title="Delete tool?"
        onClose={closeDialog}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={closeDialog}>
              Cancel
            </button>
            <button type="button" className={btnDanger} disabled={saving} onClick={confirmDeleteTool}>
              Delete
            </button>
          </>
        }
      >
        {dialog?.kind === "delete-tool" && (
          <p className="m-0">
            Remove <code>tools/{dialog.name}</code>? The previous file is copied to{" "}
            <code>_history/</code>.
          </p>
        )}
        {formError && <p className="mt-2 mb-0 text-[14px] text-danger">{formError}</p>}
      </Modal>

      <Modal
        open={dialog?.kind === "set-env"}
        title={dialog?.kind === "set-env" ? `Set ${dialog.name}` : "Set secret"}
        onClose={closeDialog}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={closeDialog}>
              Cancel
            </button>
            <button type="submit" form="admin-set-env" className={btnPrimary} disabled={saving}>
              Save
            </button>
          </>
        }
      >
        <form id="admin-set-env" onSubmit={submitEnv}>
          <p className="mt-0 mb-3 text-[14px] text-muted">
            Written to <code>secrets.env</code>. The value is never shown again after you save.
          </p>
          <SecretInput value={formSecret} onChange={setFormSecret} placeholder="value" />
          {formError && <p className="mt-2 mb-0 text-[14px] text-danger">{formError}</p>}
        </form>
      </Modal>

      <Modal
        open={dialog?.kind === "delete-env"}
        title="Remove secret?"
        onClose={closeDialog}
        footer={
          <>
            <button type="button" className={btnGhost} onClick={closeDialog}>
              Cancel
            </button>
            <button type="button" className={btnDanger} disabled={saving} onClick={confirmDeleteEnv}>
              Delete
            </button>
          </>
        }
      >
        {dialog?.kind === "delete-env" && (
          <p className="m-0">
            Remove <code>{dialog.name}</code> from <code>secrets.env</code>? Process environment is
            unchanged.
          </p>
        )}
        {formError && <p className="mt-2 mb-0 text-[14px] text-danger">{formError}</p>}
      </Modal>
    </div>
  );
}
