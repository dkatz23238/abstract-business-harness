"""python_analysis — full-CPython analysis over files saved in the workspace.

The execution split of this agent (see engine.py):
  * the code tool     — fetch data (restricted Monty sandbox; the profile's
                        data tools only exist there; saves big payloads to
                        data/).
  * python_analysis   — THIS tool: analyze what was saved. Real CPython from
                        the engine's own venv (pandas included), cwd = the
                        thread's workspace, so saved files are at data/.
  * write_file        — author deliverables (HTML reports); not this tool.
  * shell             — everything else (one-off commands, file inspection).

A dedicated tool instead of `run_command("python …")` for two reasons:
models routinely mangle heredoc/quoting when smuggling Python through a
shell string, and the timeline card should say what the step *is* —
"python_analysis" — not show a cryptic shell invocation.

The description the model reads is a profile-rendered template
(`core_instructions.PYTHON_ANALYSIS_DESCRIPTION`), because it has to name
the profile's own code tool.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from pydantic_ai.toolsets import FunctionToolset


def build_python_analysis_toolset(
    workspace: Path,
    timeout: float = 1800.0,
    *,
    description: str | None = None,
) -> FunctionToolset:
    """Native (non-sandboxed) toolset with the single python_analysis tool."""
    ts = FunctionToolset(max_retries=3)

    def python_analysis(script: str) -> str:
        """Run a Python script with full CPython in the thread's workspace."""
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return f"error: script exceeded the {timeout:.0f}s time limit"
        out = proc.stdout.strip()
        err = proc.stderr.strip()
        if proc.returncode != 0:
            parts = [f"error: script exited with code {proc.returncode}"]
            if err:
                parts.append(err)
            if out:
                parts.append(f"stdout before failure:\n{out}")
            return "\n".join(parts)
        if not out:
            return "(script ran fine but printed nothing — print() your results)"
        return f"{out}\n\n[stderr]\n{err}" if err else out

    ts.add_function(func=python_analysis, takes_ctx=False, description=description)
    return ts
