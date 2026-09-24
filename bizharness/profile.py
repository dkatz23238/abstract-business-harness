"""Profile loading: everything customer-specific, read from a directory.

A profile is a directory the engine knows how to read:

    profiles/<id>/
      profile.toml         model, code tool, skills, tool modules, limits, env
      instructions.md      the domain prompt (may embed {{ core.* }} blocks)
      memory_guidance.md   optional override of the engine's memory policy
      skills/<name>/SKILL.md
      tools/*.py           tool modules, each exposing register(ctx)
      ui.json              strings the web UI shows
      tests/               optional pytest files run by `harness profile test`

Nothing here imports the profile's Python; that is `harness.plugins`, which
runs only after the profile validates and its environment is resolved.

Secrets never live in the profile directory: they are resolved from the
process environment or from `<data_root>/<id>/secrets.env`, which the admin
API writes and which sits under the (gitignored) data root so a credential
cannot be committed together with the configuration.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import core_instructions
from .config import EngineSettings

# Placeholders a profile's instructions.md (and every core block) may use.
# `core.<name>` pulls in an engine block; the rest are plain strings taken
# from profile.toml.
TEMPLATE_VARS = (
    "code_tool",  # [code_tool] name — the sandbox tool the model calls
    "data_source",  # ui.data_source_name — what the model calls the source
    "data_tool_examples",  # a few real tool names, for the surfaces block
    "report_detail_hint",  # what the report's detail tables are about
    "report_extra_sections",  # extra sections every report must carry
    "spill_k",  # derived from [limits] spill_tokens
    "profile_name",
)

_VAR_DEFAULTS = {
    "data_tool_examples": "",
    "report_detail_hint": "the detail",
    "report_extra_sections": "",
}

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SECRETS_FILE = "secrets.env"


class ProfileError(Exception):
    """A profile is unusable. Carries every problem found, not just the first."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


# Reasoning effort the UI can pick per chat. `medium` is the engine default
# (and OpenAI's for GPT-5.x). `minimal` is omitted: GPT-5.6 rejects it.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh")
DEFAULT_EFFORT = "medium"


def coerce_effort(value: object | None, *, default: str = DEFAULT_EFFORT) -> str:
    """Return a valid effort level; unknown or missing values become `default`."""
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in EFFORT_LEVELS:
            return candidate
    return default


@dataclass(frozen=True)
class ModelConfig:
    spec: str = "openai:gpt-5.6-luna"
    effort: str = DEFAULT_EFFORT
    request_limit: int = 500
    retries: int = 6
    prices: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass(frozen=True)
class CodeToolConfig:
    """The sandbox tool: its name, why it exists, and its limits.

    The name matters to the model — a generic "run_code" reads as "any
    python here" and snippets drift into the restricted interpreter — so
    every profile renames it after its data source.
    """

    name: str = "data_code"
    purpose: str = ""
    wall_clock_s: float = 1800.0
    max_retries: int = 8
    mount_path: str = "/work"
    resource_limits: dict[str, int] = field(
        default_factory=lambda: {"max_duration_secs": 600, "max_memory": 1024**3}
    )


@dataclass(frozen=True)
class SkillsConfig:
    inline: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ToolsConfig:
    modules: list[str] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LimitsConfig:
    """Context hygiene, all of it engine behaviour with per-profile numbers."""

    spill_tokens: int = 30_000
    spill_tools: list[str] = field(default_factory=list)
    clamp_part_tokens: int = 20_000
    clear_tool_results_tokens: int = 150_000
    keep_pairs: int = 4
    analysis_timeout_s: float = 1800.0


@dataclass(frozen=True)
class EnvSpec:
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    defaults: dict[str, str] = field(default_factory=dict)
    # Names the shell/analysis subprocesses may see. Empty by design: model-
    # written code has no business reading an API key.
    expose_to_shell: list[str] = field(default_factory=list)

    @property
    def names(self) -> list[str]:
        seen = dict.fromkeys([*self.required, *self.optional, *self.defaults])
        return list(seen)


DEFAULT_UI = {
    "title": "Analysis Agent",
    "subtitle": "",
    "data_calls_label": "API calls",
    "data_source_name": "the data source",
    "placeholder": "Ask a question…",
    "accent": "#3b82f6",
}


