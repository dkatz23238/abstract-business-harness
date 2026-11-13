# Changelog

Versions follow [semver](https://semver.org). Each release is a `vX.Y.Z`
git tag and the Docker image tags `X.Y.Z`, `X.Y`, and `latest`.

## 0.2.1

### Changed

- Usernames may contain dots (`maria.gomez`). They still start with a letter
  or digit.

### Fixed

- Users tab: errors no longer vanish right after they appear. Errors from a
  dialog (add user, reset password, delete) show in that dialog, and the
  add-user form says what is wrong with a name or password while you type.

## 0.2.0

Breaking: see [docs/upgrade-0.2.md](docs/upgrade-0.2.md).

### Added

- User accounts in `<data-root>/users.json`. The `admin` account is created
  from `HARNESS_ADMIN_PASSWORD` on the first start; the server will not
  start without it.
- Roles (`user`, `admin`) and visibility (`siloed`, `shared`). Shared users
  and admins can read other users' threads with `?owner=`, read-only.
- `POST /auth/login` (session tokens, 30 days, ended by a password change),
  `GET /me`, `PUT /me/password`, `POST /me/api-key`.
- `/admin/users` to list, create, update, rotate keys, and delete (with
  `?purge=true` to remove their data).
- Per-user API keys (`bh_…`), stored hashed.
- Login throttling: five failures in five minutes lock that username or
  client address out for five minutes.
- `GET /threads?scope=all`, with an `owner` field on each thread.
- `[memory] scope = "user" | "shared"` in `profile.toml`.
- `abstract users list|add|reset-password|rotate-key` for offline recovery.
  `abstract chat` and `abstract history` take `--user`.
- `GET /healthz` (no auth) with the running version.
- Web UI: username/password sign-in, account dialog, Users tab, other
  users' threads grouped by owner, read-only view.
- Docker images tagged by version. `latest` moves only on releases; `main`
  tracks the branch.

### Changed

- Data layout: `<profile>/users/<user>/{state,workspaces,workspace}`. The
  first start moves the 0.1 layout into `users/admin` and writes
  `<profile>/layout_version`.
- `/admin/*` uses the normal credentials and requires the admin role.
- `/workspace/raw/...` is a documented route (it also takes `?owner=`).
- The package version comes from `abstract/__init__.py`.

### Removed

- `HARNESS_UI_TOKEN` and `HARNESS_ADMIN_TOKEN`. The old `<data-root>/api_key`
  is imported as admin's key on upgrade.
- `POST /admin/api-key`. Use `POST /me/api-key` or
  `POST /admin/users/{name}/api-key`.

## 0.1.0

First release: profile-driven analysis engine, AG-UI server and web UI,
detached runs, workspaces, admin editing, single API key.
