"""Loading a profile's Python tool modules at runtime.

A profile ships plain `.py` files under `tools/`. Each module that wants to
contribute tools exposes:

    def register(ctx: ToolContext) -> FunctionToolset | Iterable[Callable]

Modules without `register` are shared helpers (an HTTP client, say) that
other modules import. Imports between a profile's own modules work
normally — the whole directory is installed as a synthetic package, so both
`import api_client` and `from . import api_client` resolve.

Loading is deliberately import-by-path rather than an entry-point or plugin
registry: the point of the design is that a trusted developer can drop (or
PUT through the admin API) a new file and have the next run pick it up. That
also means a profile's tools run with the engine's privileges — profiles are
trusted code, reviewed like any other part of the deployment.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic_ai.toolsets import FunctionToolset

from .profile import Profile


# Last successful (or partial) per-module tool inventory, keyed by profile id.
_INVENTORY: dict[str, list[dict]] = {}


class PluginError(Exception):
    """A tool module could not be loaded or produced an unusable tool."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass
class ToolContext:
    """What a tool module gets: its configuration, nothing of the engine's."""

    profile_id: str
    settings: dict[str, Any]  # the [tools.settings] table from profile.toml
    env: dict[str, str]  # resolved environment (declared names only)
    data_dir: Path  # per-profile persistent storage (caches, fixtures)
    workspace: Path | None  # the current thread's workspace, when there is one
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("harness.tools"))

    def require(self, name: str) -> str:
        """Fetch a declared environment value or fail with a clear message."""
        value = self.env.get(name)
        if not value:
            raise KeyError(
                f"{name} is not set for profile {self.profile_id!r}: add it to "
                f"[env] in profile.toml and provide a value (environment, "
                f"admin API, or `harness profile env set`)"
            )
        return value


def _package_name(profile_id: str) -> str:
    return "harness_profile_" + profile_id.replace("-", "_")


def _synthetic_package(profile: Profile) -> ModuleType:
    """A package whose __path__ is the profile's tools/ directory."""
    name = _package_name(profile.id)
    package = ModuleType(name)
    package.__path__ = [str(profile.tools_dir)]  # type: ignore[attr-defined]
    sys.modules[name] = package
    return package


def unload(profile: Profile) -> None:
    """Drop cached modules so the next load re-reads the files from disk."""
    _INVENTORY.pop(profile.id, None)
    prefix = _package_name(profile.id)
    for key in [k for k in sys.modules if k == prefix or k.startswith(prefix + ".")]:
        del sys.modules[key]


def _import_module(profile: Profile, filename: str) -> ModuleType:
    path = profile.tools_dir / filename
    module_name = f"{_package_name(profile.id)}.{path.stem}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise PluginError([f"tools/{filename}: cannot be imported"])
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    # Also expose the bare stem: a module written as a standalone script may
    # `import api_client` without the package prefix.
    sys.modules.setdefault(path.stem, module)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        del sys.modules[module_name]
        raise PluginError([f"tools/{filename}: {type(exc).__name__}: {exc}"]) from exc
    return module


def _collect(result: Any, filename: str, target: FunctionToolset, problems: list[str]) -> None:
    if isinstance(result, FunctionToolset):
        for name, tool in result.tools.items():
            if name in target.tools:
                problems.append(f"tools/{filename}: duplicate tool name {name!r}")
                continue
            target.add_tool(tool)
        return
    if isinstance(result, Iterable):
        for func in result:
            if not callable(func):
                problems.append(f"tools/{filename}: register() returned a non-callable")
                continue
            if func.__name__ in target.tools:
                problems.append(f"tools/{filename}: duplicate tool name {func.__name__!r}")
                continue
            target.add_function(func=func, takes_ctx=False)
        return
    problems.append(
        f"tools/{filename}: register() must return a FunctionToolset or an "
        f"iterable of functions, got {type(result).__name__}"
    )


def _jsonish(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (str, int, float)):
        return value
    return repr(value)


def _type_str(annotation: Any) -> str:
    if annotation is inspect.Parameter.empty:
        return ""
    if isinstance(annotation, str):
        return annotation
    try:
        return inspect.formatannotation(annotation)
    except Exception:
        return str(annotation)


