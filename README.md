# bizharness

A long-horizon **analysis agent engine**. Domain logic — tools, skills,
instructions, credentials — lives in a **profile directory** you pass at
runtime. This repository has no customer data and is meant to be public.

It was first built for a large agro commodity producer: multi-hour analyses
against an internal planning system, delivered as HTML reports. The engine
is the reusable half of that design. How to write a profile like that one
(lessons included, no confidential names or figures) is in
[docs/authoring-profiles.md](docs/authoring-profiles.md).

```bash
uv sync
uv run bizharness serve --profile profiles/example --port 8811
# other terminal
cd ui && npm install && VITE_API_URL=http://localhost:8811 npm run dev
```

Open http://localhost:5173. The example profile uses pydantic-ai’s `test`
model and a public JSON API — no API keys required.

## Layout

```
bizharness/          engine package (CLI: bizharness)
profiles/example/    public demo profile
docs/                authoring guide
ui/                  AG-UI frontend; strings come from GET /profile
data/                gitignored per-profile state
```

A real customer profile is a **separate private git repo**. Point
`--profile` at it and `--data-root` at its `data/` so threads stay next to
the profile, not in this checkout.

## Profile in one page

```
profile.toml          model, code tool name, skills, tool modules, env, limits
instructions.md       domain prompt; may embed {{ core.* }} engine blocks
memory_guidance.md     optional
skills/<name>/SKILL.md
tools/*.py            each may expose register(ctx) -> FunctionToolset
ui.json               title, placeholder, data_calls_label, data_source_name
```

Environment declared in `[env]` resolves as:

1. `<PROFILE_ID>_<NAME>` in the process environment
2. bare `<NAME>` in the process environment
3. `<data-root>/<id>/secrets.env` (0600, gitignored)
4. `[env.defaults]` in profile.toml

`bizharness profile validate --profile PATH` prints a report and fails on
missing required env or a broken tool module.

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
go to `_history/` under the profile. **Tools are trusted code** — same
privileges as the engine process. Do not expose the admin token.

The web UI has an **Admin** tab that talks to those endpoints. Unlock with
the admin token (stored in this tab’s session storage, not in the Vite
build). Unset `HARNESS_ADMIN_TOKEN` and `/admin` stays 404. `/admin` is
not gated by `HARNESS_UI_TOKEN` — the two tokens are independent.

## License

MIT. See [LICENSE](LICENSE).
