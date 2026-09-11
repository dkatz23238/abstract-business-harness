"""Command-line entry: serve, chat, validate, env, history, new profile."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from .config import EngineSettings, load_dotenv
from .profile import ProfileError, delete_secret, load, write_secret


def _engine(args) -> EngineSettings:
    settings = EngineSettings()
    if getattr(args, "data_root", None):
        settings.data_root = Path(args.data_root).expanduser()
    return settings


def _load_profile(args):
    path = Path(args.profile).expanduser()
    load_dotenv(path / ".env")
    return load(path, settings=_engine(args))


def cmd_serve(args) -> None:
    import uvicorn

    from .server import create_app

    profile = _load_profile(args)
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


def cmd_validate(args) -> None:
    from . import plugins

    try:
        profile = _load_profile(args)
    except ProfileError as exc:
        print("INVALID")
        for p in exc.problems:
            print(f"  - {p}")
        raise SystemExit(1)
    missing = profile.missing_env()
    print(f"profile {profile.id}  hash={profile.hash}")
    print(f"  model {profile.model.spec}  code_tool={profile.code_tool.name}")
    print(f"  skills inline={profile.skills.inline}  deferred={profile.skills.deferred}")
    if missing:
        print("  missing required env:")
        for name in missing:
            print(f"    - {name}")
        raise SystemExit(1)
    try:
        toolset, _ = plugins.load(profile)
    except Exception as exc:
        print(f"INVALID  tools: {exc}")
        raise SystemExit(1)
    names = sorted(toolset.tools)
    print(f"  {len(names)} tools: {', '.join(names)}")
    print("OK")


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
