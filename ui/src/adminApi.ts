import { API_URL } from "./api";

const TOKEN_KEY = "bizharness-admin-token";

export function getAdminToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function setAdminToken(token: string): void {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
}

export class AdminHttpError extends Error {
  status: number;
  problems?: string[];
  constructor(message: string, status: number, problems?: string[]) {
    super(message);
    this.status = status;
    this.problems = problems;
  }
}

async function adminFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getAdminToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const res = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    let detail = `HTTP ${res.status}`;
    let problems: string[] | undefined;
    if (body && typeof body === "object") {
      if (typeof (body as { detail?: unknown }).detail === "string") {
        detail = (body as { detail: string }).detail;
      }
      if (Array.isArray((body as { problems?: unknown }).problems)) {
        problems = (body as { problems: string[] }).problems;
      }
    }
    throw new AdminHttpError(detail, res.status, problems);
  }
  return res;
}

export interface EnvStatus {
  name: string;
  set: boolean;
  required: boolean;
  source: string;
}

export interface AdminSkill {
  name: string;
  /** How the engine loads it: always in the prompt, on demand, or on disk only. */
  load: "inline" | "deferred" | "unused";
}

export interface AdminToolParam {
  name: string;
  type?: string;
  optional?: boolean;
  default?: unknown;
}

export interface AdminToolEntry {
  name: string;
  params?: AdminToolParam[];
  returns?: string;
  signature?: string;
}

export interface AdminTool {
  name: string;
  functions: number;
  helper?: boolean;
  names?: string[];
  entries?: AdminToolEntry[];
  error?: string;
}

export interface AdminProfile {
  id: string;
  name?: string;
  description?: string;
  model?: string;
  code_tool?: string;
  hash: string;
  reload_key: string;
  toml: string;
  instructions_preview?: string;
  instructions_chars?: number;
  tools: AdminTool[];
  skills: AdminSkill[];
  env: EnvStatus[];
}

function tomlStringList(toml: string, key: string): string[] {
  const section = toml.split(/^\[/m).find((block) => block.startsWith("skills]"));
  const src = section ?? toml;
  const m = src.match(new RegExp(`${key}\\s*=\\s*\\[([\\s\\S]*?)\\]`));
  if (!m) return [];
  return [...m[1].matchAll(/["']([^"']+)["']/g)].map((x) => x[1]);
}

function skillLoad(toml: string, name: string): AdminSkill["load"] {
  if (tomlStringList(toml, "inline").includes(name)) return "inline";
  if (tomlStringList(toml, "deferred").includes(name)) return "deferred";
  return "unused";
}

function asSkills(raw: unknown, toml: string): AdminSkill[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item): AdminSkill | null => {
      const name = typeof item === "string" ? item : String((item as { name?: string }).name ?? "");
      if (!name) return null;
      const fromToml = skillLoad(toml, name);
      if (fromToml !== "unused") return { name, load: fromToml };
      const hinted = typeof item === "object" && item && "load" in item ? (item as { load: unknown }).load : undefined;
      if (hinted === "inline" || hinted === "deferred" || hinted === "unused") {
        return { name, load: hinted };
      }
      return { name, load: "unused" };
    })
    .filter((s): s is AdminSkill => s !== null);
}

