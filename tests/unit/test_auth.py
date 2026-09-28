"""API key: created at startup, required on API routes, rotatable from admin."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

from fastapi.testclient import TestClient

from bizharness.auth import API_KEY_FILENAME, load_or_create_api_key
from bizharness.config import EngineSettings
from bizharness.profile import load
from bizharness.server import create_app

EXAMPLE = Path(__file__).resolve().parents[2] / "profiles" / "example"


def _app(tmp_path, monkeypatch, *, ui_token=None, admin_token="admin"):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("HARNESS_UI_TOKEN", raising=False)
    dest = tmp_path / "profile"
    shutil.copytree(EXAMPLE, dest)
    settings = EngineSettings(
        data_root=tmp_path / "data",
        ui_token=ui_token,
        admin_token=admin_token,
    )
    profile = load(dest, settings=settings)
    return create_app(profile, engine=settings, profile_path=dest)


def test_missing_key_is_rejected_and_file_is_private(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    key = app.state.harness.api_key.value
    path = tmp_path / "data" / API_KEY_FILENAME
    assert path.read_text().strip() == key
    assert key.startswith("bh_")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    client = TestClient(app)
    assert client.get("/profile").status_code == 401
    assert client.get("/profile", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/profile", params={"token": key}).status_code == 200
    assert client.get("/profile", headers={"Authorization": f"Bearer {key}"}).status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/admin/profile").status_code == 401


def test_env_seeds_only_when_file_is_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.delenv("HARNESS_UI_TOKEN", raising=False)
    dest = tmp_path / "profile"
    shutil.copytree(EXAMPLE, dest)

    def build(ui_token: str):
        settings = EngineSettings(
            data_root=tmp_path / "data",
            ui_token=ui_token,
            admin_token=None,
        )
        profile = load(dest, settings=settings)
        return create_app(profile, engine=settings, profile_path=dest)

    assert build("from-env").state.harness.api_key.value == "from-env"
    assert build("changed-env").state.harness.api_key.value == "from-env"


def test_admin_rotate_replaces_the_key(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, ui_token="original")
    client = TestClient(app)
    auth = {"Authorization": "Bearer admin"}
    rotated = client.post("/admin/api-key", headers=auth)
    assert rotated.status_code == 200, rotated.text
    new_key = rotated.json()["api_key"]
    assert new_key != "original"
    assert new_key.startswith("bh_")
    assert (tmp_path / "data" / API_KEY_FILENAME).read_text().strip() == new_key

    assert client.get("/profile", headers={"Authorization": "Bearer original"}).status_code == 401
    assert client.get("/profile", headers={"Authorization": f"Bearer {new_key}"}).status_code == 200
    assert client.post("/admin/api-key").status_code == 401

    reloaded = load_or_create_api_key(tmp_path / "data", seed="ignored")
    assert reloaded.value == new_key


def test_serve_prints_api_key_once(tmp_path):
    """Importing the server with HARNESS_PROFILE set must not print a second key.

    Docker and `bizharness serve` both set that variable, then the CLI builds
    the app that uvicorn actually runs. Startup used to print from the import
    and again from the CLI.
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

            import bizharness.cli as cli

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
    env.pop("HARNESS_UI_TOKEN", None)
    result = subprocess.run(
        [sys.executable, str(script)],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stderr.count("[bizharness] api key:") == 1
    assert result.stdout.count("[bizharness] ui=") <= 1
