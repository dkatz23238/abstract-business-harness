"""Assembly of the long-horizon analysis agent.

This is the engine: the capabilities, the context hygiene and the execution
split that make a multi-hour analysis session survive. Everything about a
particular business — the data tools, the domain prompt, the reference
documents, the model, the credentials — comes from a `Profile`.

Capabilities (pydantic-ai-harness):
  * Planning        — to-do lists for multi-step analyses, persisted to
                      sqlite under the profile's data dir so a plan survives
                      across sessions.
  * Memory          — durable notes (file-backed), shared by every
                      conversation of the profile. Methodology only: how to
                      analyze, which sources reconcile, caveats. Never data
                      or financials (the guidance text enforces the policy).
  * FileSystem      — the thread's workspace: scratch pad + where the HTML
                      report lands.
  * Shell           — one-off commands and file inspection in the workspace.
  * CodeMode        — the profile's code tool (renamed from run_code in
                      harness_patches): ONLY the profile's data tools are
                      sandboxed. The model writes Python that calls them, and
                      their results arrive complete (exempt from spill), so
                      big payloads are fetched once, saved through the /work
                      mount, and summarized without flooding the context.
                      Control tools (planning, memory, files, shell) stay
                      native tool calls — sandboxing them (tools='all') made
                      the model unable to find write_plan etc. and it stalled
                      with filler shell/code calls before every task.
  * python_analysis — real CPython (the engine's venv, pandas included) run
                      against the files the code tool saved. The execution
                      split is: fetching data -> code tool; analyzing saved
                      data -> python_analysis; authored deliverables (HTML
                      reports) -> write_file; anything else -> shell.
  * Reference docs  — the profile's inline skills are inlined into the
                      instructions at build time: they are small and always
                      needed, and deferred loading caused wasteful
                      load_capability round-trips.
  * Skills          — the profile's deferred skills stay loadable on demand
                      (harness_patches PATCH 2 lets them be re-loaded after
                      context compaction).
  * ToolOutputLimits + ClearToolResults — context hygiene for long sessions.

There is deliberately NO subagent: an earlier delegate clone had no code
sandbox/filesystem, so the main agent kept delegating tasks it couldn't
perform ("use the saved files only") and dives flailed. One agent doing
sequential dives against its own saved data is simpler and reliable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic_ai import Agent
from pydantic_ai_harness.code_mode import CodeMode
from pydantic_ai_harness.compaction import ClampOversizedMessages, ClearToolResults
from pydantic_ai_harness.filesystem import FileSystem
from pydantic_ai_harness.memory import FileStore, Memory
from pydantic_ai_harness.planning import Planning, SqlitePlanStore
from pydantic_ai_harness.shell import Shell
from pydantic_ai_harness.skills import Skills
from pydantic_ai_harness.tool_output_limits import Band, Spill, ToolOutputLimits
from pydantic_monty import MountDir

from . import core_instructions, harness_patches, plugins
from .analysis_tool import build_python_analysis_toolset
from .history import SessionHistory
from .profile import Profile


# Chat Completions (`openai-chat:` / a bare `openai:` string) cannot mix this
# model's reasoning with function tools. The engine always has tools, so every
# OpenAI-shaped spec is built as the Responses API.
_OPENAI_SPEC_PREFIXES = ("openai-chat:", "openai-responses:", "openai:")


def _reasoning_model(model_spec: str):
    """Model + settings that surface the model's reasoning as ThinkingParts.

    OpenAI only returns reasoning (as summaries) over the Responses API, so
    plain "openai:<name>" strings — which resolve to Chat Completions — never
    produce any. Built explicitly, the summaries stream as ThinkingParts,
    which the AG-UI adapter forwards to the frontend as THINKING events and
    the CLI trace prints. Non-OpenAI specs pass through unchanged.
    """
    for prefix in _OPENAI_SPEC_PREFIXES:
        if model_spec.startswith(prefix):
            from pydantic_ai.models.openai import (
                OpenAIResponsesModel,
                OpenAIResponsesModelSettings,
            )

            return (
                OpenAIResponsesModel(model_spec.removeprefix(prefix)),
                OpenAIResponsesModelSettings(openai_reasoning_summary="detailed"),
            )
    return model_spec, None


@dataclass
class BuiltAgent:
    """An agent plus the facts about it the server and UI need."""

    agent: Agent
    profile: Profile
    workspace: Path
    data_tools: frozenset[str]
    instructions: str


def build(
    profile: Profile,
    *,
    instance: str | None = None,
    trace_tools: bool = False,
    reload: bool = False,
) -> BuiltAgent:
    """Assemble the agent for one profile.

    `instance` isolates one chat thread (the web UI passes the AG-UI thread
    id): the workspace becomes workspaces/<instance>/ and the plan store
    becomes per-instance, so parallel chats can't mix assets or clobber each
    other's plan. Memory and session history stay global on purpose — memory
    is cross-session knowledge, and history files are already per run.
    Without `instance` (the CLI), a shared workspace is used.
    """
    # Re-deliver skill instructions on repeated load_capability calls instead
    # of erroring out, rename the sandbox tool after this profile, and bound
    # its wall clock. Idempotent, so CLI and server can both call it.
    harness_patches.apply(
        code_tool_name=profile.code_tool.name,
        purpose=profile.render(profile.code_tool.purpose, where="code_tool.purpose"),
        wall_clock_s=profile.code_tool.wall_clock_s,
    )

    state_dir = profile.state_dir
    if instance:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in instance)
        workspace = profile.workspaces_dir / safe
        plans_db = state_dir / "threads" / safe / "plans.db"
    else:
        workspace = profile.data_dir / "workspace"
        plans_db = state_dir / "plans.db"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "data").mkdir(exist_ok=True)
    (workspace / "reports").mkdir(exist_ok=True)
    plans_db.parent.mkdir(parents=True, exist_ok=True)
    (state_dir / "memory").mkdir(parents=True, exist_ok=True)

    data_toolset, _ctx = plugins.load(profile, workspace=workspace, reload=reload)

    trace_main: list = []
    if trace_tools:
        from .tracing import ToolTrace

        trace_main = [ToolTrace()]

    model, model_settings = _reasoning_model(profile.model.spec)
    instructions = profile.resolve_instructions()
    limits = profile.limits

    agent = Agent(
        model,
        model_settings=model_settings,
        retries=profile.model.retries,
        instructions=instructions,
        # python_analysis is NOT in CodeMode's tools list, so it stays a
        # native tool — full CPython over the files the code tool saved.
        toolsets=[
            data_toolset,
            build_python_analysis_toolset(
                workspace,
                timeout=limits.analysis_timeout_s,
                description=profile.render(
                    core_instructions.PYTHON_ANALYSIS_DESCRIPTION,
                    where="python_analysis description",
                ),
            ),
        ],
        capabilities=[
            *trace_main,
            # Full conversation persisted as JSON after every model step, so
            # even a crashed session leaves a complete trace. View with:
            #   harness history
            SessionHistory(state_dir / "history", meta={"profile_hash": profile.hash}),
            Planning(store=SqlitePlanStore(str(plans_db))),
            Memory(store=FileStore(state_dir / "memory"), guidance=profile.memory_guidance),
            *(
                [Skills(profile.skills_dir, include=frozenset(profile.skills.deferred))]
                if profile.skills.deferred
                else []
            ),
            FileSystem(root_dir=workspace),
            Shell(cwd=workspace, default_timeout=180.0),
            # The workspace is mounted read-write at /work so the code tool
            # can save raw API JSON to /work/data/ and python_analysis scripts
            # (whose cwd is the same directory) can read it back.
            CodeMode(
                # Sandbox ONLY the profile's data tools. With the default
                # 'all', planning/memory/file tools vanished from the native
                # tool list, and the model — told to "plan first" but unable
                # to find write_plan — stalled with dozens of filler
                # start_command/run_code calls before starting real work.
                tools=sorted(data_toolset.tools),
                # Monty is a restricted interpreter and models occasionally
                # trip on its gaps (e.g. no %-formatting). The default 3
                # retries killed a whole session mid-analysis; failed
                # snippets are cheap, dead sessions are not.
                max_retries=profile.code_tool.max_retries,
                mount=MountDir(
                    virtual_path=profile.code_tool.mount_path,
                    host_path=str(workspace),
                    mode="read-write",
                ),
                # Big-JSON sessions parse 100+ MB payloads; the 256 MiB
                # default heap is too tight and a burst of API awaits
                # outlives 30 s.
                resource_limits=dict(profile.code_tool.resource_limits),
            ),
            # Reduce only outputs that actually reach the model's context.
            # Nested data calls made inside the code tool MUST be exempt:
            # spilling them hands the sandbox a "[Tool output too large...]"
            # placeholder string instead of data, which silently corrupts
            # every fetch (observed live: paging loops counting 0 rows and
            # re-fetching the same pages at ever-smaller sizes). All data
            # tools are sandboxed, so full payloads stay inside the sandbox;
            # spill applies to the code tool's own return and the native
            # tools that can produce big outputs.
            ToolOutputLimits(
                bands=[Band(limits.spill_tokens, Spill())],
                tool_filter=list(limits.spill_tools),
            ),
            # No single message (giant tool result that dodged the spill, or
            # an oversized tool-call arg) may blow up the context window.
            ClampOversizedMessages(max_part_tokens=limits.clamp_part_tokens),
            ClearToolResults(
                max_tokens=limits.clear_tool_results_tokens,
                keep_pairs=limits.keep_pairs,
                exclude_tools=frozenset({"load_capability"}),
            ),
        ],
    )
    return BuiltAgent(
        agent=agent,
        profile=profile,
        workspace=workspace,
        data_tools=frozenset(data_toolset.tools),
        instructions=instructions,
    )
