# bizharness

A long-horizon analysis agent **engine**. Domain logic — tools, skills,
instructions, credentials — lives in a **profile directory** that you pass
at runtime. The engine itself has no customer-specific knowledge and is
safe to publish.

```
bizharness serve --profile /path/to/profile --port 8811
```

## Layout

```
bizharness/          engine (this package)
profiles/example/    public demo profile (one dummy HTTP JSON tool)
ui/                  AG-UI frontend; strings come from GET /profile
data/                gitignored per-profile state (threads, memory, workspaces)
```

A private profile is a separate git repo. Point `--profile` at it and
`--data-root` at its `data/` if you want state stored next to the profile.

## Profile contract

```
profile.toml          model, code tool name, skills, tool modules, env, limits
instructions.md       domain prompt; may embed {{ core.* }} engine blocks
memory_guidance.md     optional
skills/<name>/SKILL.md
tools/*.py            each may expose register(ctx) -> FunctionToolset
ui.json               title, placeholder, data_calls_label, data_source_name
```

Environment variables declared in `[env]` resolve as:

1. `<PROFILE_ID>_<NAME>` in the process environment
2. bare `<NAME>` in the process environment
3. `<data-root>/<id>/secrets.env` (0600, gitignored)
4. `[env.defaults]` in profile.toml

Required names missing fail `bizharness profile validate`.

## CLI

```
bizharness serve --profile PATH [--port 8811]
bizharness chat --profile PATH [-p prompt] [-v]
bizharness profile validate --profile PATH
bizharness profile env-list --profile PATH
bizharness profile env-set NAME VALUE --profile PATH
bizharness history --profile PATH [session]
bizharness profile new DIR [--from PATH]
```

Admin HTTP (Bearer `HARNESS_ADMIN_TOKEN`): rewrite tools/skills/instructions,
set env values, reload. Writes are validated before they land; previous files
go to `profiles/<id>/_history/`.

## Web UI

```
cd ui && npm install && npm run dev
```

The UI reads `/profile` for the title and labels. Default API is
`http://localhost:8811` (`VITE_API_URL`).
