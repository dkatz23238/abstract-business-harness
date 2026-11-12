"""A server on a copy of the example profile, signed in as the bootstrap admin."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from abstract.config import EngineSettings
from abstract.profile import load
from abstract.server import create_app

EXAMPLE = Path(__file__).resolve().parents[2] / "profiles" / "example"
ADMIN_PASSWORD = "admin-password"
ADMIN_KEY = "bh_test_admin_key"


@pytest.fixture(autouse=True)
def _fresh_shutdown_event(monkeypatch):
    """`runs.shutdown` binds to the first event loop that waits on it; each
    TestClient runs its own loop, so every test needs its own event."""
    import asyncio

    from abstract import runs, server

    event = asyncio.Event()
    monkeypatch.setattr(runs, "shutdown", event)
    monkeypatch.setattr(server, "shutdown", event)


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    dest = tmp_path / "profile"

    def _make(
        *,
        spec: str | None = None,
        choices: list[str] | None = None,
        toml_extra: str = "",
        admin_password: str | None = ADMIN_PASSWORD,
        admin_api_key: str | None = ADMIN_KEY,
        ui_dir: Path | None = None,
    ):
        if not dest.exists():
            shutil.copytree(EXAMPLE, dest)
            text = (dest / "profile.toml").read_text()
            if spec is not None:
                model_lines = f'spec = "{spec}"'
                if choices is not None:
                    model_lines += "\nchoices = [" + ", ".join(f'"{c}"' for c in choices) + "]"
                text = re.sub(r'spec = ".*"', model_lines, text, count=1)
            (dest / "profile.toml").write_text(text + toml_extra)
        settings = EngineSettings(
            data_root=tmp_path / "data",
            admin_password=admin_password,
            admin_api_key=admin_api_key,
            ui_dir=ui_dir,
        )
        profile = load(dest, settings=settings)
        return create_app(profile, engine=settings, profile_path=dest)

    return _make


@pytest.fixture
def admin_client(make_app):
    """`make_app` that returns a TestClient carrying the admin's API key."""

    def _client(**kwargs) -> TestClient:
        client = TestClient(make_app(**kwargs))
        client.headers["Authorization"] = f"Bearer {ADMIN_KEY}"
        return client

    return _client


def as_user(client: TestClient, username: str, password: str = "user-password", **fields):
    """Create a user through the admin API; returns their API key."""
    r = client.post(
        "/admin/users", json={"username": username, "password": password, **fields}
    )
    assert r.status_code == 201, r.text
    return r.json()["api_key"]
