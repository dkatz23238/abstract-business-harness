"""Headless use: provision a user and run a thread over HTTP only, as a script would."""

from __future__ import annotations

from fastapi.testclient import TestClient

from conftest import ADMIN_KEY


def test_provision_run_and_fetch_over_http(make_app, tmp_path):
    client = TestClient(make_app(spec="test"))
    admin = {"Authorization": f"Bearer {ADMIN_KEY}"}

    r = client.post(
        "/admin/users",
        headers=admin,
        json={"username": "script", "password": "script-password", "visibility": "siloed"},
    )
    assert r.status_code == 201, r.text
    key = r.json()["api_key"]
    auth = {"Authorization": f"Bearer {key}"}

    r = client.post(
        "/agui",
        headers=auth,
        json={
            "threadId": "thread-1",
            "runId": "run-1",
            "messages": [{"id": "m1", "role": "user", "content": "Hello"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
            "state": {},
        },
    )
    assert r.status_code == 200
    assert "RUN_STARTED" in r.text
    assert "RUN_FINISHED" in r.text or "RUN_ERROR" in r.text

    assert client.get("/runs/thread-1", headers=auth).json()["active"] is False
    # The test model ends in RUN_ERROR, which saves no transcript; save one as a client would.
    r = client.put(
        "/threads/thread-1",
        headers=auth,
        json={"messages": [{"id": "m1", "role": "user", "content": "Hello"}]},
    )
    assert r.status_code == 200
    threads = client.get("/threads", headers=auth).json()["threads"]
    assert [(t["id"], t["owner"]) for t in threads] == [("thread-1", "script")]
    assert client.get("/threads/thread-1", headers=auth).json()["messages"]

    # A report the agent wrote, fetched the same way as before 0.2.
    workspace = tmp_path / "data" / "example" / "users" / "script" / "workspaces" / "thread-1"
    (workspace / "reports").mkdir(parents=True, exist_ok=True)
    (workspace / "reports" / "summary.html").write_text("<h1>summary</h1>")
    listed = client.get("/workspace/thread-1/files", headers=auth).json()["files"]
    assert "reports/summary.html" in [f["path"] for f in listed]
    raw = client.get("/workspace/raw/thread-1/reports/summary.html", headers=auth)
    assert raw.status_code == 200 and raw.text == "<h1>summary</h1>"
    assert client.get(
        "/workspace/raw/thread-1/reports/summary.html", params={"token": key}
    ).status_code == 200

    # Another siloed user sees none of it; the admin can read it.
    r = client.post(
        "/admin/users", headers=admin, json={"username": "other", "password": "other-password"}
    )
    other = {"Authorization": f"Bearer {r.json()['api_key']}"}
    assert client.get("/threads", headers=other, params={"scope": "all"}).json()["threads"] == []
    r = client.get("/threads/thread-1", headers=other, params={"owner": "script"})
    assert r.status_code == 403
    r = client.get("/threads/thread-1", headers=admin, params={"owner": "script"})
    assert r.status_code == 200 and r.json()["owner"] == "script"