def _format_param(name: str, typ: str, optional: bool, default: Any) -> str:
    piece = name if not typ else f"{name}: {typ}"
    if not optional:
        return piece
    if default is None:
        return f"{piece} = None"
    if isinstance(default, bool):
        return f"{piece} = {'True' if default else 'False'}"
    if isinstance(default, str):
        return f"{piece} = {default!r}"
    return f"{piece} = {default}"


def _function_entry(name: str, tool: Any) -> dict:
    fn = getattr(tool, "function", None)
    if fn is None:
        return {"name": name, "params": [], "returns": "", "signature": f"{name}()"}
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {"name": name, "params": [], "returns": "", "signature": f"{name}()"}

    skip_ctx = bool(getattr(tool, "takes_ctx", False))
    params: list[dict] = []
    pieces: list[str] = []
    for i, (pname, param) in enumerate(sig.parameters.items()):
        if skip_ctx and i == 0:
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        typ = _type_str(param.annotation)
        optional = param.default is not param.empty
        default = _jsonish(param.default) if optional else None
        params.append({"name": pname, "type": typ, "optional": optional, "default": default})
        pieces.append(_format_param(pname, typ, optional, default))
    returns = _type_str(sig.return_annotation)
    signature = f"{name}({', '.join(pieces)})"
    if returns:
        signature += f" -> {returns}"
    return {"name": name, "params": params, "returns": returns, "signature": signature}


def _empty_tool_row(filename: str, *, helper: bool, error: str | None = None) -> dict:
    row: dict = {
        "name": filename,
        "functions": 0,
        "helper": helper,
        "names": [],
        "entries": [],
    }
    if error:
        row["error"] = error
    return row


def load(
    profile: Profile,
    *,
    workspace: Path | None = None,
    reload: bool = False,
) -> tuple[FunctionToolset, ToolContext]:
    """Import the profile's tool modules and merge everything they register.

    `profile.apply_env()` runs first so a module reading `os.environ` at
    import time sees the resolved values.
    """
    missing = profile.missing_env()
    if missing:
        raise PluginError(
            [f"required environment variable {name} is not set" for name in missing]
        )
    profile.apply_env()
    if reload:
        unload(profile)
    _synthetic_package(profile)

    ctx = ToolContext(
        profile_id=profile.id,
        settings=dict(profile.tools.settings),
        env=dict(profile.env),
        data_dir=profile.data_dir,
        workspace=workspace,
    )
    toolset = FunctionToolset(max_retries=1)
    problems: list[str] = []
    inventory: list[dict] = []
    for filename in profile.tools.modules:
        module = _import_module(profile, filename)
        register: Callable | None = getattr(module, "register", None)
        if register is None:
            inventory.append(_empty_tool_row(filename, helper=True))
            continue  # a shared helper module
        before = set(toolset.tools)
        try:
            result = register(ctx)
        except Exception as exc:
            problems.append(f"tools/{filename}: register() raised {type(exc).__name__}: {exc}")
            inventory.append(
                _empty_tool_row(
                    filename,
                    helper=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        _collect(result, filename, toolset, problems)
        added = [name for name in toolset.tools if name not in before]
        inventory.append(
            {
                "name": filename,
                "functions": len(added),
                "helper": False,
                "names": added,
                "entries": [_function_entry(n, toolset.tools[n]) for n in added],
            }
        )

    _INVENTORY[profile.id] = inventory

    for name, tool in toolset.tools.items():
        if not (tool.description or (tool.function.__doc__ if tool.function else None)):
            problems.append(
                f"tool {name!r} has no docstring — the model reads it as the tool's "
                f"description, so it is mandatory"
            )
    if problems:
        raise PluginError(problems)
    if not toolset.tools:
        raise PluginError(["no tools registered: check tools.modules in profile.toml"])
    return toolset, ctx


def inventory(profile: Profile) -> list[dict]:
    """Per-module function counts from the last `load()` of this profile.

    If nothing is cached, try a load (and still return whatever was recorded
    if load raises PluginError).
    """
    cached = _INVENTORY.get(profile.id)
    if cached is not None:
        return cached
    try:
        load(profile)
    except PluginError:
        pass
    if profile.id in _INVENTORY:
        return _INVENTORY[profile.id]
    if not profile.tools_dir.exists():
        return []
    return [
        _empty_tool_row(f.name, helper=True)
        for f in sorted(profile.tools_dir.glob("*.py"))
    ]