function tomlQuoted(toml: string, section: string, key: string): string {
  const block = toml.split(/^\[/m).find((b) => b.startsWith(`${section}]`));
  if (!block) return "";
  const m = block.match(new RegExp(`^${key}\\s*=\\s*"([^"]*)"`, "m"));
  return m?.[1] ?? "";
}

export function clipPreview(text: string, n = 240): string {
  const collapsed = text.split(/\s+/).join(" ").trim();
  if (collapsed.length <= n) return collapsed;
  const cut = collapsed.slice(0, n + 1);
  const space = cut.lastIndexOf(" ");
  const snippet = space > n / 2 ? cut.slice(0, space) : collapsed.slice(0, n);
  return snippet.replace(/[.,;:—-]+$/, "") + "…";
}

function asEntries(raw: unknown, names: string[]): AdminToolEntry[] {
  if (Array.isArray(raw) && raw.length) {
    return raw
      .map((item): AdminToolEntry | null => {
        if (typeof item === "string") return { name: item };
        const name = String((item as { name?: string }).name ?? "");
        if (!name) return null;
        const params = Array.isArray((item as { params?: unknown }).params)
          ? ((item as { params: AdminToolParam[] }).params ?? [])
          : [];
        const returns =
          typeof (item as { returns?: unknown }).returns === "string"
            ? (item as { returns: string }).returns
            : "";
        const signature =
          typeof (item as { signature?: unknown }).signature === "string"
            ? (item as { signature: string }).signature
            : undefined;
        return { name, params, returns, signature };
      })
      .filter((e): e is AdminToolEntry => e !== null);
  }
  return names.map((name) => ({ name }));
}

function asTools(raw: unknown): AdminTool[] {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((item): AdminTool | null => {
      if (typeof item === "string") return { name: item, functions: 0, names: [], entries: [] };
      const name = String((item as { name?: string }).name ?? "");
      if (!name) return null;
      const functions = Number((item as { functions?: unknown }).functions) || 0;
      const names = Array.isArray((item as { names?: unknown }).names)
        ? (item as { names: string[] }).names
        : [];
      return {
        name,
        functions,
        helper: Boolean((item as { helper?: unknown }).helper),
        names,
        entries: asEntries((item as { entries?: unknown }).entries, names),
        error: typeof (item as { error?: unknown }).error === "string" ? (item as { error: string }).error : undefined,
      };
    })
    .filter((t): t is AdminTool => t !== null);
}

export async function fetchAdminProfile(): Promise<AdminProfile> {
  const res = await adminFetch("/admin/profile");
  const data = (await res.json()) as AdminProfile;
  const toml = data.toml ?? "";
  let preview = data.instructions_preview ?? "";
  let chars = data.instructions_chars;
  if (!preview) {
    try {
      const text = await fetchAdminText("/admin/profile/instructions.md");
      preview = clipPreview(text);
      chars = text.length;
    } catch {
      preview = "";
    }
  }
  return {
    ...data,
    name: data.name || tomlQuoted(toml, "profile", "name"),
    description: data.description || tomlQuoted(toml, "profile", "description"),
    model: data.model || tomlQuoted(toml, "model", "spec"),
    code_tool: data.code_tool || tomlQuoted(toml, "code_tool", "name"),
    instructions_preview: preview,
    instructions_chars: chars,
    tools: asTools(data.tools),
    skills: asSkills(data.skills, toml),
  };
}

export async function fetchAdminText(path: string): Promise<string> {
  const res = await adminFetch(path);
  return await res.text();
}

export async function renderAdminInstructions(text: string): Promise<string> {
  const res = await adminFetch("/admin/profile/render-instructions", {
    method: "POST",
    headers: { "Content-Type": "text/plain; charset=utf-8" },
    body: text,
  });
  return await res.text();
}

export async function putAdminText(path: string, text: string): Promise<{ ok: boolean; hash?: string }> {
  const res = await adminFetch(path, {
    method: "PUT",
    headers: { "Content-Type": "text/plain; charset=utf-8" },
    body: text,
  });
  return (await res.json()) as { ok: boolean; hash?: string };
}

export async function reloadAdminProfile(): Promise<{ ok: boolean; hash: string }> {
  const res = await adminFetch("/admin/profile/reload", { method: "POST" });
  return (await res.json()) as { ok: boolean; hash: string };
}

export async function deleteAdminTool(name: string): Promise<void> {
  await adminFetch(`/admin/profile/tools/${encodeURIComponent(name)}`, { method: "DELETE" });
}

export async function setAdminEnv(name: string, value: string): Promise<void> {
  await adminFetch(`/admin/profile/env/${encodeURIComponent(name)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  });
}

export async function deleteAdminEnv(name: string): Promise<void> {
  await adminFetch(`/admin/profile/env/${encodeURIComponent(name)}`, { method: "DELETE" });
}
