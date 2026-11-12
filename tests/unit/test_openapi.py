"""OpenAPI document: tagged sections and request bodies for write routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def schema(make_app) -> dict:
    response = TestClient(make_app()).get("/openapi.json")
    assert response.status_code == 200
    return response.json()


def _resolve(schema: dict, node: dict) -> dict:
    ref = node.get("$ref")
    if not ref:
        return node
    name = ref.rsplit("/", 1)[-1]
    return schema["components"]["schemas"][name]


def test_docs_are_grouped_and_name_the_server(schema):
    assert schema["info"]["title"] == "Example Agent AG-UI server"
    assert schema["servers"] == [{"url": "/", "description": "Example Agent"}]
    tag_names = [tag["name"] for tag in schema["tags"]]
    assert "default" not in tag_names
    assert tag_names == ["Profile", "Account", "Runs", "Events", "Threads", "Workspace", "Admin"]
    for path, methods in schema["paths"].items():
        for method, operation in methods.items():
            assert operation.get("tags"), f"{method.upper()} {path} has no tag"
            assert "default" not in operation["tags"]
            assert operation["summary"]


def test_post_and_put_payloads_are_documented(schema):
    agui = schema["paths"]["/agui"]["post"]
    body = agui["requestBody"]["content"]["application/json"]
    assert body["schema"] == {"$ref": "#/components/schemas/RunAgentInput"}
    assert body["example"]["messages"][0]["role"] == "user"
    assert "text/event-stream" in agui["responses"]["200"]["content"]
    assert "application/json" not in agui["responses"]["200"]["content"]

    run_input = schema["components"]["schemas"]["RunAgentInput"]
    for name in ("threadId", "runId", "messages", "tools", "context", "forwardedProps"):
        assert name in run_input["properties"]
        assert run_input["properties"][name]["description"]
        assert "title" not in run_input["properties"][name]
    assert "UserMessage" in schema["components"]["schemas"]

    rendered = schema["paths"]["/admin/profile/render-instructions"]["post"]
    text = rendered["requestBody"]["content"]["text/plain"]
    assert text["schema"] == {"type": "string"}
    assert "{{ core.execution }}" in text["example"]
    assert "requestBody" not in schema["paths"]["/admin/profile/reload"]["post"]

    for path in (
        "/admin/profile/instructions.md",
        "/admin/profile/profile.toml",
        "/admin/profile/tools/{name}",
        "/admin/profile/skills/{skill}/SKILL.md",
    ):
        assert "text/plain" in schema["paths"][path]["put"]["requestBody"]["content"]

    saved = schema["paths"]["/threads/{thread_id}"]["put"]["requestBody"]["content"][
        "application/json"
    ]
    saved_schema = _resolve(schema, saved["schema"])
    assert "messages" in saved_schema["properties"]
    assert "effort" in saved_schema["properties"]

    env = schema["paths"]["/admin/profile/env/{name}"]["put"]["requestBody"]["content"][
        "application/json"
    ]
    env_schema = _resolve(schema, env["schema"])
    assert "value" in env_schema["properties"]
    assert schema["paths"]["/admin/profile/reload"]["post"]["security"] == [{"adminBearer": []}]
    assert schema["paths"]["/profile"]["get"]["security"] == [{"apiKey": []}]
    assert "/admin/api-key" not in schema["paths"]
    rotated = schema["paths"]["/admin/users/{username}/api-key"]["post"]
    assert rotated["security"] == [{"adminBearer": []}]
    assert "requestBody" not in rotated
    rotated_body = rotated["responses"]["200"]["content"]["application/json"]["schema"]
    assert "api_key" in _resolve(schema, rotated_body)["properties"]

    assert schema["paths"]["/auth/login"]["post"]["security"] == []
    assert schema["paths"]["/healthz"]["get"]["security"] == []
    assert schema["paths"]["/me/api-key"]["post"]["security"] == [{"apiKey": []}]
    created = schema["paths"]["/admin/users"]["post"]
    created_body = created["requestBody"]["content"]["application/json"]["schema"]
    assert {"username", "password", "role", "visibility"} <= set(
        _resolve(schema, created_body)["properties"]
    )
    owner = [p for p in schema["paths"]["/threads/{thread_id}"]["get"]["parameters"]
             if p["name"] == "owner"]
    assert owner and owner[0]["in"] == "query"
