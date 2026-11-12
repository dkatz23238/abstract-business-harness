"""Admin HTTP: admin-role gate, file GET/PUT, skill pills."""

from __future__ import annotations

from conftest import as_user


def test_admin_needs_a_token_and_the_admin_role(admin_client):
    client = admin_client()
    assert client.get("/admin/profile", headers={"Authorization": ""}).status_code == 401
    assert client.get("/admin/profile", headers={"Authorization": "Bearer nope"}).status_code == 401
    key = as_user(client, "bob")
    r = client.get("/admin/profile", headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 403
    assert r.json()["detail"] == "admin role required"
    assert client.get("/admin/profile").status_code == 200


def test_admin_get_files_and_put_instructions(admin_client, tmp_path):
    client = admin_client()
    dest = tmp_path / "profile"
    r = client.get("/admin/profile")
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

    r = client.get("/admin/profile/instructions.md")
    assert r.status_code == 200
    original = r.text
    assert original
    assert "{{ core." in original

    r = client.post("/admin/profile/render-instructions", content=original)
    assert r.status_code == 200, r.text
    rendered = r.text
    assert "{{ core." not in rendered
    assert "data_code" in rendered
    assert "YOUR EXECUTION SURFACES" in rendered

    r = client.post(
        "/admin/profile/render-instructions", content="Hello {{ core.not_a_block }}\n"
    )
    assert r.status_code == 422
    assert r.json()["problems"]

    r = client.put(
        "/admin/profile/instructions.md", content=original + "\n<!-- admin test -->\n"
    )
    assert r.status_code == 200, r.text
    assert "<!-- admin test -->" in (dest / "instructions.md").read_text()
    history = list((dest / "_history").glob("*_instructions.md"))
    assert history


def test_admin_skill_load_pills(admin_client, tmp_path):
    client = admin_client()
    dest = tmp_path / "profile"
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
    r = client.post("/admin/profile/reload")
    assert r.status_code == 200, r.text
    skills = {s["name"]: s["load"] for s in client.get("/admin/profile").json()["skills"]}
    assert skills == {"always-on": "inline", "on-demand": "deferred", "orphan": "unused"}
