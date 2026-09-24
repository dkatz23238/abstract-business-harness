# Authoring a profile

A **profile** is a directory the engine loads at runtime. It holds everything
that is about *your* business: the prompt, the HTTP (or other) tools, the
reference docs, credentials, and the strings the UI shows. The engine repo
stays generic; keep a real customer profile in its **own private git repo**.

This engine was first built for a **large agro commodity producer**: long
multi-hour analyses over a production-planning system, delivered as HTML
reports. The notes below are the lessons that survived that work, with no
customer data, farm names, seasons, or figures.

## Minimal layout

```
my-profile/
  profile.toml          # required
  instructions.md       # required — domain prompt
  ui.json               # title, placeholder, banner label
  tools/*.py            # each may expose register(ctx)
  skills/<name>/SKILL.md
  memory_guidance.md    # optional
  .env                  # gitignored; loaded by `bizharness serve`
```

Start from the bundled demo or an empty skeleton:

```bash
bizharness profile new ~/my-profile
# or
bizharness profile new ~/my-profile --from profiles/example
```

Then:

```bash
cp ~/my-profile/.env.example ~/my-profile/.env   # if you added one
uv run bizharness profile validate --profile ~/my-profile
uv run bizharness serve --profile ~/my-profile --data-root ~/my-profile/data
```

## `profile.toml`

| Section | What it is |
|---|---|
| `[profile]` | `id`, `name`, `description` |
| `[model]` | `spec` (e.g. `openai:gpt-5.6-luna` or `test`), `request_limit`, optional `effort` (`low`/`medium`/`high`/`xhigh`, default `medium`), optional `[model.prices]` |
| `[code_tool]` | **Rename** the sandbox tool after the data source. A generic `run_code` reads as “any Python here” and snippets drift into the restricted interpreter. `purpose` is optional; wall-clock and heap already have long-horizon defaults. |
| `[skills]` | `inline` (always in the prompt) vs `deferred` (loaded with `load_capability` when needed) |
| `[tools]` | omit to load every `tools/*.py`; helpers without `register` are fine |
| `[env]` | `optional` / `required` names to list in validate; put non-secret defaults in the tool client, not here |
| `[limits]` | omit unless you need tighter spill / timeouts than the engine defaults |
| `[instructions]` | only if `instructions.md` uses `{{ core.* }}` — filler for `data_tool_examples`, `report_detail_hint`, … |
| `[redact]` | optional names to mask in the UI when the page is opened with `?redact=1` |

`ui.json` is what the web UI reads via `GET /profile`: `title`, `placeholder`, `hint`, `data_calls_label`, `data_source_name`.

### Sharing view (`?redact=1`)

Opening the UI with `?redact=1` masks figures in chat, tool output, and HTML reports. It is display-only: the stored thread is unchanged.

`[redact]` adds customer and establishment names to that same mask. Matching ignores capitalization and does not fire inside a longer word. `terms` is either a comma-separated string or a list. A long list (hundreds of names) belongs in a file next to the profile, one name per line or comma-separated:

```toml
[redact]
terms = "Acme Grain, North Elevator"
# or
terms = ["Acme Grain", "North Elevator"]
terms_file = "redact_terms.txt"
```

```text
# redact_terms.txt
Acme Grain
North Elevator
```

## Tools

A module that contributes tools exposes:

```python
def register(ctx) -> FunctionToolset:
    ts = FunctionToolset(max_retries=1)
    def get_thing(id: int) -> dict:
        """One-line description the model sees. Mandatory."""
        ...
    ts.add_function(func=get_thing, takes_ctx=False)
    return ts
```

`ctx.env` is the resolved `[env]` map; `ctx.require("API_KEY")` fails clearly if missing. Values are also injected into `os.environ` before import, so a client that already reads the environment ports with little change.

Put credentials in `[env]`, not in the prompt and not in git. Use `required` only for names that really must be present. If the client accepts several auth modes (API key *or* username/password), leave them `optional` and fail in `register()` when none of the modes is complete — a single required key will reject the other valid modes. The admin API can set values (`PUT /admin/profile/env/{NAME}`) into `<data-root>/<id>/secrets.env` (mode 0600). That store is convenience, not a vault — treat the host as trusted. The web UI’s Admin tab is the same surface: unlock with `HARNESS_ADMIN_TOKEN`, edit files, set env (values never displayed), reload.

## Instructions

Write the **domain** in `instructions.md`: who the user is, the ontology, what “good” looks like, which tools to prefer. Pull engine behaviour in with placeholders so you do not fork it:

```
{{ core.execution_surfaces }}
{{ core.native_tools_note }}
{{ core.act_decisively }}
{{ core.plan }}
{{ core.memory }}
{{ core.code_tool_limits }}
{{ core.honesty }}
{{ core.delivery }}
```

Inline small docs that every session needs (`[skills] inline`). Keep long, specialised mechanics as **deferred** skills so they do not occupy the context window until the task actually needs them.

## Lessons learned (agro production planning)

These are engine/product lessons, not a description of any one company’s books.

1. **Split by purpose, not preference.** Fetching the business system belongs in the renamed sandbox tool; analysis of saved files belongs in `python_analysis` (full CPython); authored deliverables (HTML reports) go through `write_file`. Mixing those three is how sessions stall or corrupt data.

2. **Do not sandbox planning, memory, or files.** If the model cannot see `write_plan`, it will burn tokens on filler shell/code calls before doing any work.

3. **Exempt nested fetches from output spill.** Spilling a nested API result hands the sandbox a placeholder string instead of data. Spill the sandbox tool’s *return* and the native tools that can dump huge files; never the functions called inside the snippet.

4. **Bound wall-clock on the sandbox, including await time.** Interpreter time limits do not count time spent waiting on HTTP. A loop of per-entity calls can run silently until the session is killed. Prefer bulk endpoints; if a loop is unavoidable, chunk it and save each chunk to disk.

5. **The sandbox is not CPython.** Models trip on missing `%`-formatting, `json.dump`, pandas, and `asyncio.gather` of large fetches. Spell the gaps in the prompt (`{{ core.code_tool_limits }}`) and retry by moving processing to `python_analysis`.

6. **Memory is a methodology notebook.** It is global across conversations; workspaces are not. Never store results, rankings, ids, or file paths. Those go stale and become dead links in the next thread.

7. **Show plan step ids wherever the plan is printed.** Auto-generated ids that only appear in `read_plan` cause the model to guess, fail `update_task_statuses`, and rewrite the whole plan.

8. **Two sizes of answer.** A paragraph in chat, or an HTML report. Nothing in between. Business users do not want markdown tables pasted into the thread.

9. **Report numbers at the grain the screens already use.** Engine-internal blends (weighted averages across unlike things) are plumbing. Attribute results to the entities the user already thinks in — not to a single rolled-up figure they never see.

10. **Trust the system’s own rollups** when it has a documented P&L / cashflow / cost structure. Re-summing by hand fights the business logic the rest of the organisation uses.

11. **One agent with the full toolset beats a delegate that cannot fetch or write files.** A subagent without the sandbox will be asked to “use the saved files” and flail.

12. **Keep the customer profile private.** Publish the engine. Point `--profile` at a separate repo. Credentials never belong in either git history.

## Checklist before first serve

- [ ] `bizharness profile validate --profile PATH` is green
- [ ] Required env is set; `profile env-list` shows sources, not values
- [ ] Code tool name is specific to the data source
- [ ] Every tool has a docstring
- [ ] `instructions.md` uses `{{ core.* }}` rather than copying engine policy
- [ ] Profile directory is **not** inside the public engine repo
