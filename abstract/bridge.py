"""Tool-call event bridge for the AG-UI frontend.

The AG-UI protocol streams only *top-level* tool calls. The interesting
activity of this agent — the data fetches — happens as nested calls inside
the code sandbox, invisible to the protocol. This bridge taps the same
capability hooks as tracing.ToolTrace and republishes every tool execution
(top-level and nested alike, in one uniform format) onto a per-thread
fan-out, which server.py exposes as a side-channel SSE endpoint.

The frontend takes the *lifecycle* of top-level calls from the AG-UI
stream (a call is finished iff its tool result exists — true for successes
and errors alike) and uses this feed only to enrich: code text, nested
calls, durations, result previews, error text. So a gap in this feed can
degrade detail but never leave a card "running".
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities.abstract import AbstractCapability

_ARGS_LIMIT = 4_000
_RESULT_LIMIT = 2_000
_REPLAY_LIMIT = 1_000  # most recent events re-sent to a fresh subscriber


def safe_id(thread_id: str) -> str:
    """Filesystem-safe thread id (same rule everywhere it becomes a path)."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in thread_id)


def is_data_event(event: dict, data_tools: frozenset[str]) -> bool:
    """Is this event one individual data-source call (nested or not)?

    New events carry `kind`; events logged without it are classified by tool name.
    """
    kind = event.get("kind")
    if kind == "data":
        return True
    if kind == "control":
        return False
    return event.get("tool") in data_tools


def _preview(value: Any, limit: int) -> str:
    # The code tool returns a ToolReturn whose return_value is {"output":
    # "<the snippet's stdout>"}: show that text, not the repr of the wrapper.
    if hasattr(value, "return_value"):
        value = value.return_value
    if isinstance(value, dict) and set(value) == {"output"} and isinstance(value["output"], str):
        value = value["output"]
    if isinstance(value, str):
        text = value  # raw, so JSON printed by a snippet stays parseable
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = repr(value)
    if len(text) > limit:
        half = limit // 2
        text = f"{text[:half]} … {text[-half:]} [{len(text)} chars total]"
    return text


