"""AG-UI server for a profile-driven analysis agent.

Every chat thread gets its own isolated agent instance and workspace
(workspaces/<thread_id>/), so parallel chats can't mix assets and a fresh
conversation never shows stale files. Memory stays shared across threads
(cross-session knowledge); plans are per thread.

Runs are DETACHED (see runs.py): POST /agui starts the run in a background
task and the response only attaches to its buffered event stream, so a page
reload or dropped connection no longer kills the run. The server persists
the final conversation itself when a run completes, and tool events are
logged per thread so the timeline survives a refresh.

Run with:  bizharness serve --profile PATH --port 8811

Auth: every request except `/admin/*`, the docs pages, and the built UI
files must carry the API key as `Authorization: Bearer <key>` or
`?token=` (the latter so iframe and EventSource links work). The key is
created at startup in `<data-root>/api_key` and printed in the log.
`/admin/*` is gated separately by `HARNESS_ADMIN_TOKEN`.

When `ui/dist` (or `HARNESS_UI_DIR`) contains `index.html`, that build is
served on `/` by the same process. API routes stay on their own paths.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import secrets
import shutil
import signal
import sys
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from pydantic_ai.ui.ag_ui import AGUIAdapter
from pydantic_ai.usage import UsageLimits
from starlette.datastructures import Headers, QueryParams

from . import __version__, admin, engine as engine_mod, profile as profile_mod
from .auth import ApiKey, load_or_create_api_key
from .api_docs import (
    EVENTS,
    OPENAPI_TAGS,
    PROFILE,
    RUNS,
    THREADS,
    WORKSPACE,
    EventStreamResponse,
    SaveThreadBody,
    install as install_openapi,
)
from .bridge import NestedCallBridge, hub, safe_id as _safe_id
from .config import EngineSettings, load_dotenv
from .engine import model_settings_for_effort
from .profile import Profile, coerce_effort
from .runs import RunActiveError, RunManager, ThreadRun, next_or_shutdown, shutdown
from .usage import UsageMeter, summary as usage_summary

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _hook_shutdown_signals() -> None:
    """Make Ctrl+C / SIGTERM end every open SSE stream immediately.

    uvicorn's graceful shutdown stops listening, then waits for in-flight
    responses to complete before exiting — and the UI's EventSource
    connections never complete on their own, so the process hung until
    every browser tab was closed. We wrap uvicorn's handler so the same
    signal also sets `runs.shutdown`.
    """
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        previous = signal.getsignal(sig)

        def handler(signum, frame, _previous=previous):
            loop.call_soon_threadsafe(shutdown.set)
            if callable(_previous):
                _previous(signum, frame)

        try:
            signal.signal(sig, handler)
        except ValueError:
            return


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    _hook_shutdown_signals()
    yield
    shutdown.set()


# Swagger UI loads these without a key. Calls made from "Try it out" still
# need the API key; the scheme is on each operation.
_PUBLIC_PATHS = frozenset({"/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"})


class _TokenMiddleware:
    """Bearer/`?token=` check as pure ASGI middleware.

    Deliberately not `@app.middleware("http")` (BaseHTTPMiddleware): that
    wrapper pipes every response body through an anyio memory stream, which
    for long-lived SSE responses adds a task that only ends when the client
    disconnects — one of the things that kept the server alive on Ctrl+C.
    """

    def __init__(
        self,
        app,
        api_key: ApiKey,
        skip_prefixes: tuple[str, ...] = (),
        ui_dir: Path | None = None,
    ):
        self.app = app
        self.api_key = api_key
        self.skip_prefixes = skip_prefixes
        self.ui_dir = ui_dir

    def _ui_asset(self, path: str) -> bool:
        """GET/HEAD of the built UI. API paths are not files in that directory."""
        if self.ui_dir is None:
            return False
        if path in ("/", "/index.html"):
            return True
        rel = path.lstrip("/")
        if not rel or ".." in Path(rel).parts:
            return False
        candidate = (self.ui_dir / rel).resolve()
        try:
            candidate.relative_to(self.ui_dir)
        except ValueError:
            return False
        return candidate.is_file()

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if (
            self.ui_dir is not None
            and scope.get("type") == "http"
            and scope.get("method") in ("GET", "HEAD")
            and self._ui_asset(path)
        ):
            await self.app(scope, receive, send)
            return
        if path in _PUBLIC_PATHS or path.startswith("/docs/"):
            await self.app(scope, receive, send)
            return
        if self.skip_prefixes and any(
            path == p or path.startswith(p.rstrip("/") + "/") for p in self.skip_prefixes
        ):
            await self.app(scope, receive, send)
            return
        if scope["type"] == "http" and scope.get("method") != "OPTIONS":
            supplied = (
                Headers(scope=scope).get("authorization", "").removeprefix("Bearer ").strip()
            )
            if not supplied:
                supplied = QueryParams(scope.get("query_string", b"")).get("token", "")
            if not secrets.compare_digest(supplied.encode(), self.api_key.value.encode()):
                response = JSONResponse({"detail": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


class AppState:
    """Mutable server state: the loaded profile, per-thread agents, paths."""

    def __init__(
        self,
        profile: Profile,
        engine: EngineSettings,
        profile_path: Path,
        api_key: ApiKey,
    ):
        self.profile = profile
        self.engine = engine
        self.profile_path = Path(profile_path).resolve()
        self.api_key = api_key
        self.runs = RunManager()
        self.agents: dict = {}
        self.agents_lock = asyncio.Lock()
        self._wire_hub()

    def _apply_paths(self) -> None:
        self.profile.data_dir.mkdir(parents=True, exist_ok=True)
        self.profile.workspaces_dir.mkdir(parents=True, exist_ok=True)
        self.profile.threads_dir.mkdir(parents=True, exist_ok=True)
        self.profile.state_dir.mkdir(parents=True, exist_ok=True)

    def _wire_hub(self) -> None:
        self._apply_paths()
        hub.log_dir = self.profile.threads_dir
        hub.code_tool_names = frozenset({self.profile.code_tool.name, "run_code"})
        try:
            from . import plugins

            toolset, _ = plugins.load(self.profile)
            hub.data_tools = frozenset(toolset.tools)
        except Exception as exc:
            print(f"[server] could not load data tools for counters: {exc!r}", file=sys.stderr)
            hub.data_tools = frozenset()

    @property
    def workspaces_root(self) -> Path:
        return self.profile.workspaces_dir

    @property
    def threads_dir(self) -> Path:
        return self.profile.threads_dir

    def reload_from_disk(self, *, drop_agents: bool = True) -> Profile:
        self.profile = profile_mod.load(self.profile_path, settings=self.engine)
        self._wire_hub()
        if drop_agents:
            self.agents.clear()
        return self.profile

    async def agent_for(self, thread_id: str):
        key = _safe_id(thread_id)
        async with self.agents_lock:
            # Rebuild if the profile files changed (admin PUT, or an editor).
            on_disk = profile_mod.load(self.profile_path, settings=self.engine)
            if on_disk.reload_key != self.profile.reload_key:
                self.profile = on_disk
                self._wire_hub()
                self.agents.clear()
            if key not in self.agents:
                built = engine_mod.build(self.profile, instance=key)
                hub.data_tools = built.data_tools
                self.agents[key] = built
            return self.agents[key]


def _run_error_from_sse(chunk: str) -> str | None:
    """Pull a RUN_ERROR message out of an encoded AG-UI SSE chunk, if any.

    The adapter emits model/provider failures as protocol events rather than
    raising, so the HTTP stream is still 200. Without this, `run.finish()`
    would record success and the UI would show a blank turn.
    """
    if "RUN_ERROR" not in chunk:
        return None
    for line in chunk.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            data = json.loads(line[5:].lstrip())
        except ValueError:
            continue
        if data.get("type") == "RUN_ERROR" and data.get("message"):
            return str(data["message"])
    return None


def _encode_error_event(message: str) -> str:
    try:
        from ag_ui.core import EventType, RunErrorEvent
        from ag_ui.encoder import EventEncoder

        return EventEncoder().encode(RunErrorEvent(type=EventType.RUN_ERROR, message=message))
    except Exception:
        return f'data: {json.dumps({"type": "RUN_ERROR", "message": message})}\n\n'


def _effort_from_forwarded(props: object, *, default: str) -> str:
    """Read `effort` out of AG-UI forwardedProps, if the client sent one."""
    if isinstance(props, dict):
        return coerce_effort(props.get("effort"), default=default)
    return default


def _title_from_messages(messages: list) -> str:
    for m in messages:
        if isinstance(m, dict) and m.get("role") == "user":
            content = m.get("content")
            if isinstance(content, list):
                content = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in content
                )
            text = " ".join(str(content or "").split())
            return text[:80] + ("…" if len(text) > 80 else "")
    return "(empty)"


def create_app(
    profile: Profile,
    *,
    engine: EngineSettings | None = None,
    profile_path: Path | None = None,
) -> FastAPI:
    engine = engine or EngineSettings()
    api_key = load_or_create_api_key(engine.data_root, seed=engine.ui_token)
    state = AppState(profile, engine, profile_path or profile.path, api_key)
    app = FastAPI(
        title=f"{profile.name} AG-UI server",
        version=__version__,
        description=(
            f"HTTP API for the {profile.name} agent.\n\n"
            "Start a conversation with `POST /agui`. The body is an "
            "[AG-UI RunAgentInput](https://docs.ag-ui.com/concepts/messages) "
            "and the response is a server-sent event stream. Saved threads, "
            "workspace files, and the admin editor live on this same server.\n\n"
            "Send the API key as `Authorization: Bearer <key>` "
            "(or `?token=` on links that cannot set a header). "
            "The key is created at startup, printed in the server log, and stored in "
            "`<data-root>/api_key`. `POST /admin/api-key` replaces it. "
            "`/admin` routes use `HARNESS_ADMIN_TOKEN` instead of the API key."
        ),
        servers=[{"url": "/", "description": profile.name}],
        openapi_tags=OPENAPI_TAGS,
        lifespan=_lifespan,
    )
    app.state.harness = state
    ui_root = _usable_ui_dir(engine.ui_dir)

    app.add_middleware(
        _TokenMiddleware,
        api_key=api_key,
        skip_prefixes=("/admin",),
        ui_dir=ui_root,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=engine.ui_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _conversation_path(thread_id: str) -> Path:
        return state.threads_dir / _safe_id(thread_id) / "conversation.json"

    def _persist_conversation(
        thread_id: str,
        messages: list[dict],
        model: str | None = None,
        effort: str | None = None,
    ) -> None:
        path = _conversation_path(thread_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except ValueError:
                existing = {}
        if model is None:
            model = existing.get("model")
        if effort is None:
            effort = existing.get("effort")
        payload = {
            "id": _safe_id(thread_id),
            "title": _title_from_messages(messages),
            "updated_at": time.time(),
            "model": model,
            "effort": effort,
            "profile_hash": state.profile.hash,
            "messages": messages,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False))

    @app.get("/profile", tags=[PROFILE], summary="Profile shown in the chat UI")
    async def get_profile():
        """Name, model, effort levels, and UI copy for the loaded profile."""
        return state.profile.ui_config()

    @app.post(
        "/agui",
        tags=[RUNS],
        summary="Start a run",
        response_class=EventStreamResponse,
        response_description=(
            "AG-UI events for this run. Disconnecting does not cancel the agent; "
            "reattach with GET /agui/{thread_id}/attach."
        ),
        responses={
            409: {"description": "A run is already active for this thread."},
            422: {"description": "The body is not a valid AG-UI RunAgentInput."},
        },
    )
    async def agui_run(request: Request):
        """Start an agent run and stream its AG-UI events.

        The body is a `RunAgentInput`. `forwardedProps.effort` overrides reasoning
        effort for this run. The HTTP response only attaches to the run.
        """
        try:
            body = await request.json()
            thread_id = body.get("threadId") or body.get("thread_id") or "default"
        except Exception:
            thread_id = "default"
        built = await state.agent_for(thread_id)
        try:
            adapter = await AGUIAdapter.from_request(request, agent=built.agent)
        except ValidationError as exc:
            return JSONResponse({"detail": json.loads(exc.json())}, status_code=422)
        bridge = NestedCallBridge(thread_id, data_tools=built.data_tools)
        meter = UsageMeter(
            state.threads_dir,
            thread_id,
            price_overrides=state.profile.model.prices,
        )
        run_id = getattr(adapter.run_input, "run_id", None)
        input_messages = list(getattr(adapter.run_input, "messages", []) or [])
        limits = UsageLimits(request_limit=state.profile.model.request_limit)
        stored_effort = None
        conv_path = _conversation_path(thread_id)
        if conv_path.exists():
            try:
                stored_effort = json.loads(conv_path.read_text()).get("effort")
            except ValueError:
                stored_effort = None
        effort = _effort_from_forwarded(
            getattr(adapter.run_input, "forwarded_props", None),
            default=coerce_effort(stored_effort, default=state.profile.model.effort),
        )

        async def execute(run: ThreadRun) -> None:
            captured: dict = {}

            def on_complete(result) -> None:
                captured["result"] = result

            error: str | None = None
            try:
                events = adapter.run_stream(
                    usage_limits=limits,
                    capabilities=[bridge, meter],
                    on_complete=on_complete,
                    model_settings=model_settings_for_effort(effort),
                )
                async for chunk in adapter.encode_stream(events):
                    run.append(chunk)
                    if error is None:
                        error = _run_error_from_sse(chunk)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                run.append(_encode_error_event(error))
            finally:
                run.finish(error)
                result = captured.get("result")
                if result is not None:
                    try:
                        new_messages = AGUIAdapter.dump_messages(result.new_messages())
                        _persist_conversation(
                            thread_id,
                            [
                                m.model_dump(mode="json", by_alias=True)
                                for m in [*input_messages, *new_messages]
                            ],
                            model=state.profile.model.spec,
                            effort=effort,
                        )
                    except Exception as persist_exc:
                        print(f"[runs] conversation persist failed: {persist_exc!r}", file=sys.stderr)

        try:
            run = state.runs.start(thread_id, run_id, execute)
        except RunActiveError:
            return JSONResponse(
                {"detail": "a run is already active for this thread"}, status_code=409
            )
        return StreamingResponse(run.attach(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.get(
        "/agui/{thread_id}/attach",
        tags=[RUNS],
        summary="Reattach to a run",
        response_class=EventStreamResponse,
        response_description="AG-UI events from the start of the active run, then live events.",
        responses={404: {"description": "This thread has no run to attach to."}},
    )
    async def agui_attach(thread_id: str):
        """Replay the buffered event stream for the thread's active run, then follow it."""
        run = state.runs.get(thread_id)
        if run is None:
            return JSONResponse({"detail": "no run for this thread"}, status_code=404)
        return StreamingResponse(run.attach(), media_type="text/event-stream", headers=_SSE_HEADERS)

    @app.get("/runs/{thread_id}", tags=[RUNS], summary="Run status")
    async def run_status(thread_id: str):
        """Whether this thread has a run, and that run's id, timing, and error."""
        run = state.runs.get(thread_id)
        if run is None:
            return {"active": False}
        return run.status()

    @app.get(
        "/events/{thread_id}",
        tags=[EVENTS],
        summary="Tool activity stream",
        response_class=EventStreamResponse,
        response_description=(
            "A summary frame, then the stored tool events, then live events. "
            "Comments of the form `: ping` are keepalives."
        ),
    )
    async def tool_events(thread_id: str):
        """Stream tool-call activity for one thread."""
        queue = await hub.subscribe(thread_id)
        history = hub.replay(thread_id)
        summary = hub.summary(thread_id)
        summary["usage"] = usage_summary(
            state.threads_dir, thread_id, overrides=state.profile.model.prices
        )

        async def stream():
            try:
                yield f"data: {json.dumps(summary, ensure_ascii=False)}\n\n"
                for event in history:
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                while True:
                    try:
                        event = await next_or_shutdown(queue, timeout=15.0)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if event is None:
                        return
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            finally:
                await hub.unsubscribe(thread_id, queue)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get(
        "/workspace/{thread_id}/files",
        tags=[WORKSPACE],
        summary="List workspace files",
    )
    async def workspace_files(thread_id: str):
        """Files the agent wrote under this thread's workspace, excluding dotfiles."""
        root = state.workspaces_root / _safe_id(thread_id)
        entries = []
        if root.exists():
            for path in sorted(root.rglob("*")):
                if path.is_dir() or any(part.startswith(".") for part in path.parts):
                    continue
                stat = path.stat()
                entries.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "size": stat.st_size,
                        "mtime": stat.mtime if hasattr(stat, "mtime") else stat.st_mtime,
                        "kind": path.suffix.lstrip(".").lower() or "file",
                    }
                )
        return {"root": str(root), "files": entries}

    @app.get("/threads", tags=[THREADS], summary="List saved threads")
    async def list_threads():
        """Saved conversations, newest first, plus the profile's default model and effort."""
        threads = []
        for conv in state.threads_dir.glob("*/conversation.json"):
            try:
                data = json.loads(conv.read_text())
            except ValueError:
                continue
            threads.append(
                {
                    "id": conv.parent.name,
                    "title": data.get("title", ""),
                    "updated_at": data.get("updated_at", 0),
                    "message_count": len(data.get("messages", [])),
                    "model": data.get("model"),
                    "effort": data.get("effort"),
                }
            )
        threads.sort(key=lambda t: t["updated_at"], reverse=True)
        return {
            "threads": threads,
            "default_model": state.profile.model.spec,
            "default_effort": state.profile.model.effort,
        }

    @app.get("/threads/{thread_id}", tags=[THREADS], summary="Load a thread")
    async def get_thread(thread_id: str):
        """Saved transcript for one thread. A missing thread returns an empty transcript."""
        path = _conversation_path(thread_id)
        if not path.exists():
            return {
                "id": thread_id,
                "title": "",
                "messages": [],
                "effort": state.profile.model.effort,
            }
        return json.loads(path.read_text())

    @app.put("/threads/{thread_id}", tags=[THREADS], summary="Save a thread")
    async def save_thread(thread_id: str, body: SaveThreadBody):
        """Replace the saved transcript. Effort is updated only when the body includes it."""
        _persist_conversation(
            thread_id,
            body.messages,
            effort=coerce_effort(body.effort) if body.effort is not None else None,
        )
        return {"ok": True}

    @app.delete(
        "/threads/{thread_id}",
        tags=[THREADS],
        summary="Delete a thread",
        responses={409: {"description": "The thread's run is still active."}},
    )
    async def delete_thread(thread_id: str):
        """Delete the conversation, its tool log, and its workspace."""
        run = state.runs.get(thread_id)
        if run is not None and run.active:
            return JSONResponse(
                {"detail": "cannot delete a thread while its run is active"}, status_code=409
            )
        key = _safe_id(thread_id)
        async with state.agents_lock:
            state.agents.pop(key, None)
        for path in (state.threads_dir / key, state.workspaces_root / key):
            shutil.rmtree(path, ignore_errors=True)
        return {"ok": True}

    admin.mount(app, state)
    install_openapi(app)
    app.mount(
        "/workspace/raw",
        StaticFiles(directory=str(state.workspaces_root)),
        name="workspaces",
    )
    # Mounted last. A mount at "/" matches every path, so it has to come
    # after the API routes, which then win on a full match.
    if ui_root is not None:
        app.mount("/", StaticFiles(directory=str(ui_root), html=True), name="ui")
    return app


def _usable_ui_dir(ui_dir: Path | None) -> Path | None:
    """Resolved UI build directory, or None when there is nothing to serve."""
    if ui_dir is None:
        return None
    root = Path(ui_dir).expanduser().resolve()
    if not (root / "index.html").is_file():
        print(f"[bizharness] UI dir {root} has no index.html; serving API only", file=sys.stderr)
        return None
    print(f"[bizharness] ui={root}")
    return root


# Default module-level app for `uvicorn bizharness.server:app` when
# HARNESS_PROFILE is set. The CLI uses create_app directly.
def _app_from_env():
    path = os.environ.get("HARNESS_PROFILE")
    if not path:
        return FastAPI(title="bizharness (set HARNESS_PROFILE)")
    profile_path = Path(path).expanduser()
    load_dotenv(profile_path / ".env")
    engine = EngineSettings()
    app = create_app(
        profile_mod.load(profile_path, settings=engine),
        engine=engine,
        profile_path=profile_path,
    )
    print(f"[bizharness] api key: {app.state.harness.api_key.value}", file=sys.stderr)
    return app


app = _app_from_env()
