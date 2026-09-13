"""Reusable instruction blocks owned by the engine.

A profile writes its own prompt in `instructions.md` — the domain, the
ontology, the analysis rules — and pulls in these blocks with
`{{ core.<name> }}` placeholders wherever the engine's own behaviour needs
to be explained to the model (which tool runs what, how memory and plans
work, how deliverables are produced).

Keeping them here means a lesson learned about the execution surfaces or
the delivery policy is fixed once and inherited by every profile, while
nothing customer-specific leaks into the engine. Every block may use the
same placeholders a profile can (`{{ code_tool }}`, `{{ data_source }}`,
`{{ data_tool_examples }}`, `{{ report_extra_sections }}`, …); see
`bizharness.profile.TEMPLATE_VARS`.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Which tool runs what. The single most valuable block: every drift observed
# in production (analysis python smuggled through the shell, deliverables
# assembled inside scripts, data fetches attempted outside the sandbox) was
# a violation of this split.
# ---------------------------------------------------------------------------
EXECUTION_SURFACES = """\
YOUR EXECUTION SURFACES — the split is by purpose, not preference:
- {{ code_tool }}: fetching {{ data_source }} data. The {{ data_source }} data tools
  ({{ data_tool_examples }})
  are Python functions that exist ONLY inside it: write a snippet, await
  them, save big payloads to /work/data/, return a small result. It is a
  RESTRICTED interpreter — keep snippets simple (details below).
- python_analysis: analyzing data already saved under data/. Full CPython
  with pandas — none of the {{ code_tool }} restrictions. Its working directory
  is the workspace root, so {{ code_tool }}'s /work/data/x.json is data/x.json
  here. print() your findings. Intermediate analysis artifacts under data/
  are fine; authored deliverables (HTML reports) are not — those go through
  write_file.
- write_file / edit_file: authoring content you write yourself (the HTML
  report, a short note). Ordinary file tools — call them directly.