class ToolEventHub:
    """Per-thread fan-out of tool events to any number of SSE subscribers.

    When `log_dir` is set (the server points it at the thread-state
    directory), every event is also appended to
    <log_dir>/<thread_id>/tool_events.jsonl, and `replay()` returns the
    stored tail — so a reloaded page gets its tool timeline back instead
    of starting blank.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[dict]]] = {}
        self._lock = asyncio.Lock()
        self.log_dir: Path | None = None
        # Names of the profile's data tools (the server sets this); used by
        # summary() to classify events logged before `kind` existed, and by
        # legacy logs copied over from an older deployment.
        self.data_tools: frozenset[str] = frozenset()
        # The profile's code tool name, so its blocks are counted separately
        # from the individual data calls inside them.
        self.code_tool_names: frozenset[str] = frozenset({"run_code"})

    def _log_path(self, thread_id: str) -> Path | None:
        if self.log_dir is None:
            return None
        return self.log_dir / safe_id(thread_id) / "tool_events.jsonl"

    def _read_log(self, thread_id: str) -> list[dict]:
        path = self._log_path(thread_id)
        if path is None or not path.exists():
            return []
        events = []
        for line in path.read_text().splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                continue
        return events

    def replay(self, thread_id: str, limit: int = _REPLAY_LIMIT) -> list[dict]:
        return self._read_log(thread_id)[-limit:]

    def summary(self, thread_id: str) -> dict:
        """Whole-thread counters (over the full log, not just the replayed
        tail): individual data calls, code blocks, and the ts of the last
        stored event so a subscriber can tell replayed-and-counted events
        from genuinely new ones."""
        events = self._read_log(thread_id)
        data_calls = sum(
            1 for e in events if e.get("phase") == "start" and is_data_event(e, self.data_tools)
        )
        blocks = sum(
            1
            for e in events
            if e.get("phase") == "start" and e.get("tool") in self.code_tool_names
        )
        last_ts = max((e.get("ts", 0) for e in events), default=0)
        return {
            "phase": "summary",
            "data_calls": data_calls,
            "code_blocks": blocks,
            "last_ts": last_ts,
        }

    async def subscribe(self, thread_id: str) -> asyncio.Queue[dict]:
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers.setdefault(thread_id, set()).add(queue)
        return queue

    async def unsubscribe(self, thread_id: str, queue: asyncio.Queue[dict]) -> None:
        async with self._lock:
            subs = self._subscribers.get(thread_id)
            if subs:
                subs.discard(queue)
                if not subs:
                    del self._subscribers[thread_id]

    def publish(self, thread_id: str, event: dict, *, persist: bool = True) -> None:
        """Fan out to live subscribers; `persist=False` for events that have
        their own store (usage records) and must not enter the replay log."""
        for queue in self._subscribers.get(thread_id, set()):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                pass  # a stalled subscriber must not block the agent
        path = self._log_path(thread_id) if persist else None
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a") as fh:
                    fh.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            except OSError:
                pass  # event logging must never break a run


hub = ToolEventHub()


class NestedCallBridge(AbstractCapability):
    """Publishes every tool execution (incl. nested sandbox calls) to the hub."""

    def __init__(
        self, thread_id: str, label: str = "main", data_tools: frozenset[str] = frozenset()
    ):
        super().__init__()
        self._thread_id = thread_id
        self._label = label
        self._data_tools = data_tools  # names of the profile's data tools
        self._seq = 0

    def _emit(self, phase: str, call, extra: dict) -> None:
        self._seq += 1
        hub.publish(
            self._thread_id,
            {
                "phase": phase,
                "seq": self._seq,
                "call_id": call.tool_call_id,
                "tool": call.tool_name,
                # "data" = one individual read from the profile's data source
                # (the thing the UI counts); "control" = the agent's own
                # machinery.
                "kind": "data" if call.tool_name in self._data_tools else "control",
                "agent": self._label,
                "ts": time.time(),
                **extra,
            },
        )

    # The whole execution is one span: `start` is emitted before the tool
    # runs and `end` ALWAYS follows — success, ModelRetry (how the sandbox
    # reports every snippet failure), any other exception, or cancellation
    # (wall-clock limit, shutdown). before/after hooks were used before, and
    # pydantic-ai skips after_tool_execute when the tool raises, so a failed
    # code block never closed and its card stayed "running" in the UI.
    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        self._emit_start(call, args)
        t0 = time.perf_counter()
        status, error = "ok", None
        result: Any = None
        try:
            result = await handler(args)
            return result
        except asyncio.CancelledError:
            status = "cancelled"
            raise
        except Exception as exc:
            # A ModelRetry arrives here already wrapped as ToolRetryError;
            # its RetryPromptPart holds the message the model will read.
            retry_content = getattr(getattr(exc, "tool_retry", None), "content", None)
            status = "error"
            error = (
                retry_content if isinstance(retry_content, str) else f"{type(exc).__name__}: {exc}"
            )
            raise
        finally:
            extra: dict = {
                "duration_ms": round((time.perf_counter() - t0) * 1000),
                "status": status,
            }
            if status == "ok":
                extra["result"] = _preview(result, _RESULT_LIMIT)
            else:
                extra["error"] = (error or status)[:_RESULT_LIMIT]
            self._emit("end", call, extra)

    def _emit_start(self, call, args) -> None:
        raw = getattr(args, "args_dict", None) or getattr(call, "args", None)
        if isinstance(raw, str):  # args often arrive as a JSON string
            try:
                raw = json.loads(raw)
            except ValueError:
                pass
        extra: dict = {}
        # The code tool takes `code`, python_analysis takes `script`: both are
        # source text and render as code in the UI, not as an escaped JSON
        # args preview.
        source = None
        if isinstance(raw, dict):
            for key in ("code", "script"):
                if isinstance(raw.get(key), str):
                    source = raw[key]
                    break
        if source is not None:
            extra["code"] = source[:_ARGS_LIMIT]
        else:
            extra["args"] = _preview(raw, _ARGS_LIMIT)
        self._emit("start", call, extra)
