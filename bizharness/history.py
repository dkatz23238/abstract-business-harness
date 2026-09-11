"""Persistent JSON history of every chat session + a small trace viewer.

Capture: `SessionHistory` is a capability that rewrites the session's JSON
file after every model step, so an interrupted or crashed session still
leaves a complete trace on disk. One file per process for the main agent
(one CLI session = one conversation; later turns rewrite the same file with
the full history), one file per run for subagents (parallel deep dives must
not clobber each other).

Files land in <data_root>/<profile>/state/history/ as
    <started>[-<label>][-<run_id>].json
with shape {"meta": {...}, "messages": [...]} — messages in pydantic-ai's
own serialization format, so they can be replayed or post-processed with
`ModelMessagesTypeAdapter` later.

View:
    bizharness history --profile PATH            # list sessions
    bizharness history --profile PATH <name>     # render one as a trace
`<name>` may be a filename, a unique prefix, or an index from the listing
(1 = most recent).
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic_ai.capabilities.abstract import AbstractCapability
from pydantic_ai.messages import ModelMessagesTypeAdapter



def _jsonable_messages(messages: list[Any]) -> Any:
    """Serialize messages with full fidelity, degrading instead of failing.

    `ModelMessagesTypeAdapter` gives the round-trippable tagged format, but
    tool-return metadata can carry arbitrary objects (code mode stores nested
    `ToolCallPart`s there). A history write must never take down the run, so
    anything unserializable degrades to its repr.
    """
    try:
        return ModelMessagesTypeAdapter.dump_python(
            messages, mode="json", fallback=repr
        )
    except Exception:
        from pydantic_core import to_jsonable_python

        return to_jsonable_python(messages, serialize_unknown=True, fallback=repr)


class SessionHistory(AbstractCapability):
    """Persist the full message history to a JSON file after every model step."""

    def __init__(
        self,
        directory: Path,
        label: str = "",
        per_run: bool = False,
        meta: dict[str, Any] | None = None,
    ):
        super().__init__()
        self._directory = directory
        self._label = label
        self._per_run = per_run
        # Extra facts stamped into every file: the engine passes the profile
        # hash, so a finished report can be traced back to the exact tool and
        # instruction versions that produced it.
        self._meta = dict(meta or {})
        self._started = datetime.now(timezone.utc)
        self._stamp = self._started.strftime("%Y%m%d-%H%M%S")

    def _path(self, run_id: str | None) -> Path:
        parts = [self._stamp]
        if self._label:
            parts.append(self._label)
        if self._per_run and run_id:
            parts.append(run_id[:8])
        return self._directory / ("-".join(parts) + ".json")

    async def after_model_request(self, ctx, *, request_context, response):
        try:
            self._write(ctx, response)
        except Exception as exc:  # never let history capture break the run
            print(f"[history] write failed: {exc!r}", file=sys.stderr)
        return response

    def _write(self, ctx, response) -> None:
        messages = [*ctx.messages, response]
        payload = {
            "meta": {
                "started_at": self._started.isoformat(),
                "label": self._label or "main",
                "model": getattr(ctx.model, "model_name", None) or str(ctx.model),
                "run_id": ctx.run_id,
                "written_at": datetime.now(timezone.utc).isoformat(),
                "message_count": len(messages),
                **self._meta,
            },
            "messages": _jsonable_messages(messages),
        }
        path = self._path(ctx.run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
        os.replace(tmp, path)  # atomic: a reader never sees a torn file


# ======================================================================
# viewer
# ======================================================================

_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def _compact(value: Any, limit: int = 400) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    text = " ".join(text.split())
    if len(text) > limit:
        half = limit // 2
        text = f"{text[:half]} … {text[-half:]} ({len(text)} chars)"
    return text


def _first_user_prompt(messages: list[dict]) -> str:
    for message in messages:
        for part in message.get("parts", []):
            if part.get("part_kind") == "user-prompt":
                content = part.get("content")
                if isinstance(content, str):
                    return content
                if isinstance(content, list):
                    return " ".join(c for c in content if isinstance(c, str))
    return ""


def _list_sessions(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)


def _print_listing(directory: Path) -> None:
    sessions = _list_sessions(directory)
    if not sessions:
        print(f"no sessions in {directory}")
        return
    for i, path in enumerate(sessions, 1):
        try:
            data = json.loads(path.read_text())
            meta = data.get("meta", {})
            prompt = _first_user_prompt(data.get("messages", []))
        except Exception:
            meta, prompt = {}, "(unreadable)"
        age = time.strftime("%Y-%m-%d %H:%M", time.localtime(path.stat().st_mtime))
        label = meta.get("label", "?")
        count = meta.get("message_count", "?")
        snippet = (prompt[:80] + "…") if len(prompt) > 80 else prompt
        print(f"{i:3}. {path.name}  {_DIM}{age}  {label}  {count} msgs{_RESET}  {snippet}")


def _resolve(directory: Path, name: str) -> Path | None:
    sessions = _list_sessions(directory)
    if name.isdigit() and 1 <= int(name) <= len(sessions):
        return sessions[int(name) - 1]
    matches = [p for p in sessions if p.name == name] or [
        p for p in sessions if p.name.startswith(name)
    ]
    if len(matches) == 1:
        return matches[0]
    if matches:
        print(f"ambiguous: {', '.join(p.name for p in matches)}", file=sys.stderr)
    return None


def _render_part(part: dict) -> None:
    kind = part.get("part_kind")
    if kind == "user-prompt":
        print(f"\n{_BOLD}USER{_RESET} {_compact(part.get('content'), 2000)}")
    elif kind == "text":
        print(f"\n{_BOLD}ASSISTANT{_RESET} {part.get('content', '')}")
    elif kind == "thinking":
        print(f"\n{_DIM}thinking: {_compact(part.get('content'), 300)}{_RESET}")
    elif kind == "tool-call":
        args = part.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except Exception:
                pass
        if isinstance(args, dict) and isinstance(args.get("code"), str):
            print(f"\n▶ {_BOLD}{part.get('tool_name')}{_RESET} code:")
            print(args["code"])
        else:
            print(f"\n▶ {_BOLD}{part.get('tool_name')}{_RESET} {_compact(args)}")
    elif kind == "tool-return":
        print(f"◀ {part.get('tool_name')} → {_compact(part.get('content'))}")
    elif kind == "retry-prompt":
        print(f"↺ {_BOLD}retry{_RESET} {_compact(part.get('content'))}")
    # instruction/system parts are noise when reading a trace; skip silently


def _print_session(path: Path) -> None:
    data = json.loads(path.read_text())
    meta = data.get("meta", {})
    print(
        f"{_BOLD}{path.name}{_RESET}  model={meta.get('model')} "
        f"label={meta.get('label')} started={meta.get('started_at')}"
    )
    for message in data.get("messages", []):
        for part in message.get("parts", []):
            _render_part(part)


def main(directory: Path, name: str | None = None) -> None:
    if not name:
        _print_listing(directory)
        return
    path = _resolve(directory, name)
    if path is None:
        print(f"no session matching {name!r}; run with no args to list", file=sys.stderr)
        raise SystemExit(1)
    _print_session(path)
