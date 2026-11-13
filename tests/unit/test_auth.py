"""Accounts: first-start bootstrap, sessions, access rules, migration from 0.1."""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import textwrap

import pytest
from fastapi.testclient import TestClient

from abstract.users import BootstrapError, UserStore
from conftest import ADMIN_KEY, ADMIN_PASSWORD, EXAMPLE, as_user


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_first_start_needs_the_admin_password_and_touches_nothing(tmp_path, make_app):
    legacy = tmp_path / "data" / "example" / "state" / "threads" / "t1"
    legacy.mkdir(parents=True)
    (tmp_path / "data" / "api_key").write_text("old-key\n")
    with pytest.raises(BootstrapError, match="HARNESS_ADMIN_PASSWORD"):
        make_app(admin_password=None)
    assert legacy.is_dir()
    assert not (tmp_path / "data" / "users.json").exists()
    assert not (tmp_path / "data" / "example" / "users").exists()
    assert (tmp_path / "data" / "api_key").exists()
    with pytest.raises(BootstrapError, match="at least 8"):
        make_app(admin_password="short")


def test_store_is_private_and_password_only_matters_once(tmp_path, make_app):
    make_app()
    path = tmp_path / "data" / "users.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    text = path.read_text()
    assert ADMIN_KEY not in text and ADMIN_PASSWORD not in text
    # Later starts ignore the env var entirely.
    client = TestClient(make_app(admin_password=None, admin_api_key=None))
    assert client.get("/me", headers=_bearer(ADMIN_KEY)).json()["username"] == "admin"


def test_generated_admin_key_is_printed_once(make_app):
    app = make_app(admin_api_key=None)
    key = app.state.harness.bootstrap.generated_admin_key
    assert key and key.startswith("bh_")
    assert TestClient(app).get("/me", headers=_bearer(key)).status_code == 200
    assert make_app(admin_api_key=None).state.harness.bootstrap.generated_admin_key is None


def test_tokens_are_required_except_on_public_routes(admin_client):
    client = admin_client()
    bare = {"Authorization": ""}
    assert client.get("/profile", headers=bare).status_code == 401
    assert client.get("/profile", headers=_bearer("nope")).status_code == 401
    assert client.get("/profile", headers=bare, params={"token": ADMIN_KEY}).status_code == 200
    assert client.get("/profile").status_code == 200
    assert client.get("/openapi.json", headers=bare).status_code == 200
    health = client.get("/healthz", headers=bare).json()
    assert health["ok"] is True and health["version"]


def test_login_session_and_password_change(admin_client):
    client = admin_client()
    as_user(client, "alice", "alice-password")
    bare = {"Authorization": ""}
    r = client.post("/auth/login", json={"username": "alice", "password": "wrong-pass"}, headers=bare)
    assert r.status_code == 401
    r = client.post("/auth/login", json={"username": "Alice", "password": "alice-password"}, headers=bare)
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    assert r.json()["user"]["username"] == "alice"
    assert client.get("/me", headers=_bearer(token)).json()["role"] == "user"

    r = client.put(
        "/me/password",
        json={"current_password": "wrong", "new_password": "new-password"},
        headers=_bearer(token),
    )
    assert r.status_code == 403
    r = client.put(
        "/me/password",
        json={"current_password": "alice-password", "new_password": "new-password"},
        headers=_bearer(token),
    )
    assert r.status_code == 200
    fresh = r.json()["token"]
    assert client.get("/me", headers=_bearer(token)).status_code == 401
    assert client.get("/me", headers=_bearer(fresh)).status_code == 200


def test_login_is_throttled(admin_client):
    client = admin_client()
    as_user(client, "carol")
    for _ in range(5):
        client.post("/auth/login", json={"username": "carol", "password": "nope-nope"})
    r = client.post("/auth/login", json={"username": "carol", "password": "user-password"})
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_api_key_rotation(admin_client):
    client = admin_client()
    key = as_user(client, "dave")
    r = client.post("/me/api-key", headers=_bearer(key))
    new = r.json()["api_key"]
    assert client.get("/me", headers=_bearer(key)).status_code == 401
    assert client.get("/me", headers=_bearer(new)).status_code == 200
    newer = client.post("/admin/users/dave/api-key").json()["api_key"]
    assert client.get("/me", headers=_bearer(new)).status_code == 401
    assert client.get("/me", headers=_bearer(newer)).status_code == 200


