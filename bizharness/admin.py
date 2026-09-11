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
from fastapi.responses import JSONResponse

from . import plugins, profile as profile_mod
from .profile import ProfileError, delete_secret, write_secret


_ALLOWED_SKILL = "SKILL.md"


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


def mount(app: FastAPI, state) -> None:
    @app.get("/admin/profile")
    async def admin_get(request: Request):
        if err := _require_admin(request, state.engine.admin_token):
            return err
        p = state.profile
        return {
            "id": p.id,
            "hash": p.hash,
            "reload_key": p.reload_key,
            "toml": p.raw_toml,
            "tools": sorted(f.name for f in p.tools_dir.glob("*.py")) if p.tools_dir.exists() else [],
            "skills": [s.name for s in p.skills_dir.iterdir() if s.is_dir()] if p.skills_dir.exists() else [],
            "env": p.env_status(),
        }

    @app.post("/admin/profile/reload")
    async def admin_reload(request: Request):
        if err := _require_admin(request, state.engine.admin_token):
            return err
        p = state.reload_from_disk()
        return {"ok": True, "hash": p.hash}

    @app.put("/admin/profile/instructions.md")
    async def put_instructions(request: Request):
        return await _put_text(request, state, "instructions.md")

    @app.put("/admin/profile/profile.toml")
    async def put_toml(request: Request):
        return await _put_text(request, state, "profile.toml")

    @app.put("/admin/profile/tools/{name}")
    async def put_tool(name: str, request: Request):
        if not name.endswith(".py") or "/" in name or name.startswith("."):
            return JSONResponse({"detail": "invalid tool name"}, status_code=400)
        return await _put_text(request, state, f"tools/{name}")

    @app.delete("/admin/profile/tools/{name}")
    async def delete_tool(name: str, request: Request):
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

    @app.put("/admin/profile/skills/{skill}/SKILL.md")
    async def put_skill(skill: str, request: Request):
        if "/" in skill or skill.startswith("."):
            return JSONResponse({"detail": "invalid skill name"}, status_code=400)
        return await _put_text(request, state, f"skills/{skill}/{_ALLOWED_SKILL}")

    @app.get("/admin/profile/env")
    async def env_list(request: Request):
        if err := _require_admin(request, state.engine.admin_token):
            return err
        return {"env": state.profile.env_status()}

    @app.put("/admin/profile/env/{name}")
    async def env_set(name: str, request: Request):
        if err := _require_admin(request, state.engine.admin_token):
            return err
        body = await request.json()
        value = str(body.get("value", ""))
        write_secret(state.profile, name, value)
        _audit(state.profile_path, f"env/{name}", b"(value omitted)\n")
        state.reload_from_disk()
        return {"ok": True, "name": name}

    @app.delete("/admin/profile/env/{name}")
    async def env_delete(name: str, request: Request):
        if err := _require_admin(request, state.engine.admin_token):
            return err
        delete_secret(state.profile, name)
        _audit(state.profile_path, f"env/{name}", b"(deleted)\n")
        state.reload_from_disk()
        return {"ok": True}


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
