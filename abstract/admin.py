"""Admin API: rewrite a profile's files, env, and reload.

Trusted-developer surface. Disabled unless HARNESS_ADMIN_TOKEN is set.
Every mutating write is validated in a temp copy first; on success the
previous file is copied to profiles/<id>/_history/<utc>_<relpath>.
Env values are never written into the audit copies.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from . import plugins, profile as profile_mod
from .api_docs import ADMIN, EnvValueBody, text_request_body
from .profile import ProfileError, delete_secret, write_secret


_ALLOWED_SKILL = "SKILL.md"
_PREVIEW_CHARS = 240


class ApiKeyResponse(BaseModel):
    """The API key that replaced the previous one."""

    api_key: str = Field(
        description=(
            "New API key. The previous key is rejected immediately. "
            "Send it as `Authorization: Bearer` or `?token=`."
        )
    )


def _preview(text: str, n: int = _PREVIEW_CHARS) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= n:
        return collapsed
    cut = collapsed[: n + 1]
    space = cut.rfind(" ")
    snippet = cut[:space] if space > n // 2 else collapsed[:n]
    return snippet.rstrip(".,;:—-") + "…"


def _tools_payload(p) -> list[dict]:
    rows = {row["name"]: row for row in plugins.inventory(p)}
    if not p.tools_dir.exists():
        return list(rows.values())
    out = []
    for path in sorted(p.tools_dir.glob("*.py")):
        row = rows.get(path.name)
        if row is None:
            row = {"name": path.name, "functions": 0, "helper": True, "names": [], "entries": []}
        out.append(row)
    return out


def _require_admin(request: Request, token: str | None) -> JSONResponse | None:
    if not token:
        return JSONResponse({"detail": "admin API disabled"}, status_code=404)
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if supplied != token:
        return JSONResponse({"detail": "unauthorized"}, status_code=401)
    return None


def _audit(profile_path: Path, rel: str, previous: bytes | None) -> None:
    if previous is None:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = profile_path / "_history" / f"{stamp}_{rel.replace('/', '_')}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(previous)


def _validate_copy(src: Path) -> list[str]:
    """Load + import tools from a directory; return problems (empty = ok)."""
    try:
        p = profile_mod.load(src)
    except ProfileError as exc:
        return list(exc.problems)
    missing = p.missing_env()
    if missing:
        return [f"required environment variable {n} is not set" for n in missing]
    try:
        plugins.load(p, reload=True)
    except plugins.PluginError as exc:
        return list(exc.problems)
    except Exception as exc:
        return [f"{type(exc).__name__}: {exc}"]
    return []


_INSTRUCTIONS_EXAMPLE = "Write the report in the house style.\n\n{{ core.execution }}\n"
_TOML_EXAMPLE = '[profile]\nname = "Example Agent"\ndescription = "A sample profile."\n'
_TOOL_EXAMPLE = (
    'def get_post(post_id: int) -> dict:\n    """Fetch one post."""\n    return {"id": post_id}\n'
)
_SKILL_EXAMPLE = (
    "---\nname: filing-review\ndescription: Review one filing.\n---\n\n"
    "Review one filing at a time.\n"
)


def mount(app: FastAPI, state) -> None:
    @app.get("/admin/profile", tags=[ADMIN], summary="Profile editor snapshot")
    async def admin_get(request: Request):
        """Toml, instruction preview, tools, skills, and env status for the loaded profile."""
        if err := _require_admin(request, state.engine.admin_token):
            return err
        p = state.profile
        skills = []
        if p.skills_dir.exists():
            inline = set(p.skills.inline)
            deferred = set(p.skills.deferred)
            for folder in sorted(p.skills_dir.iterdir(), key=lambda s: s.name):
                if not folder.is_dir():
                    continue
                if folder.name in inline:
                    load = "inline"
                elif folder.name in deferred:
                    load = "deferred"
                else:
                    load = "unused"
                skills.append({"name": folder.name, "load": load})
        rendered = p.render(p.instructions_template, where="instructions.md")
        return {
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "model": p.model.spec,
            "code_tool": p.code_tool.name,
            "hash": p.hash,
            "reload_key": p.reload_key,
            "toml": p.raw_toml,
            "instructions_preview": _preview(rendered),
            "instructions_chars": len(rendered),
            "tools": _tools_payload(p),
            "skills": skills,
            "env": p.env_status(),
        }

    @app.post(
        "/admin/api-key",
        tags=[ADMIN],
        summary="Rotate the API key",
        response_model=ApiKeyResponse,
    )
    async def rotate_api_key(request: Request):
        """Replace the API key used by the chat UI and by API clients.

        The new key is returned once. It is also written to `<data-root>/api_key`.
        """
        if err := _require_admin(request, state.engine.admin_token):
            return err
        return {"api_key": state.api_key.rotate()}

    @app.post("/admin/profile/reload", tags=[ADMIN], summary="Reload the profile from disk")
    async def admin_reload(request: Request):
        """Re-read the profile directory and drop cached agents. This route has no body."""
        if err := _require_admin(request, state.engine.admin_token):
            return err
        p = state.reload_from_disk()
        return {"ok": True, "hash": p.hash}

    @app.get(
        "/admin/profile/profile.toml",
        tags=[ADMIN],
        summary="Read profile.toml",
        response_class=PlainTextResponse,
    )
    async def get_toml(request: Request):
        """Raw `profile.toml` for the loaded profile."""
        return _get_text(request, state, "profile.toml")

    @app.get(
        "/admin/profile/instructions.md",
        tags=[ADMIN],
        summary="Read instructions.md",
        response_class=PlainTextResponse,
    )
    async def get_instructions(request: Request):
        """Raw `instructions.md` template, including `{{ core.* }}` placeholders."""
        return _get_text(request, state, "instructions.md")

    @app.post(
        "/admin/profile/render-instructions",
        tags=[ADMIN],
        summary="Preview rendered instructions",
        response_class=PlainTextResponse,
        response_description="Instructions after `{{ core.* }}` placeholders are filled in.",
        responses={
            422: {
                "description": (
                    "The template uses an unknown placeholder or otherwise failed to render."
                )
            }
        },
        openapi_extra=text_request_body(
            "UTF-8 `instructions.md` template, including `{{ core.* }}` placeholders. "
            "The response is the text the model would see.",
            _INSTRUCTIONS_EXAMPLE,
        ),
    )
    async def render_instructions(request: Request):
        """Render an instructions template against the loaded profile without saving it."""
        if err := _require_admin(request, state.engine.admin_token):
            return err
        try:
            text = (await request.body()).decode("utf-8")
        except UnicodeDecodeError:
            return JSONResponse({"detail": "body must be utf-8 text"}, status_code=400)
        try:
            rendered = state.profile.render(text, where="instructions.md")
        except ProfileError as exc:
            return JSONResponse(
                {"detail": "render failed", "problems": list(exc.problems)},
                status_code=422,
            )
        return PlainTextResponse(rendered)

    @app.get(
        "/admin/profile/tools/{name}",
        tags=[ADMIN],
        summary="Read a tool module",
        response_class=PlainTextResponse,
    )
    async def get_tool(name: str, request: Request):
        """Python source for one file in the profile's `tools/` directory."""
        if not name.endswith(".py") or "/" in name or name.startswith("."):
            return JSONResponse({"detail": "invalid tool name"}, status_code=400)
        return _get_text(request, state, f"tools/{name}")

    @app.get(
        "/admin/profile/skills/{skill}/SKILL.md",
        tags=[ADMIN],
        summary="Read a skill",
        response_class=PlainTextResponse,
    )
    async def get_skill(skill: str, request: Request):
        """`SKILL.md` for one skill directory."""
        if "/" in skill or skill.startswith("."):
            return JSONResponse({"detail": "invalid skill name"}, status_code=400)
        return _get_text(request, state, f"skills/{skill}/{_ALLOWED_SKILL}")

    @app.put(
        "/admin/profile/instructions.md",
        tags=[ADMIN],
        summary="Replace instructions.md",
        responses={
            422: {"description": "The template failed validation against a copy of the profile."}
        },
        openapi_extra=text_request_body(
            "Full UTF-8 replacement for `instructions.md`. Validated, then saved and reloaded.",
            _INSTRUCTIONS_EXAMPLE,
        ),
    )
    async def put_instructions(request: Request):
        """Replace `instructions.md`. The previous file is copied into `_history/`."""
        return await _put_text(request, state, "instructions.md")

    @app.put(
        "/admin/profile/profile.toml",
        tags=[ADMIN],
        summary="Replace profile.toml",
        responses={
            422: {"description": "The toml failed validation against a copy of the profile."}
        },
        openapi_extra=text_request_body(
            "Full UTF-8 replacement for `profile.toml`. Validated, then saved and reloaded.",
            _TOML_EXAMPLE,
        ),
    )
    async def put_toml(request: Request):
        """Replace `profile.toml`. The previous file is copied into `_history/`."""
        return await _put_text(request, state, "profile.toml")

    @app.put(
        "/admin/profile/tools/{name}",
        tags=[ADMIN],
        summary="Write a tool module",
        responses={422: {"description": "The module failed to import from a copy of the profile."}},
        openapi_extra=text_request_body(
            "Full UTF-8 Python source for `tools/{name}`. `name` must be a single `*.py` filename.",
            _TOOL_EXAMPLE,
        ),
    )
    async def put_tool(name: str, request: Request):
        """Create or replace one tool module. The previous file is copied into `_history/`."""
        if not name.endswith(".py") or "/" in name or name.startswith("."):
            return JSONResponse({"detail": "invalid tool name"}, status_code=400)
        return await _put_text(request, state, f"tools/{name}")

    @app.delete("/admin/profile/tools/{name}", tags=[ADMIN], summary="Delete a tool module")
    async def delete_tool(name: str, request: Request):
        """Delete one tool module and reload. The previous file is copied into `_history/`."""
        if err := _require_admin(request, state.engine.admin_token):
            return err
        if not name.endswith(".py") or "/" in name:
            return JSONResponse({"detail": "invalid tool name"}, status_code=400)
        path = state.profile.tools_dir / name
        if not path.exists():
            return JSONResponse({"detail": "not found"}, status_code=404)
        _audit(state.profile_path, f"tools/{name}", path.read_bytes())
        path.unlink()
        state.reload_from_disk()
        return {"ok": True}

    @app.put(
        "/admin/profile/skills/{skill}/SKILL.md",
        tags=[ADMIN],
        summary="Write a skill",
        responses={
            422: {"description": "The skill failed validation against a copy of the profile."}
        },
        openapi_extra=text_request_body(
            "Full UTF-8 replacement for `skills/{skill}/SKILL.md`.",
            _SKILL_EXAMPLE,
        ),
    )
    async def put_skill(skill: str, request: Request):
        """Create or replace one skill file. The previous file is copied into `_history/`."""
        if "/" in skill or skill.startswith("."):
            return JSONResponse({"detail": "invalid skill name"}, status_code=400)
        return await _put_text(request, state, f"skills/{skill}/{_ALLOWED_SKILL}")

    @app.get("/admin/profile/env", tags=[ADMIN], summary="Environment variable status")
    async def env_list(request: Request):
        """Which declared variables are set, and where the value comes from.

        Values themselves are omitted.
        """
        if err := _require_admin(request, state.engine.admin_token):
            return err
        return {"env": state.profile.env_status()}

    @app.put("/admin/profile/env/{name}", tags=[ADMIN], summary="Set an environment variable")
    async def env_set(name: str, request: Request, body: EnvValueBody):
        """Write one variable into the secrets file and reload.

        The audit copy records that the variable changed and leaves the value out.
        """
        if err := _require_admin(request, state.engine.admin_token):
            return err
        value = body.value
        write_secret(state.profile, name, value)
        _audit(state.profile_path, f"env/{name}", b"(value omitted)\n")
        state.reload_from_disk()
        return {"ok": True, "name": name}

    @app.delete(
        "/admin/profile/env/{name}",
        tags=[ADMIN],
        summary="Delete an environment variable",
    )
    async def env_delete(name: str, request: Request):
        """Remove one variable from the secrets file and reload."""
        if err := _require_admin(request, state.engine.admin_token):
            return err
        delete_secret(state.profile, name)
        _audit(state.profile_path, f"env/{name}", b"(deleted)\n")
        state.reload_from_disk()
        return {"ok": True}


def _get_text(request: Request, state, rel: str) -> JSONResponse | PlainTextResponse:
    if err := _require_admin(request, state.engine.admin_token):
        return err
    path = state.profile_path / rel
    if not path.is_file():
        return JSONResponse({"detail": "not found"}, status_code=404)
    return PlainTextResponse(path.read_text())


async def _put_text(request: Request, state, rel: str) -> JSONResponse:
    if err := _require_admin(request, state.engine.admin_token):
        return err
    body = await request.body()
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return JSONResponse({"detail": "body must be utf-8 text"}, status_code=400)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "profile"
        shutil.copytree(
            state.profile_path,
            tmp_path,
            ignore=shutil.ignore_patterns("_history", "__pycache__", "*.pyc"),
        )
        dest = tmp_path / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text)
        problems = _validate_copy(tmp_path)
        if problems:
            return JSONResponse({"detail": "validation failed", "problems": problems}, status_code=422)

    real = state.profile_path / rel
    previous = real.read_bytes() if real.exists() else None
    real.parent.mkdir(parents=True, exist_ok=True)
    real.write_text(text)
    _audit(state.profile_path, rel, previous)
    state.reload_from_disk()
    return JSONResponse({"ok": True, "hash": state.profile.hash})