def test_user_management_rules(admin_client):
    client = admin_client()
    r = client.post("/admin/users", json={"username": "Bad Name", "password": "x" * 8})
    assert r.status_code == 400 and "invalid username" in r.json()["detail"]
    assert client.post("/admin/users", json={"username": ".hidden", "password": "x" * 8}).status_code == 400
    assert client.post("/admin/users", json={"username": "maria.gomez", "password": "x" * 8}).status_code == 201
    assert client.post("/admin/users", json={"username": "erin", "password": "short"}).status_code == 400
    as_user(client, "erin")
    assert client.post("/admin/users", json={"username": "erin", "password": "x" * 8}).status_code == 400
    assert client.patch("/admin/users/admin", json={"role": "user"}).status_code == 400
    assert client.delete("/admin/users/admin").status_code == 400
    r = client.patch("/admin/users/erin", json={"visibility": "shared", "role": "admin"})
    assert r.json()["visibility"] == "shared" and r.json()["role"] == "admin"
    assert client.patch("/admin/users/ghost", json={"role": "user"}).status_code == 404
    names = [u["username"] for u in client.get("/admin/users").json()["users"]]
    assert names == ["admin", "erin", "maria.gomez"]


def _save(client, thread, headers=None, text="hello"):
    return client.put(
        f"/threads/{thread}",
        json={"messages": [{"id": "m1", "role": "user", "content": text}]},
        headers=headers,
    )


def test_siloed_and_shared_users(admin_client, tmp_path):
    client = admin_client()
    silo = _bearer(as_user(client, "silo"))
    viewer = _bearer(as_user(client, "viewer", visibility="shared"))
    assert _save(client, "t1", silo, "silo's question").status_code == 200

    # The thread lives in silo's folder; another user's t1 is a different thread.
    assert (tmp_path / "data" / "example" / "users" / "silo" / "state" / "threads" / "t1").is_dir()
    assert client.get("/threads/t1", headers=viewer).json()["messages"] == []

    # Siloed users never see other folders.
    _save(client, "admin-thread")
    assert client.get("/threads/admin-thread", headers=silo, params={"owner": "admin"}).status_code == 403
    listed = client.get("/threads", headers=silo, params={"scope": "all"}).json()["threads"]
    assert {t["owner"] for t in listed} == {"silo"}

    # Shared users read everyone's threads, but only read.
    r = client.get("/threads/t1", headers=viewer, params={"owner": "silo"})
    assert r.status_code == 200 and r.json()["title"] == "silo's question"
    listed = client.get("/threads", headers=viewer, params={"scope": "all"}).json()["threads"]
    assert {t["owner"] for t in listed} == {"silo", "admin"}
    assert client.get("/threads", headers=viewer).json()["threads"] == []
    r = client.put(
        "/threads/t1", params={"owner": "silo"}, headers=viewer, json={"messages": []}
    )
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    assert client.delete("/threads/t1", params={"owner": "silo"}, headers=viewer).status_code == 403
    assert client.post("/agui", params={"owner": "silo"}, headers=viewer, json={}).status_code == 403
    assert client.get("/runs/t1", params={"owner": "silo"}, headers=viewer).status_code == 200

    # Admins read everything.
    assert client.get("/threads/t1", params={"owner": "silo"}).status_code == 200
    assert client.get("/threads/t1", params={"owner": "../etc"}).status_code == 400


def test_workspace_files_follow_the_same_rules(admin_client, tmp_path):
    client = admin_client()
    silo = _bearer(as_user(client, "silo"))
    reports = tmp_path / "data" / "example" / "users" / "admin" / "workspaces" / "t1" / "reports"
    reports.mkdir(parents=True)
    (reports / "r.html").write_text("<p>report</p>")
    (reports.parent / ".hidden").write_text("secret")
    (tmp_path / "data" / "outside.txt").write_text("outside")

    files = client.get("/workspace/t1/files").json()["files"]
    assert [f["path"] for f in files] == ["reports/r.html"]
    assert client.get("/workspace/raw/t1/reports/r.html").text == "<p>report</p>"
    assert client.get("/workspace/raw/t1/.hidden").status_code == 404
    assert client.get("/workspace/raw/t1/../../../../outside.txt").status_code == 404
    assert client.get("/workspace/raw/t1/%2e%2e/%2e%2e/%2e%2e/%2e%2e/outside.txt").status_code == 404
    assert client.get("/workspace/raw/t1/reports/r.html", headers=silo).status_code == 404
    r = client.get("/workspace/raw/t1/reports/r.html", headers=silo, params={"owner": "admin"})
    assert r.status_code == 403