@dataclass
class Profile:
    id: str
    name: str
    description: str
    path: Path
    data_dir: Path
    model: ModelConfig
    code_tool: CodeToolConfig
    skills: SkillsConfig
    tools: ToolsConfig
    limits: LimitsConfig
    env_spec: EnvSpec
    ui: dict[str, Any]
    instructions_template: str
    memory_guidance: str
    template_vars: dict[str, str]
    env: dict[str, str]
    hash: str
    raw_toml: str

    # ---------------- derived paths ----------------

    @property
    def skills_dir(self) -> Path:
        return self.path / "skills"

    @property
    def tools_dir(self) -> Path:
        return self.path / "tools"

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def threads_dir(self) -> Path:
        return self.state_dir / "threads"

    @property
    def secrets_file(self) -> Path:
        return self.data_dir / _SECRETS_FILE

    @property
    def reload_key(self) -> str:
        """Hash that also moves when a credential changes.

        `hash` identifies the configuration (it is what a run stamps into its
        history, so a report can be traced to the exact instructions and tool
        versions); the reload key additionally covers the resolved
        environment, because a rotated key means a rebuilt client.
        """
        fingerprint = hashlib.sha256(
            json.dumps(sorted(self.env.items()), ensure_ascii=False).encode()
        ).hexdigest()
        return hashlib.sha256(f"{self.hash}:{fingerprint}".encode()).hexdigest()[:16]

    # ---------------- instructions ----------------

    def resolve_instructions(self) -> str:
        """instructions.md + core blocks + the inlined skills."""
        body = self.render(self.instructions_template, where="instructions.md")
        extra = self._reference_material()
        if not extra:
            return body
        # instructions.md files usually end with a newline; strip so the
        # header's leading blank line matches the original in-source prompt
        # (one blank line before REFERENCE MATERIAL, not two).
        return body.rstrip("\n") + extra

    def render(self, template: str, *, where: str = "template") -> str:
        """Substitute this profile's variables and `{{ core.* }}` blocks.

        Used for the prompt and for any other model-facing text the engine
        parametrizes (the python_analysis description, the code tool's
        purpose line), so a profile only ever learns one syntax.
        """
        return _render(template, self.template_vars, where=where)

    def _reference_material(self) -> str:
        sections = []
        for skill in self.skills.inline:
            text = (self.skills_dir / skill / "SKILL.md").read_text()
            if text.startswith("---"):
                text = text.split("---", 2)[2]
            sections.append(text.strip())
        if not sections:
            return ""
        return core_instructions.reference_material_header(
            self.skills.deferred
        ) + "\n\n---\n\n".join(sections)

    # ---------------- environment ----------------

    def missing_env(self) -> list[str]:
        return [name for name in self.env_spec.required if not self.env.get(name)]

    def apply_env(self) -> None:
        """Publish the resolved values into os.environ.

        Tool modules are ordinary Python and most read their configuration
        from the environment; doing this before they are imported means a
        client written for a standalone script needs no changes to run inside
        a profile. One profile per process, so nothing leaks sideways.
        """
        for key, value in self.env.items():
            if value != "":
                os.environ[key] = value

    def env_status(self) -> list[dict]:
        """Per declared name: is it set and where from — never the value."""
        out = []
        file_values = _read_env_file(self.secrets_file)
        for name in self.env_spec.names:
            value = self.env.get(name, "")
            if os.environ.get(_scoped(self.id, name)) or (
                value and os.environ.get(name) == value and name not in file_values
            ):
                source = "environment"
            elif name in file_values:
                source = "secrets_file"
            elif name in self.env_spec.defaults:
                source = "default"
            else:
                source = "unset"
            out.append(
                {
                    "name": name,
                    "set": bool(value),
                    "required": name in self.env_spec.required,
                    "source": source,
                }
            )
        return out

    # ---------------- misc ----------------

    def ui_config(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "model": self.model.spec,
            "default_effort": self.model.effort,
            "effort_levels": list(EFFORT_LEVELS),
            "code_tool": self.code_tool.name,
            "profile_hash": self.hash,
            **self.ui,
        }


# ---------------------------------------------------------------------------
# loading
# ---------------------------------------------------------------------------


def _scoped(profile_id: str, name: str) -> str:
    return f"{profile_id.upper().replace('-', '_')}_{name}"


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _relocate_missing_path(value: str, profile_path: Path) -> str:
    """Point a missing absolute path at the same filename beside the profile.

    Profiles often store host paths (`/home/someone/tree/file.sqlite`). A
    container mounts that tree at one directory; the filename next to the
    profile, or in the profile itself, is the same file.
    """
    if not value.startswith("/"):
        return value
    original = Path(value)
    if original.exists():
        return value
    name = original.name
    if not name or name in (".", ".."):
        return value
    for root in (profile_path, profile_path.parent):
        found = root / name
        if found.exists():
            return str(found)
    return value


