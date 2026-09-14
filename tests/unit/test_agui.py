"""AG-UI request handling: validation vs a real (dummy-model) run."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from bizharness.config import EngineSettings
from bizharness.profile import load
from bizharness.server import _run_error_from_sse, create_app

EXAMPLE = Path(__file__).resolve().parents[2] / "profiles" / "example"


def _client(tmp_path, monkeypatch, *, spec: str = "test"):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    dest = tmp_path / "profile"
    shutil.copytree(EXAMPLE, dest)
    text = (dest / "profile.toml").read_text()
    (dest / "profile.toml").write_text(re.sub(r'spec = ".*"', f'spec = "{spec}"', text, count=1))
    settings = EngineSettings(data_root=tmp_path / "data")
    profile = load(dest, settings=settings)
    app = create_app(profile, engine=settings, profile_path=dest)
    return TestClient(app)


def test_incomplete_agui_body_is_422_not_500(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/agui",
        json={
            "threadId": "hello-probe",
            "runId": "hello-probe-1",
            "messages": [{"id": "m1", "role": "user", "content": "hello"}],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    loc = str(detail)
    assert "context" in loc
    assert "forwardedProps" in loc or "forwarded_props" in loc


def test_hello_with_test_model_streams_events(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    r = client.post(
        "/agui",
        json={
            "threadId": "hello-test",
            "runId": "hello-test-1",
            "messages": [{"id": "m1", "role": "user", "content": "hello"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "state": {},
        },
    )
    assert r.status_code == 200
    assert "RUN_STARTED" in r.text
    # TestModel will poke the code tool; a finished or protocol-error stream
    # still proves the detached run produced a complete AG-UI response.
    assert "RUN_FINISHED" in r.text or "RUN_ERROR" in r.text


def test_run_error_from_sse_chunk():
    chunk = (
        'data: {"type":"RUN_STARTED","threadId":"t","runId":"r"}\n\n'
        'data: {"type":"RUN_ERROR","message":"status_code: 400, boom"}\n\n'
    )
    assert _run_error_from_sse(chunk) == "status_code: 400, boom"
    assert _run_error_from_sse("data: {\"type\":\"RUN_STARTED\"}\n\n") is None
