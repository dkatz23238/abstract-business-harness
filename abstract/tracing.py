"""Compact live tool tracing for verbose mode.

Prints one line when a tool starts (name + arguments) and one when it
returns (duration + result preview), to stderr so the CLI's stdout
rendering is untouched. Payloads are truncated — this is for watching the
agent work, not for capturing data (use LOGFIRE_TOKEN for full traces).
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

from pydantic_ai.capabilities.abstract import AbstractCapability

_DIM = "\x1b[2m"
_BOLD = "\x1b[1m"
_RESET = "\x1b[0m"


def _compact(value: Any, limit: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = repr(value)
    text = " ".join(text.split())
    if len(text) > limit:
        half = limit // 2
        text = f"{text[:half]} … {text[-half:]} ({len(text)} chars)"
    return text


class ToolTrace(AbstractCapability):
    """Prints every tool call with arguments and a result preview."""

    def __init__(self, label: str = "", args_limit: int = 400, result_limit: int = 400):
        super().__init__()
        self._label = f"[{label}] " if label else ""
        self._args_limit = args_limit
        self._result_limit = result_limit
        self._started: dict[str, float] = {}

    def _emit(self, line: str) -> None:
        print(f"{_DIM}{line}{_RESET}", file=sys.stderr, flush=True)

    async def before_tool_execute(self, ctx, *, call, tool_def, args):
        self._started[call.tool_call_id] = time.perf_counter()
        raw = getattr(args, "args_dict", None) or getattr(call, "args", None)
        if isinstance(raw, dict) and isinstance(raw.get("code"), str):
            shown = raw["code"]
            if len(shown) > 1200:
                shown = shown[:1200] + f"\n… ({len(raw['code'])} chars)"
            self._emit(f"{self._label}▶ {_BOLD}{call.tool_name}{_RESET}{_DIM} code:\n{shown}")
        else:
            self._emit(
                f"{self._label}▶ {_BOLD}{call.tool_name}{_RESET}{_DIM} {_compact(raw, self._args_limit)}"
            )
        return args

    async def after_tool_execute(self, ctx, *, call, tool_def, args, result):
        t0 = self._started.pop(call.tool_call_id, None)
        took = f" {time.perf_counter() - t0:.1f}s" if t0 is not None else ""
        self._emit(
            f"{self._label}◀ {call.tool_name}{took} → {_compact(result, self._result_limit)}"
        )
        return result
