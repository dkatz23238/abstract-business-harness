"""Targeted patches for long-horizon robustness (pydantic-ai-harness 0.30.0).

PATCH 1 — rename `run_code` to the profile's code tool name.

The CodeMode sandbox exists for exactly one purpose: running the profile's
data tools. A generic name ("run_code") reads as "any Python here", which
confused both models (analysis snippets drifted into the restricted Monty
interpreter) and users watching the timeline. The harness hardcodes the name
as module constants, so the rename patches those constants — the name itself
plus every description string that mentions it. The stock strings are kept,
so the patch can be re-applied with a different name (tests, a second
profile in one process) without compounding.

PATCH 2 — re-deliver skill instructions on repeated load_capability calls.

Problem observed in real runs: skill instructions are delivered as the
tool-return text of `load_capability`. On a long task the context gets
compacted, the model loses that text, and it (reasonably) calls
`load_capability` again — but the stock loader refuses with a ModelRetry
("already active"). That refusal can never turn into a success for that
capability id, so a persistent model burns the whole retry budget and the
run dies with UnexpectedModelBehavior.

Patch: when the capability is already active, respond with a normal success
instead of an error. The FIRST re-load re-delivers the full instructions
(recovers from compaction); subsequent re-loads return a short "already
loaded" note. Never a ModelRetry, so the retry budget is untouched — and
never repeated full text, so a model stuck in a load loop can't blow the
context window (observed live: ~200 consecutive re-loads x a 3.5k-token
skill exceeded the model's context before this cap).

The patch replicates the tail of the stock `_load_capability` and therefore
pins to harness 0.30.0 (see pyproject). Revisit on upgrade — newer versions
may fix this upstream.

PATCH 3 — show step ids in the compact plan rendering.

Step ids are auto-generated (uuid hex) when the model omits them in
write_plan, but the stock `render_plan` — used for write_plan's reply AND
for the <plan-reminder> appended to every turn — prints only display
numbers ("1. [~] text"). The only place ids ever appear is read_plan. So in
every observed session the model guessed ids ('1', '2' or invented hashes),
update_task_statuses failed with "Step with id ... not found", and the model
spent two more round-trips on read_plan + retry — or gave up and rewrote
the whole plan with write_plan each time, which is why the UI showed the
same checklist several times. Rendering "n. [~] [id] text" (the read_plan
format) everywhere removes the guesswork; the UI also parses this line
shape to keep its checklist in sync with the real plan state.

PATCH 4 — wall-clock limit on the code tool.

Monty's `max_duration_secs` bounds interpreter time, not the time a snippet
spends awaiting data calls, so a snippet looping hundreds of requests can
run for as long as the network allows with nothing reaching the model or
the UI (observed: an 81-contract loop, ~800 requests, no output until the
session was killed). The patch wraps the sandbox tool call in
`asyncio.wait_for`; on expiry the cancellation propagates through the stock
handler (which resets the REPL session) and the model gets a ModelRetry
explaining what to do. Files the snippet already saved to the mount
survive. The limit comes from the profile's `[code_tool] wall_clock_s`
(deliberately liberal — long analyses are legitimate).
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai import ModelRetry
from pydantic_ai._deferred_capabilities import LoadCapabilityReturn
from pydantic_ai._instructions import resolve_sourced_instructions
from pydantic_ai.messages import InstructionPart, ToolReturn
from pydantic_ai.toolsets import _deferred_capability_loader as _dcl
from pydantic_ai_harness.code_mode import _capability as _cm_capability
from pydantic_ai_harness.code_mode import _toolset as _cm_toolset
from pydantic_ai_harness.planning import _capability as _pl_capability
from pydantic_ai_harness.planning import _toolset as _pl_toolset
from pydantic_ai_harness.planning._types import PlanItem, TaskStatus

# ---------------------------------------------------------------------------
# Patch 1: run_code -> <profile code tool>
# ---------------------------------------------------------------------------

_STOCK_NAME = "run_code"
# The stock description strings, captured before any patching, so every
# apply() recomputes from the original text.
_STOCK_STRINGS = {
    "head": _cm_toolset._RUN_CODE_DESCRIPTION_HEAD,
    "tail": _cm_toolset._RUN_CODE_DESCRIPTION_TAIL,
    "mount": _cm_toolset._MOUNT_LIFETIME_NOTE,
    "discovery": _cm_capability._DISCOVERY_ANNOUNCEMENT_PREFIX,
}

# The current name, read by patch 4's error message.
code_tool_name = _STOCK_NAME


def _apply_code_tool_rename(name: str, purpose: str) -> None:
    """Rename the CodeMode tool and fix every description string naming it.

    The harness builds the tool definition from module-level constants on
    every step, so patching the module attributes is enough — dispatch keys
    off the tool object type, not the name.

    `purpose` is prepended to the tool description so the tool itself says
    what it is for (the functions catalog follows below it).
    """
    global code_tool_name
    code_tool_name = name
    _cm_toolset._RUN_CODE_TOOL_NAME = name
    head = _STOCK_STRINGS["head"]
    if purpose:
        head = purpose + "\n\n" + head
    _cm_toolset._RUN_CODE_DESCRIPTION_HEAD = head.replace(_STOCK_NAME, name)
    _cm_toolset._RUN_CODE_DESCRIPTION_TAIL = _STOCK_STRINGS["tail"].replace(_STOCK_NAME, name)
    _cm_toolset._MOUNT_LIFETIME_NOTE = _STOCK_STRINGS["mount"].replace(_STOCK_NAME, name)
    _cm_capability._DISCOVERY_ANNOUNCEMENT_PREFIX = _STOCK_STRINGS["discovery"].replace(
        _STOCK_NAME, name
    )


# ---------------------------------------------------------------------------
# Patch 2: load_capability re-delivery
# ---------------------------------------------------------------------------

_original_load_capability = _dcl.DeferredCapabilityLoaderToolset._load_capability

# capability ids that already got one full re-delivery (per loader instance)
_REDELIVERED_ATTR = "_harness_redelivered_ids"


async def _load_capability_redeliver(self, tool_args: dict[str, Any], ctx) -> ToolReturn:
    capability_id = tool_args.get("id")
    capability = ctx.capabilities.get(capability_id)
    if capability is not None and capability_id in ctx.active_capability_ids:
        redelivered: set = getattr(self, _REDELIVERED_ATTR, None) or set()
        if capability_id in redelivered:
            return ToolReturn(
                return_value={
                    "status": (
                        f"'{capability_id}' is already loaded and its instructions are "
                        "already available in this conversation. Do not load it again; "
                        "proceed with the task."
                    )
                }
            )
        redelivered.add(capability_id)
        setattr(self, _REDELIVERED_ATTR, redelivered)
        parts = await resolve_sourced_instructions(
            capability._collect_instructions(),  # pyright: ignore[reportPrivateUsage]
            ctx,
        )
        parts.extend(await self._collect_owned_toolset_instructions(capability_id, ctx))
        instructions_text = InstructionPart.join(parts)
        result: LoadCapabilityReturn = (
            {"instructions": instructions_text} if instructions_text is not None else {}
        )
        tools = sorted(
            name for name, tool_def in ctx.tools.items() if tool_def.capability_id == capability_id
        )
        return ToolReturn(return_value=result, tools=tools or None)
    return await _original_load_capability(self, tool_args, ctx)


# ---------------------------------------------------------------------------
# Patch 3: step ids in the compact plan rendering
# ---------------------------------------------------------------------------


def _render_plan_with_ids(items: list[PlanItem]) -> str:
    """Stock render_plan plus the step id — the same line shape read_plan uses."""
    if not items:
        return "No plan yet."
    lines = [
        f"{index}. {_pl_toolset.status_icon(item.status)} [{item.id}] {item.content}"
        for index, item in enumerate(items, 1)
    ]
    completed = sum(1 for item in items if item.status is TaskStatus.completed)
    lines.append(f"({completed}/{len(items)} completed)")
    return "\n".join(lines)


def _apply_plan_ids() -> None:
    # Both modules bound the name at import time (`from ._toolset import
    # render_plan`), so each binding is replaced.
    _pl_toolset.render_plan = _render_plan_with_ids
    _pl_capability.render_plan = _render_plan_with_ids


# ---------------------------------------------------------------------------
# Patch 4: wall-clock limit on the code tool
# ---------------------------------------------------------------------------

# Seconds; None disables the guard. Set from the profile in engine.build.
_code_wall_clock: float | None = 1800.0

_original_code_mode_call_tool = _cm_toolset.CodeModeToolset.call_tool


def set_code_wall_clock(seconds: float | None) -> None:
    global _code_wall_clock
    _code_wall_clock = seconds if seconds and seconds > 0 else None


async def _call_tool_with_wall_clock(self, name: str, tool_args: dict[str, Any], ctx, tool) -> Any:
    limit = _code_wall_clock
    if limit is None or not isinstance(tool, _cm_toolset._RunCodeTool):
        return await _original_code_mode_call_tool(self, name, tool_args, ctx, tool)
    try:
        return await asyncio.wait_for(
            _original_code_mode_call_tool(self, name, tool_args, ctx, tool), timeout=limit
        )
    except asyncio.TimeoutError:
        # The cancellation already ran through call_tool's BaseException
        # branch, which reset the REPL session; nothing else to tear down.
        minutes = limit / 60
        raise ModelRetry(
            f"{code_tool_name} exceeded its {minutes:.0f}-minute wall-clock limit and "
            "the sandbox session was reset (variables are gone; files already written to "
            "/work/data/ are kept). The snippet did too much in one go — typically a loop "
            "of per-entity calls. Continue from the saved files: fetch in bulk where a "
            "bulk tool or a filtered view exists, otherwise process at most ~15 entities "
            "per snippet, saving each chunk to its own file."
        ) from None


def _apply_code_wall_clock() -> None:
    if _cm_toolset.CodeModeToolset.call_tool is _call_tool_with_wall_clock:
        return
    _cm_toolset.CodeModeToolset.call_tool = _call_tool_with_wall_clock


def apply(
    *,
    code_tool_name: str = _STOCK_NAME,
    purpose: str = "",
    wall_clock_s: float | None = None,
) -> None:
    _apply_code_tool_rename(code_tool_name, purpose)
    _dcl.DeferredCapabilityLoaderToolset._load_capability = _load_capability_redeliver
    _apply_plan_ids()
    set_code_wall_clock(wall_clock_s)
    _apply_code_wall_clock()