def _resolve_env(
    profile_id: str, spec: EnvSpec, secrets_file: Path, profile_path: Path
) -> dict[str, str]:
    """Process environment wins, then the profile's secrets file, then defaults.

    The scoped form (`<PROFILE_ID>_<NAME>`) is checked before the bare name
    so two profiles served from one machine can hold different credentials
    for the same variable.
    """
    file_values = _read_env_file(secrets_file)
    resolved: dict[str, str] = {}
    for name in spec.names:
        value = (
            os.environ.get(_scoped(profile_id, name))
            or os.environ.get(name)
            or file_values.get(name)
            or spec.defaults.get(name)
            or ""
        )
        resolved[name] = _relocate_missing_path(str(value), profile_path)
    return resolved


def _render(text: str, variables: dict[str, str], *, where: str) -> str:
    """Substitute {{ placeholders }}, including {{ core.* }} blocks.

    Two passes: core blocks are themselves templates. An unknown placeholder
    is an error — a silent empty section in a prompt is a bug nobody notices
    until the model misbehaves.
    """
    unknown: list[str] = []

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key.startswith("core."):
            block = core_instructions.BLOCKS.get(key.removeprefix("core."))
            if block is None:
                unknown.append(key)
                return match.group(0)
            return block
        if key in variables:
            return variables[key]
        unknown.append(key)
        return match.group(0)

    rendered = _PLACEHOLDER.sub(replace, text)
    rendered = _PLACEHOLDER.sub(replace, rendered)  # core blocks use vars too
    if unknown:
        raise ProfileError(
            [f"{where}: unknown placeholder {{{{ {key} }}}}" for key in dict.fromkeys(unknown)]
        )
    return rendered


