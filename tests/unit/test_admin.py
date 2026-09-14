"""Admin HTTP: token gate, file GET/PUT, independent of HARNESS_UI_TOKEN."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from bizharness.config import EngineSettings
from bizharness.profile import load
from bizharness.server import create_app

EXAMPLE = Path(__file__).resolve().parents[2] / "profiles" / "example"


def _client(tmp_path, monkeypatch, *, admin_token, ui_token=None):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "data"))
    dest = tmp_path / "profile"
    shutil.copytree(EXAMPLE, dest)
    settings = EngineSettings(
        data_root=tmp_path / "data",
        admin_token=admin_token,
        ui_token=ui_token,
    )
    profile = load(dest, settings=settings)
    app = create_app(profile, engine=settings, profile_path=dest)
    return TestClient(app), dest


def test_admin_disabled_without_token(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, admin_token=None)
    r = client.get("/admin/profile")
    assert r.status_code == 404


def test_admin_rejects_wrong_token(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, admin_token="secret")
    r = client.get("/admin/profile")
    assert r.status_code == 401
    r = client.get("/admin/profile", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_admin_get_files_and_put_instructions(tmp_path, monkeypatch):
    client, dest = _client(tmp_path, monkeypatch, admin_token="secret")
    auth = {"Authorization": "Bearer secret"}
    r = client.get("/admin/profile", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == "example"
    assert body["name"] == "Example Agent"
    tools = {t["name"]: t for t in body["tools"]}
    assert "json_placeholder.py" in tools
    assert tools["json_placeholder.py"]["functions"] == 2
    assert tools["json_placeholder.py"]["names"] == ["get_post", "list_posts"]
    entries = {e["name"]: e for e in tools["json_placeholder.py"]["entries"]}
    assert entries["get_post"]["signature"] == "get_post(post_id: int) -> dict"
    assert entries["list_posts"]["signature"] == "list_posts(limit: int = 5) -> list[dict]"
    assert body["skills"] == []
    assert body["instructions_preview"]
    assert "{{" not in body["instructions_preview"]
    assert body["instructions_preview"].endswith("…") or len(body["instructions_preview"]) <= 240
    assert body["instructions_chars"] > 0

    r = client.get("/admin/profile/instructions.md", headers=auth)
    assert r.status_code == 200
    original = r.text
    assert original
    assert "{{ core." in original

    r = client.post(
        "/admin/profile/render-instructions",
        headers=auth,
        content=original,
    )
    assert r.status_code == 200, r.text
    rendered = r.text
    assert "{{ core." not in rendered
    assert "data_code" in rendered
    assert "YOUR EXECUTION SURFACES" in rendered

    r = client.post(
        "/admin/profile/render-instructions",
        headers=auth,
        content="Hello {{ core.not_a_block }}\n",
    )
    assert r.status_code == 422
    assert r.json()["problems"]

    r = client.put(
        "/admin/profile/instructions.md",
        headers=auth,
        content=original + "\n<!-- admin test -->\n",
    )
    assert r.status_code == 200, r.text
    assert "<!-- admin test -->" in (dest / "instructions.md").read_text()
    history = list((dest / "_history").glob("*_instructions.md"))
    assert history


def test_admin_skips_ui_token(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch, admin_token="admin", ui_token="ui")
    r = client.get("/profile")
    assert r.status_code == 401
    r = client.get("/admin/profile", headers={"Authorization": "Bearer admin"})
    assert r.status_code == 200


def test_admin_skill_load_pills(tmp_path, monkeypatch):
    client, dest = _client(tmp_path, monkeypatch, admin_token="secret")
    auth = {"Authorization": "Bearer secret"}
    for name, body in {
        "always-on": "# Always\n",
        "on-demand": "# Demand\n",
        "orphan": "# Orphan\n",
    }.items():
        folder = dest / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(f"---\nname: {name}\n\ndescription: x\n---\n\n{body}")
    (dest / "profile.toml").write_text(
        (dest / "profile.toml").read_text()
        + '\n[skills]\ninline = ["always-on"]\ndeferred = ["on-demand"]\n'
    )
    r = client.post("/admin/profile/reload", headers=auth)
    assert r.status_code == 200, r.text
    skills = {s["name"]: s["load"] for s in client.get("/admin/profile", headers=auth).json()["skills"]}
    assert skills == {"always-on": "inline", "on-demand": "deferred", "orphan": "unused"}