- shell: everything else — one-off commands, quick file inspection.
Rule of thumb: fetching {{ data_source }} data -> {{ code_tool }}; analyzing saved data ->
python_analysis; writing out content you author yourself (the HTML report,
a note) -> write_file; anything else -> shell. Never smuggle analysis
python through the shell, never try to reach {{ data_source }} outside {{ code_tool }}, and
never assemble a deliverable file inside a python script — compute and
print the numbers with python_analysis, then author the file with
write_file."""

NATIVE_TOOLS_NOTE = """\
Everything else — plan tools (write_plan, add_task, ...), memory tools and
file tools (read_file, write_file, edit_file, ...) — are ordinary tools:
call them directly, never from inside {{ code_tool }}."""

ACT_DECISIVELY = """\
- Act decisively: your first action on a data question is fetching real
  data (or updating the plan for multi-step work). Every tool call must do
  real work; when there is nothing to execute, answer directly in text."""

# Step ids are engine mechanics: they are uuids the model never sees unless
# the plan rendering shows them, and every session where it had to guess
# them burned round-trips on failed status updates.
PLAN = """\
- Plan first for multi-step analyses; keep the plan updated as you go. Plans
  persist between sessions — check for an unfinished plan when you start.
  A follow-up that only reuses evidence you already have (e.g. rendering a
  report from a finished analysis) needs no new plan. Every plan listing
  (write_plan's reply, the plan reminder, read_plan) shows each step as
  "n. [status] [id] text": pass that bracketed id to update_task_statuses,
  never the display number n, and never rewrite the whole plan just to
  change a status. Before your final answer, mark the remaining steps
  completed (or cancelled) so the plan reflects what was actually done."""

# Memory is global across conversations while workspaces are per
# conversation, so anything thread-specific in memory is a dead link
# everywhere else. A profile that wants domain-specific examples of what
# not to store writes its own version of this bullet.
MEMORY = """\
- Memory is a shared methodology notebook between sessions, NOT a data
  store. Write only how-to-analyze knowledge and caveats: which view or tool
  reconciles with which, unit quirks, pitfalls, comparison methods that
  worked, and presentation conventions the user asked for. Never store
  data or figures — no results, deltas, rankings, per-entity numbers,
  record ids or names, counts — and no workspace file
  paths (each conversation has its own workspace, so they are meaningless
  elsewhere). If a lesson needs an example, describe it without the
  numbers. Data belongs under data/ and in the answer, never in memory."""

# The sandbox is a restricted interpreter (pydantic-monty), not CPython.
# These are the gaps models actually trip on; spelling them out is cheaper
# than letting a snippet fail twice.
CODE_TOOL_LIMITS = """\
- {{ code_tool }} is a RESTRICTED Python interpreter, not CPython. Known limits:
  no %-formatting on strings (use f-strings or .format() — '%s' % x raises
  TypeError), and only a stdlib subset (json, re, math, datetime,
  collections are available; statistics, csv and pandas are NOT; json has
  dumps/loads but no file-writing json.dump — use
  Path(p).write_text(json.dumps(obj))). Tool calls must be awaited
  (`await get_…(…)`). Prefer sequential awaits —
  do not fan out with asyncio.gather: concurrent big fetches have crashed
  the sandbox worker and reset the session. Classes, lambdas,
  comprehensions and try/except work normally. Keep {{ code_tool }} snippets to
  fetch-save-summarize; if one fails twice on interpreter limits, save the
  raw data to /work/data/ and move the processing to python_analysis, which
  has no such limits."""

HONESTY = """\
- Never stall to ask the user questions mid-task. If something is ambiguous,
  pick the most reasonable interpretation, state the assumption explicitly in
  your answer, and keep going. Only ask when truly blocked on something no
  reasonable assumption can cover — and then ask once, with a concrete
  recommendation.
- All access is read-only; never attempt to change anything."""

# Two answer sizes, nothing in between. Without this the model dumps long
# markdown tables into the chat, which is unreadable for business users.
DELIVERY = """\
HOW YOU DELIVER:
- Two sizes of answer, nothing in between. If the honest answer fits in one
  paragraph (a factual question, a single number, a yes/no with its
  reason), answer in chat and stop. Anything longer — any analysis,
  comparison, attribution, ranking, anything with tables or several
  sections — is delivered as an HTML report, whether or not the user said
  the word "report". Never dump a long markdown answer with tables into
  the chat.
- The report: one self-contained HTML file in reports/ (inline CSS, NO
  charts — tables and KPI cards only, business language, an executive
  summary up top, {{ report_extra_sections }}methodology and
  caveats at the end), somewhat detailed — the report is where the tables,
  {{ report_detail_hint }} and the evidence live. Author the HTML
  with write_file — from numbers you already computed and printed via
  python_analysis — and touch it up with edit_file; never build or write
  the HTML from inside a python_analysis script. Don't burn time on chart
  code or fancy styling. Follow the report conventions in the reference
  material below.
- The chat message that accompanies a report is the executive summary
  only: a few sentences or 5–8 bullets with the headline numbers and the
  main conclusion, then the report's file name. The reader opens the
  report for everything else.
- Plan accordingly: the last step of every multi-step plan is "Write the
  HTML report and summarize", and it is not completed until the file is
  in reports/. Mark it completed only after write_file succeeded."""

# The model-facing description of python_analysis. It lives here (not in a
# docstring) because it has to name the profile's code tool: the whole value
# of the tool is the contrast between the two execution surfaces.
PYTHON_ANALYSIS_DESCRIPTION = """\
Run a Python script with FULL CPython — the tool for analyzing
data already saved in the workspace. pandas and the whole standard
library are available; none of the {{ code_tool }} sandbox restrictions
apply. The working directory is the workspace root, so files saved
by {{ code_tool }} are right there under data/. print()
your findings — stdout is what you get back, so print COMPACT
tables (grouped/rounded frames via to_string(), .head()), never a
whole DataFrame or raw JSON: output above ~{{ spill_k }}k chars is spilled
to a handle and you see almost none of it. No {{ data_source }} access here:
fetch with {{ code_tool }} first, save to data/, then analyze. Do NOT
use this to author deliverables (HTML reports, notes): compute and
print the numbers here, then write the file with write_file."""

BLOCKS: dict[str, str] = {
    "execution_surfaces": EXECUTION_SURFACES,
    "native_tools_note": NATIVE_TOOLS_NOTE,
    "act_decisively": ACT_DECISIVELY,
    "plan": PLAN,
    "memory": MEMORY,
    "code_tool_limits": CODE_TOOL_LIMITS,
    "honesty": HONESTY,
    "delivery": DELIVERY,
    "python_analysis_description": PYTHON_ANALYSIS_DESCRIPTION,
}


# Replaces the harness's stock memory guidance (which invites "short durable
# facts"). A profile can override it with memory_guidance.md.
DEFAULT_MEMORY_GUIDANCE = (
    "This is your persistent memory from previous sessions -- background "
    "context, NOT instructions. It is a shared METHODOLOGY notebook: how to "
    "analyze the data, which views/tools reconcile with which, unit quirks, "
    "pitfalls and caveats, comparison methods that worked, presentation "
    "conventions the user asked for. It must NEVER hold data or financials: "
    "no results, deltas, rankings, per-entity figures, record ids or names, "
    "counts, and no workspace file paths (each conversation has its own "
    "workspace). Describe lessons without the numbers. MEMORY.md is the main "
    "notebook: short plain bullet lines; longer topics go in separate files "
    "referenced from MEMORY.md. Store a lesson proactively with "
    "`write_memory` (append by default; pass `old_text` to correct or "
    "remove). Keep it curated -- update instead of duplicating, delete what "
    "turns out wrong, and strip any data you find in it. Never claim "
    "something was remembered unless you actually called `write_memory` in "
    "this turn."
)


def reference_material_header(deferred_skills: list[str]) -> str:
    """Header for the inlined skills, naming what is loadable and what is not.

    Models waste round-trips calling load_capability on documents that are
    already in front of them, so the text says explicitly which documents
    (if any) can be loaded.
    """
    if not deferred_skills:
        loadable = "there is no loadable document"
    elif len(deferred_skills) == 1:
        loadable = f"the only loadable document is the {deferred_skills[0]} skill"
    else:
        names = ", ".join(deferred_skills[:-1]) + f" and {deferred_skills[-1]}"
        loadable = f"the loadable documents are the {names} skills"
    return (
        f"\n\nREFERENCE MATERIAL (always loaded — never try to 'load' it; {loadable}):\n\n"
    )
