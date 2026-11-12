# Upgrading from 0.1 to 0.2

0.2 replaces the single shared API key and the separate admin token with
user accounts. Each user has a password (web UI), an API key (scripts), a
role, and a visibility, and their own data folder. HTTP URLs and request
bodies are unchanged, so scripts keep working with the old key.

Plan for about ten minutes per deployment, plus a short restart.

## 1. Write down what you run now

Note the image tag in use (for example `sha-a3ff526`). That is your rollback
target. Also note where the data root is: `harness-data/` or `data/` in the
profile mount for Docker, `--data-root` otherwise.

## 2. Back up the data root

Stop the server, then copy the whole data root:

```bash
docker compose down        # or stop `abstract serve`
cp -a /path/to/harness-data /path/to/harness-data.bak-0.1
```

The first 0.2 start moves folders around (step 5). The backup is the
simplest way back.

## 3. Change the environment

| 0.1 | 0.2 |
| --- | --- |
| `HARNESS_UI_TOKEN` | remove. The old `api_key` file becomes admin's key |
| `HARNESS_ADMIN_TOKEN` | remove. `/admin` now takes an admin user's key or session |
| | add `HARNESS_ADMIN_PASSWORD` (8+ characters, required on the first start) |
| | optional `HARNESS_ADMIN_API_KEY`: admin's key on the first start, instead of the old one |

If the old variables are still set, the server prints a warning and ignores
them.

`HARNESS_ADMIN_PASSWORD` is read only when `<data-root>/users.json` doesn't
exist. After the first start you can remove it from the environment and
change the password in the UI.

## 4. Point at the new image

Use `dkatz23238/abstract-business-harness:0.2.0`, or `:0.2` to pick up patch
releases automatically.

## 5. Start and read the log

The first start prints something like:

```
[abstract] admin api key: kept from /harness-profile/harness-data/api_key
[abstract] removed /harness-profile/harness-data/api_key (now the admin API key)
[abstract] migrate: moved …/managro/state -> …/managro/users/admin/state
[abstract] migrate: moved …/managro/workspaces -> …/managro/users/admin/workspaces
[abstract] migrate: wrote …/managro/layout_version
```

- All existing threads, workspaces and memory now belong to `admin`.
- The old API key keeps working as admin's key, and the old `api_key` file
  is removed. If there was no old key, a new one is printed once as
  `admin api key (shown once): bh_…`. Save it.
- `<profile>/layout_version` records that the move happened, so later
  starts skip it.

If `HARNESS_ADMIN_PASSWORD` is missing or too short, the server exits
before changing anything on disk.

`GET /healthz` (no auth) returns `{"ok": true, "version": "0.2.0", …}`.
Use it for health checks instead of `/docs`.

## 6. Sign in and add users

Open the UI and sign in as `admin` with the password from step 3. In the
**Users** tab, add one account per person:

- **role** `user`, or `admin` to manage users and edit the profile
- **visibility** `siloed` (own threads only) or `shared` (can also read
  everyone's threads, read-only)

Each new user's API key is shown once. Users change their password and
rotate their key under their name in the header.

## 7. Scripts and integrations

Nothing changes for existing scripts: same URLs, same bodies, and `Bearer`
or `?token=` with the old key. They now act as `admin`, so their threads
show up under admin.

To keep a script's threads separate, create a user for it and switch the
script to that user's key.

Calls that used `HARNESS_ADMIN_TOKEN` against `/admin/*` now need an admin
user's API key. `POST /admin/api-key` is gone. Use `POST /me/api-key` for
your own key, or `POST /admin/users/{name}/api-key` for someone else's.

## 8. Shared memory (profiles with a team notebook)

Memory is now per user by default. If the profile's notebook is meant to be
shared by everyone, add this to `profile.toml` before the first 0.2 start:

```toml
[memory]
scope = "shared"
```

The migration then moves admin's existing memory to `<profile>/shared/memory`.
If you add it later, move `users/admin/state/memory` there by hand.

## Lost the admin password

Run this on the host or in the container, against the same data root:

```bash
docker compose exec harness abstract users reset-password admin \
  --data-root /harness-profile/harness-data
abstract users list --data-root /path/to/harness-data
```

`add`, `rotate-key` and `list` work the same way, without a running server.

## Rolling back

1. Stop the server.
2. Restore the backup: `rm -rf harness-data && mv harness-data.bak-0.1 harness-data`.
3. Put back the old image tag and the old `HARNESS_UI_TOKEN` and
   `HARNESS_ADMIN_TOKEN`.

Threads created on 0.2 are not in the backup. If you need them, copy them
out of `<profile>/users/*/` before restoring.