def test_migration_moves_old_data_to_admin(tmp_path, make_app):
    data = tmp_path / "data"
    old = data / "example"
    (old / "state" / "threads" / "t1").mkdir(parents=True)
    (old / "state" / "threads" / "t1" / "conversation.json").write_text(
        json.dumps({"title": "old", "messages": [{"role": "user", "content": "old"}]})
    )
    (old / "state" / "memory").mkdir()
    (old / "state" / "memory" / "MEMORY.md").write_text("- a lesson\n")
    (old / "workspaces" / "t1").mkdir(parents=True)
    (old / "secrets.env").write_text("X=1\n")
    (data / "api_key").write_text("legacy-key\n")

    app = make_app(admin_api_key=None, toml_extra='\n[memory]\nscope = "shared"\n')
    notes = app.state.harness.migration_notes
    assert any("moved" in n for n in notes)
    admin = old / "users" / "admin"
    assert (admin / "state" / "threads" / "t1" / "conversation.json").is_file()
    assert (admin / "workspaces" / "t1").is_dir()
    assert (old / "shared" / "memory" / "MEMORY.md").read_text() == "- a lesson\n"
    assert not (admin / "state" / "memory").exists()
    assert (old / "secrets.env").is_file()
    assert (old / "layout_version").read_text().strip() == "2"
    assert not (data / "api_key").exists()

    # The old key is now the admin's key, and the old thread is admin's.
    client = TestClient(app)
    assert client.get("/threads/t1", headers=_bearer("legacy-key")).json()["title"] == "old"
    assert app.state.harness.bootstrap.generated_admin_key is None

    # Second start: nothing to do.
    assert make_app(admin_api_key=None).state.harness.migration_notes == []


def test_cli_users_commands(tmp_path, make_app):
    from abstract.cli import main

    make_app()
    data = str(tmp_path / "data")
    stdin = sys.stdin
    try:
        import io

        sys.stdin = io.StringIO("frank-password\n")
        main(["users", "add", "frank", "--visibility", "shared", "--password-stdin", "--data-root", data])
        sys.stdin = io.StringIO("frank-password-2\n")
        main(["users", "reset-password", "frank", "--password-stdin", "--data-root", data])
    finally:
        sys.stdin = stdin
    store = UserStore(tmp_path / "data")
    store.load()
    assert store.get("frank").visibility == "shared"
    assert store.check_password("frank", "frank-password-2") is not None


def test_serve_logs_startup_once(tmp_path):
    """Importing the server with HARNESS_PROFILE set must not log startup twice.

    Docker and `abstract serve` both set that variable, then the CLI builds
    the app that uvicorn actually runs.
    """
    dest = tmp_path / "profile"
    shutil.copytree(EXAMPLE, dest)
    data = tmp_path / "data"
    script = tmp_path / "serve_once.py"
    script.write_text(
        textwrap.dedent(
            """\
            import uvicorn
            from fastapi.testclient import TestClient

            import abstract.cli as cli

            def fake_run(app, host, port):
                with TestClient(app):
                    pass

            uvicorn.run = fake_run

            class Args:
                pass

            args = Args()
            args.profile = %r
            args.data_root = %r
            args.host = "127.0.0.1"
            args.port = 8811
            cli.cmd_serve(args)
            """
            % (str(dest), str(data))
        )
    )
    env = os.environ.copy()
    env["HARNESS_PROFILE"] = str(dest)
    env["HARNESS_DATA_ROOT"] = str(data)
    env["HARNESS_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    env.pop("HARNESS_ADMIN_API_KEY", None)
    env["HARNESS_UI_TOKEN"] = "stale"
    result = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("admin api key (shown once):") == 1
    assert result.stderr.count("HARNESS_UI_TOKEN is no longer used") == 1

    env.pop("HARNESS_ADMIN_PASSWORD")
    again = subprocess.run(
        [sys.executable, str(script)], env=env, capture_output=True, text=True, check=False
    )
    assert again.returncode == 0, again.stderr
    assert "admin api key" not in again.stderr


def test_serve_without_password_exits_cleanly(tmp_path):
    dest = tmp_path / "profile"
    shutil.copytree(EXAMPLE, dest)
    env = os.environ.copy()
    env.pop("HARNESS_ADMIN_PASSWORD", None)
    env["HARNESS_DATA_ROOT"] = str(tmp_path / "data")
    result = subprocess.run(
        [sys.executable, "-m", "abstract", "serve", "--profile", str(dest)],
        env=env, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 1
    assert "HARNESS_ADMIN_PASSWORD must be set" in result.stderr
