"""Tiny public HTTP client used by the example profile."""

from __future__ import annotations

import os

import requests

from pydantic_ai.toolsets import FunctionToolset


def register(ctx) -> FunctionToolset:
    base = ctx.env.get("JSON_API_BASE") or os.environ.get(
        "JSON_API_BASE", "https://jsonplaceholder.typicode.com"
    )
    ts = FunctionToolset(max_retries=1)

    def get_post(post_id: int) -> dict:
        """Fetch one demo post by id (1–100). Returns id, title, body, userId."""
        r = requests.get(f"{base}/posts/{int(post_id)}", timeout=30)
        r.raise_for_status()
        return r.json()

    def list_posts(limit: int = 5) -> list[dict]:
        """List demo posts. `limit` caps how many are returned (default 5)."""
        r = requests.get(f"{base}/posts", timeout=30)
        r.raise_for_status()
        return r.json()[: max(1, min(int(limit), 20))]

    ts.add_function(func=get_post, takes_ctx=False)
    ts.add_function(func=list_posts, takes_ctx=False)
    return ts