def _hash_files(paths: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()[:16]


def config_files(path: Path) -> list[Path]:
    """Every file whose content defines the profile (what `hash` covers)."""
    files = [p for p in (path / "profile.toml", path / "instructions.md", path / "ui.json",
                         path / "memory_guidance.md") if p.exists()]
    files += sorted((path / "skills").glob("*/SKILL.md"))
    files += sorted((path / "tools").glob("*.py"))
    return files


def load(path: str | Path, *, settings: EngineSettings | None = None) -> Profile:
    """Read and validate a profile directory. Raises ProfileError with every
    problem found, so `harness profile validate` can print a full report."""
    path = Path(path).expanduser().resolve()
    settings = settings or EngineSettings()
    problems: list[str] = []

    if not path.is_dir():
        raise ProfileError([f"{path} is not a directory"])
    toml_path = path / "profile.toml"
    if not toml_path.exists():
        raise ProfileError([f"{toml_path} not found"])
    raw_toml = toml_path.read_text()
    try:
        data = tomllib.loads(raw_toml)
    except tomllib.TOMLDecodeError as exc:
        raise ProfileError([f"profile.toml is not valid TOML: {exc}"]) from exc

    meta = data.get("profile", {})
    profile_id = str(meta.get("id") or path.name)
    if not _ID_RE.match(profile_id):
        problems.append(
            f"profile.id {profile_id!r} must be lowercase alphanumeric with - or _"
        )

    model_raw = data.get("model", {})
    raw_effort = model_raw.get("effort", DEFAULT_EFFORT)
    if str(raw_effort).strip().lower() not in EFFORT_LEVELS:
        problems.append(
            f"model.effort {raw_effort!r} must be one of {', '.join(EFFORT_LEVELS)}"
        )
    model = ModelConfig(
        spec=str(model_raw.get("spec", ModelConfig.spec)),
        effort=coerce_effort(raw_effort),
        request_limit=int(model_raw.get("request_limit", ModelConfig.request_limit)),
        retries=int(model_raw.get("retries", ModelConfig.retries)),
        prices={k: dict(v) for k, v in (model_raw.get("prices") or {}).items()},
    )

    code_raw = data.get("code_tool", {})
    code_tool = CodeToolConfig(
        name=str(code_raw.get("name", CodeToolConfig.name)),
        purpose=str(code_raw.get("purpose", "")),
        wall_clock_s=float(code_raw.get("wall_clock_s", CodeToolConfig.wall_clock_s)),
        max_retries=int(code_raw.get("max_retries", CodeToolConfig.max_retries)),
        mount_path=str(code_raw.get("mount_path", CodeToolConfig.mount_path)),
        resource_limits=dict(
            code_raw.get("resource_limits")
            or {"max_duration_secs": 600, "max_memory": 1024**3}
        ),
    )
    if not code_tool.name.isidentifier():
        problems.append(f"code_tool.name {code_tool.name!r} is not a valid python identifier")

    skills_raw = data.get("skills", {})
    skills = SkillsConfig(
        inline=[str(s) for s in skills_raw.get("inline", [])],
        deferred=[str(s) for s in skills_raw.get("deferred", [])],
    )
    for skill in [*skills.inline, *skills.deferred]:
        if not (path / "skills" / skill / "SKILL.md").exists():
            problems.append(f"skills: {skill}/SKILL.md not found")

    tools_raw = data.get("tools", {})
    modules = [str(m) for m in tools_raw.get("modules", [])]
    if not modules and (path / "tools").is_dir():
        modules = [p.name for p in sorted((path / "tools").glob("*.py"))]
    tools = ToolsConfig(modules=modules, settings=dict(tools_raw.get("settings") or {}))
    for module in tools.modules:
        if not (path / "tools" / module).exists():
            problems.append(f"tools: {module} not found")

    limits_raw = data.get("limits", {})
    limits = LimitsConfig(
        spill_tokens=int(limits_raw.get("spill_tokens", LimitsConfig.spill_tokens)),
        spill_tools=[str(t) for t in limits_raw.get("spill_tools", [])]
        or [
            code_tool.name,
            "python_analysis",
            "run_command",
            "start_command",
            "check_command",
            "read_file",
            "search_files",
        ],
        clamp_part_tokens=int(
            limits_raw.get("clamp_part_tokens", LimitsConfig.clamp_part_tokens)
        ),
        clear_tool_results_tokens=int(
            limits_raw.get("clear_tool_results_tokens", LimitsConfig.clear_tool_results_tokens)
        ),
        keep_pairs=int(limits_raw.get("keep_pairs", LimitsConfig.keep_pairs)),
        analysis_timeout_s=float(
            limits_raw.get("analysis_timeout_s", LimitsConfig.analysis_timeout_s)
        ),
    )

    env_raw = data.get("env", {})
    env_spec = EnvSpec(
        required=[str(n) for n in env_raw.get("required", [])],
        optional=[str(n) for n in env_raw.get("optional", [])],
        defaults={str(k): str(v) for k, v in (env_raw.get("defaults") or {}).items()},
        expose_to_shell=[str(n) for n in env_raw.get("expose_to_shell", [])],
    )

    ui = dict(DEFAULT_UI)
    ui_path = path / "ui.json"
    if ui_path.exists():
        try:
            ui.update(json.loads(ui_path.read_text()))
        except ValueError as exc:
            problems.append(f"ui.json is not valid JSON: {exc}")

    instructions_path = path / "instructions.md"
    if not instructions_path.exists():
        problems.append("instructions.md not found")
        instructions_template = ""
    else:
        instructions_template = instructions_path.read_text()

    guidance_path = path / "memory_guidance.md"
    memory_guidance = (
        guidance_path.read_text().strip()
        if guidance_path.exists()
        else core_instructions.DEFAULT_MEMORY_GUIDANCE
    )

    instructions_raw = data.get("instructions", {})
    template_vars = {
        **_VAR_DEFAULTS,
        "code_tool": code_tool.name,
        "data_source": str(ui.get("data_source_name")),
        "spill_k": str(limits.spill_tokens // 1000),
        "profile_name": str(meta.get("name") or profile_id),
        **{
            str(k): str(v)
            for k, v in instructions_raw.items()
            if k != "vars" and not isinstance(v, dict)
        },
        **{str(k): str(v) for k, v in (instructions_raw.get("vars") or {}).items()},
    }

    data_dir = settings.profile_data_dir(profile_id)
    env = _resolve_env(profile_id, env_spec, data_dir / _SECRETS_FILE, path)

    profile = Profile(
        id=profile_id,
        name=str(meta.get("name") or profile_id),
        description=str(meta.get("description") or ""),
        path=path,
        data_dir=data_dir,
        model=model,
        code_tool=code_tool,
        skills=skills,
        tools=tools,
        limits=limits,
        env_spec=env_spec,
        ui=ui,
        instructions_template=instructions_template,
        memory_guidance=memory_guidance,
        template_vars=template_vars,
        env=env,
        hash=_hash_files(config_files(path)),
        raw_toml=raw_toml,
    )

    if problems:
        raise ProfileError(problems)
    # Placeholder errors are raised by _render; surface them as load errors so
    # a broken prompt can never reach a run.
    profile.resolve_instructions()
    return profile


def write_secret(profile: Profile, name: str, value: str) -> None:
    """Upsert one value in the profile's secrets file (owner-readable only)."""
    path = profile.secrets_file
    path.parent.mkdir(parents=True, exist_ok=True)
    values = _read_env_file(path)
    values[name] = value
    _write_env_file(path, values)


def delete_secret(profile: Profile, name: str) -> bool:
    path = profile.secrets_file
    values = _read_env_file(path)
    if name not in values:
        return False
    del values[name]
    _write_env_file(path, values)
    return True


def _write_env_file(path: Path, values: dict[str, str]) -> None:
    body = "".join(f"{k}={v}\n" for k, v in sorted(values.items()))
    path.write_text(
        "# Written by the harness admin API / CLI. Not committed: the data\n"
        "# root is gitignored. Values are process-environment overridable.\n" + body
    )
    path.chmod(0o600)
