"""Command-line entry: serve, chat, validate, env, history, new profile."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

from .config import EngineSettings, load_dotenv
from .profile import Profile, ProfileError, delete_secret, load, write_secret


def _engine(args) -> EngineSettings:
    settings = EngineSettings()
    if getattr(args, "data_root", None):
        settings.data_root = Path(args.data_root).expanduser()
    return settings


def _load_profile(args):
    path = Path(args.profile).expanduser()
    load_dotenv(path / ".env")
    return load(path, settings=_engine(args))


def _setup_tracing(service_name: str) -> None:
    """With LOGFIRE_TOKEN set, full traces stream to the Logfire web UI."""
    token = os.environ.get("LOGFIRE_TOKEN")
    if not token:
        return
    import logfire

    logfire.configure(
        send_to_logfire=True, token=token, console=False, service_name=service_name
    )
    logfire.instrument_pydantic_ai()


def cmd_serve(args) -> None:
    import uvicorn

    from .server import create_app

    profile = _load_profile(args)
    _setup_tracing(f"bizharness:{profile.id}")
    app = create_app(profile, engine=_engine(args), profile_path=Path(args.profile).expanduser())
    print(
        f"[bizharness] profile={profile.id} model={profile.model.spec} "
        f"data={profile.data_dir} port={args.port}"
    )
    uvicorn.run(app, host=args.host, port=args.port)


def cmd_chat(args) -> None:
    from pydantic_ai.usage import UsageLimits

    from .engine import build

    profile = _load_profile(args)
    _setup_tracing(f"bizharness:{profile.id}")
    built = build(profile, trace_tools=args.verbose)
    limits = UsageLimits(request_limit=profile.model.request_limit)
    print(
        f"[bizharness] profile={profile.id} model={profile.model.spec} "
        f"workspace={built.workspace}"
    )
    if args.prompt:
        result = built.agent.run_sync(args.prompt, usage_limits=limits)
        print(result.output)
        return
    built.agent.to_cli_sync(prog_name=f"bizharness:{profile.id}", usage_limits=limits)


def _console():
    from rich.console import Console

    return Console(stderr=False)


def _fmt_seconds(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:.0f}h"
    if seconds >= 60:
        return f"{seconds / 60:.0f}m"
    return f"{seconds:.0f}s"


def _print_problems(console, problems: list[str]) -> None:
    for problem in problems:
        console.print(f"  [red]×[/] {problem}")


def _print_validate_report(
    profile: Profile,
    tool_names: list[str],
    *,
    elapsed_s: float,
    ok: bool = True,
    env_rows: list[dict] | None = None,
) -> None:
    """Human-readable validate report: sections, checks, wrapping tool list."""
    from rich.padding import Padding
    from rich.text import Text

    console = _console()
    title = profile.name if profile.name != profile.id else profile.id
    console.print()
    console.print(f"[bold]{title}[/]  [dim]{profile.id}[/]")
    console.print(f"[dim]{profile.path}[/]")
    if profile.description:
        console.print(f"[dim]{profile.description}[/]")
    console.print()

    kv = [
        ("hash", profile.hash),
        ("model", profile.model.spec),
        (
            "code tool",
            f"{profile.code_tool.name}  [dim]{_fmt_seconds(profile.code_tool.wall_clock_s)} wall clock[/]",
        ),
        ("requests", f"{profile.model.request_limit} per run"),
        ("data", str(profile.data_dir)),
    ]
    label_w = max(len(k) for k, _ in kv)
    for key, value in kv:
        console.print(f"  [dim]{key:<{label_w}}[/]  {value}")

    if profile.skills.inline or profile.skills.deferred:
        console.print()
        console.print("  [bold]Skills[/]")
        if profile.skills.inline:
            console.print("    [dim]inline[/]     " + ", ".join(profile.skills.inline))
        if profile.skills.deferred:
            console.print("    [dim]deferred[/]   " + ", ".join(profile.skills.deferred))

    rows = env_rows if env_rows is not None else profile.env_status()
    if rows:
        console.print()
        console.print("  [bold]Environment[/]")
        name_w = max(len(r["name"]) for r in rows)
        for row in rows:
            flag = "required" if row["required"] else "optional"
            if row["set"]:
                mark, state = "[green]✓[/]", "[green]set[/]"
            elif row["required"]:
                mark, state = "[red]×[/]", "[red]unset[/]"
            else:
                mark, state = "[dim]·[/]", "[dim]unset[/]"
            console.print(
                f"    {mark} {row['name']:<{name_w}}  [dim]{flag:<8}[/] {state}  [dim]{row['source']}[/]"
            )

    if tool_names:
        console.print()
        console.print(f"  [bold]Tools[/]  [dim]{len(tool_names)}[/]")
        listed = Text("  ".join(tool_names), style="cyan")
        console.print(Padding(listed, (0, 0, 0, 4)))
    console.print()
    if ok:
        console.print(f"[green]✓[/] Profile is valid  [dim]{elapsed_s:.2f}s[/]")
        console.print()


def cmd_validate(args) -> None:
    from . import plugins
    from .plugins import PluginError

    console = _console()
    t0 = time.perf_counter()
    try:
        profile = _load_profile(args)
    except ProfileError as exc:
        console.print()
        console.print("[red bold]× Invalid profile[/]")
        _print_problems(console, exc.problems)
        console.print()
        raise SystemExit(1)

    missing = profile.missing_env()
    env_rows = profile.env_status()
    if missing:
        _print_validate_report(
            profile, [], elapsed_s=time.perf_counter() - t0, ok=False, env_rows=env_rows
        )
        console.print("[red bold]× Missing required environment[/]")
        _print_problems(
            console,
            [
                f"{name} is not set (process env, secrets.env, or [env.defaults])"
                for name in missing
            ],
        )
        console.print()
        raise SystemExit(1)

    try:
        toolset, _ = plugins.load(profile)
    except PluginError as exc:
        _print_validate_report(
            profile, [], elapsed_s=time.perf_counter() - t0, ok=False, env_rows=env_rows
        )
        console.print("[red bold]× Tools failed to load[/]")
        _print_problems(console, exc.problems)
        console.print()
        raise SystemExit(1)
    except Exception as exc:
        _print_validate_report(
            profile, [], elapsed_s=time.perf_counter() - t0, ok=False, env_rows=env_rows
        )
        console.print("[red bold]× Tools failed to load[/]")
        _print_problems(console, [f"{type(exc).__name__}: {exc}"])
        console.print()
        raise SystemExit(1)

    names = sorted(toolset.tools)
    _print_validate_report(
        profile, names, elapsed_s=time.perf_counter() - t0, env_rows=env_rows
    )


def cmd_env_list(args) -> None:
    profile = _load_profile(args)
    for row in profile.env_status():
        flag = "required" if row["required"] else "optional"
        state = "set" if row["set"] else "UNSET"
        print(f"{row['name']:24} {flag:8} {state:5}  {row['source']}")


def cmd_env_set(args) -> None:
    profile = _load_profile(args)
    value = sys.stdin.read().rstrip("\n") if args.stdin else args.value
    if value is None:
        print("pass a value or --stdin", file=sys.stderr)
        raise SystemExit(1)
    write_secret(profile, args.name, value)
    print(f"set {args.name} in {profile.secrets_file}")


def cmd_env_delete(args) -> None:
    profile = _load_profile(args)
    if delete_secret(profile, args.name):
        print(f"deleted {args.name}")
    else:
        print(f"{args.name} was not in the secrets file")
        raise SystemExit(1)


def cmd_history(args) -> None:
    from . import history

    profile = _load_profile(args)
    history.main(profile.state_dir / "history", args.name)


def cmd_new(args) -> None:
    dest = Path(args.dest).expanduser()
    if dest.exists():
        print(f"{dest} already exists", file=sys.stderr)
        raise SystemExit(1)
    src = Path(args.from_path).expanduser() if args.from_path else None
    if src:
        shutil.copytree(src, dest, ignore=shutil.ignore_patterns("_history", "__pycache__", "*.pyc"))
        print(f"copied {src} -> {dest}")
        return
    dest.mkdir(parents=True)
    (dest / "tools").mkdir()
    (dest / "skills").mkdir()
    (dest / "profile.toml").write_text(
        f'[profile]\nid = "{dest.name}"\nname = "{dest.name}"\n\n'
        '[model]\nspec = "openai:gpt-5.6-luna"\nrequest_limit = 500\n\n'
        '[code_tool]\nname = "data_code"\n'
        'purpose = "THE ONLY WAY TO FETCH DATA: tools listed below exist only '
        'inside this sandbox. Fetch, save to /work/data/, return a small result."\n\n'
        "[tools]\nmodules = []\n\n[env]\nrequired = []\n"
    )
    (dest / "instructions.md").write_text(
        "You are an analysis agent.\n\n{{ core.execution_surfaces }}\n\n"
        "{{ core.native_tools_note }}\n\nWORKING STYLE:\n{{ core.act_decisively }}\n"
        "{{ core.plan }}\n{{ core.memory }}\n{{ core.code_tool_limits }}\n"
        "{{ core.honesty }}\n\n{{ core.delivery }}\n"
    )
    (dest / "ui.json").write_text(
        '{\n  "title": "Analysis Agent",\n  "data_calls_label": "API calls",\n'
        '  "placeholder": "Ask a question…",\n  "data_source_name": "the data source"\n}\n'
    )
    print(f"created empty profile at {dest}")


def main(argv: list[str] | None = None) -> None:
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument(
        "--profile", default=os.environ.get("HARNESS_PROFILE"), help="profile directory"
    )
    shared.add_argument("--data-root", default=None, help="override HARNESS_DATA_ROOT")

    parser = argparse.ArgumentParser(prog="bizharness", description="Profile-driven analysis agent")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="AG-UI HTTP server", parents=[shared])
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8811)
    serve.set_defaults(func=cmd_serve)

    chat = sub.add_parser("chat", help="CLI chat / one-shot prompt", parents=[shared])
    chat.add_argument("-p", "--prompt")
    chat.add_argument("-v", "--verbose", action="store_true")
    chat.set_defaults(func=cmd_chat)

    val = sub.add_parser("profile", help="profile utilities")
    psub = val.add_subparsers(dest="profile_cmd", required=True)
    v = psub.add_parser("validate", parents=[shared])
    v.set_defaults(func=cmd_validate)
    el = psub.add_parser("env-list", parents=[shared])
    el.set_defaults(func=cmd_env_list)
    es = psub.add_parser("env-set", parents=[shared])
    es.add_argument("name")
    es.add_argument("value", nargs="?")
    es.add_argument("--stdin", action="store_true")
    es.set_defaults(func=cmd_env_set)
    ed = psub.add_parser("env-delete", parents=[shared])
    ed.add_argument("name")
    ed.set_defaults(func=cmd_env_delete)
    nw = psub.add_parser("new")
    nw.add_argument("dest")
    nw.add_argument("--from", dest="from_path", default=None)
    nw.add_argument("--data-root", default=None)
    nw.set_defaults(func=cmd_new)

    hist = sub.add_parser("history", help="list or render session traces", parents=[shared])
    hist.add_argument("name", nargs="?")
    hist.set_defaults(func=cmd_history)

    args = parser.parse_args(argv)
    if args.cmd == "profile" and args.profile_cmd == "new":
        args.func(args)
        return
    if not getattr(args, "profile", None):
        parser.error("--profile is required (or set HARNESS_PROFILE)")
    args.func(args)


if __name__ == "__main__":
    main()
