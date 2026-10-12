"""OpenAPI presentation for the HTTP API.

FastAPI leaves untagged routes under a section named "default", and a handler
that reads `Request` directly gets no request-body schema. The models and
helpers here are what `/docs` shows for those payloads. Parsing of `POST /agui`
stays in the AG-UI adapter so a newer protocol message is still skipped instead
of rejected before that adapter runs.
"""

from __future__ import annotations

import copy
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

PROFILE = "Profile"
RUNS = "Runs"
EVENTS = "Events"
THREADS = "Threads"
WORKSPACE = "Workspace"
ADMIN = "Admin"

OPENAPI_TAGS: list[dict[str, str]] = [
    {
        "name": PROFILE,
        "description": "Profile metadata the chat UI loads at startup.",
    },
    {
        "name": RUNS,
        "description": (
            "Start an agent run and reattach to its AG-UI event stream. "
            "A run keeps going after the client disconnects."
        ),
    },
    {
        "name": EVENTS,
        "description": "Live tool-activity stream for one thread.",
    },
    {
        "name": THREADS,
        "description": "Saved conversations.",
    },
    {
        "name": WORKSPACE,
        "description": "Files the agent wrote for one thread.",
    },
    {
        "name": ADMIN,
        "description": (
            "Read and rewrite the profile directory. "
            "Every route requires `Authorization: Bearer <HARNESS_ADMIN_TOKEN>`."
        ),
    },
]

_AGUI_FIELD_DOCS = {
    "threadId": (
        "Conversation id. The saved transcript, workspace files, and event stream all use this id."
    ),
    "runId": "Id for this run. The client may reuse it when reconnecting.",
    "parentRunId": "Set when this run was started by another run. Omit for a top-level turn.",
    "state": "Opaque JSON the client wants associated with the run. Null means no state.",
    "messages": (
        "Conversation to run, including the new user message. "
        "Each item is an AG-UI message discriminated by `role`."
    ),
    "tools": (
        "Tool definitions the agent may call on the client. "
        "Send an empty list when there are none."
    ),
    "context": "Extra named context entries. Send an empty list when there are none.",
    "forwardedProps": (
        "Free-form client metadata. This server reads `effort` "
        "(`low`, `medium`, `high`, or `xhigh`) as the reasoning effort for the run. "
        "Send `{}` when there is nothing to forward."
    ),
    "resume": "Answers to interrupts from a paused run. Omit when starting a normal turn.",
}

_AGUI_EXAMPLE = {
    "threadId": "thread-1",
    "runId": "run-1",
    "messages": [{"id": "m1", "role": "user", "content": "Summarize the latest filings."}],
    "tools": [],
    "context": [],
    "forwardedProps": {"effort": "medium"},
    "state": {},
}


class SaveThreadBody(BaseModel):
    """Transcript snapshot written over one thread."""

    messages: list[dict[str, Any]] = Field(
        default_factory=list,
        description="AG-UI messages to store. Replaces the thread's saved transcript.",
        examples=[[{"id": "m1", "role": "user", "content": "hello"}]],
    )
    effort: str | None = Field(
        default=None,
        description=(
            "Reasoning effort to store for later runs on this thread: "
            "`low`, `medium`, `high`, or `xhigh`. An unrecognized value is stored as "
            "`medium`. Omit the field to keep the effort already saved."
        ),
        examples=["high"],
    )


class EnvValueBody(BaseModel):
    """New value for one profile environment variable."""

    value: str = Field(
        default="",
        description="Value to write into the profile's secrets file. The API never returns it.",
        examples=["sk-..."],
    )


def text_request_body(description: str, example: str) -> dict[str, Any]:
    """OpenAPI extra for a route whose body is raw UTF-8 text, not JSON."""
    return {
        "requestBody": {
            "required": True,
            "description": description,
            "content": {
                "text/plain": {
                    "schema": {"type": "string"},
                    "example": example,
                }
            },
        }
    }


class EventStreamResponse(StreamingResponse):
    """Response class used so `/docs` advertises `text/event-stream`.

    The handlers return their own `StreamingResponse`; this subclass only
    supplies the media type FastAPI copies into the OpenAPI document.
    """

    media_type = "text/event-stream"


def install(app: FastAPI) -> None:
    """Replace `app.openapi` with a schema that includes the AG-UI body."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            openapi_version=app.openapi_version,
            summary=app.summary,
            description=app.description,
            routes=app.routes,
            webhooks=app.webhooks.routes,
            tags=app.openapi_tags,
            servers=app.servers,
            separate_input_output_schemas=app.separate_input_output_schemas,
        )
        _add_agui_body(schema)
        _add_admin_security(schema)
        app.openapi_schema = schema
        return schema

    app.openapi = openapi


def _add_agui_body(schema: dict[str, Any]) -> None:
    from ag_ui.core.types import RunAgentInput

    raw = copy.deepcopy(
        RunAgentInput.model_json_schema(ref_template="#/components/schemas/{model}")
    )
    defs = raw.pop("$defs", {})
    raw["description"] = (
        "AG-UI `RunAgentInput`. Property names are camelCase, as on the wire."
    )
    for name, text in _AGUI_FIELD_DOCS.items():
        raw["properties"][name]["description"] = text
    _strip_property_titles(raw)
    for definition in defs.values():
        _strip_property_titles(definition)

    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components.update(defs)
    components["RunAgentInput"] = raw
    operation = schema["paths"]["/agui"]["post"]
    operation["requestBody"] = {
        "required": True,
        "description": (
            "AG-UI run request. `forwardedProps.effort` selects reasoning effort "
            "for this run (`low`, `medium`, `high`, or `xhigh`)."
        ),
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/RunAgentInput"},
                "example": _AGUI_EXAMPLE,
            }
        },
    }


def _add_admin_security(schema: dict[str, Any]) -> None:
    schemes = schema.setdefault("components", {}).setdefault("securitySchemes", {})
    schemes["apiKey"] = {
        "type": "http",
        "scheme": "bearer",
        "description": (
            "API key printed at startup and stored in `<data-root>/api_key`. "
            "The same value is accepted as the `token` query parameter."
        ),
    }
    schemes["adminBearer"] = {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "token",
        "description": "The `HARNESS_ADMIN_TOKEN` value.",
    }
    for path, item in schema.get("paths", {}).items():
        for operation in item.values():
            if not isinstance(operation, dict) or "operationId" not in operation:
                continue
            if path.startswith("/admin"):
                operation["security"] = [{"adminBearer": []}]
            else:
                operation["security"] = [{"apiKey": []}]


def _strip_property_titles(node: object) -> None:
    """Drop Pydantic's auto titles (`Threadid`, `Forwardedprops`) on fields.

    Model names stay. Swagger already shows the property name, and the generated
    title mangles camelCase aliases.
    """
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for prop in props.values():
                if isinstance(prop, dict):
                    prop.pop("title", None)
        for value in node.values():
            _strip_property_titles(value)
    elif isinstance(node, list):
        for value in node:
            _strip_property_titles(value)
