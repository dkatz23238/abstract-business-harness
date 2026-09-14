"""Plugin loader using the public example tools."""

from pathlib import Path

from bizharness import plugins
from bizharness.profile import load

EXAMPLE = Path(__file__).resolve().parents[2] / "profiles" / "example"


def test_example_tools_register(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path))
    p = load(EXAMPLE)
    toolset, ctx = plugins.load(p)
    assert set(toolset.tools) == {"get_post", "list_posts"}
    assert ctx.env["JSON_API_BASE"].startswith("https://")
    inv = plugins.inventory(p)
    by_name = {row["name"]: row for row in inv}
    row = by_name["json_placeholder.py"]
    assert row["functions"] == 2
    assert row["names"] == ["get_post", "list_posts"]
    entries = {e["name"]: e for e in row["entries"]}
    assert entries["get_post"]["signature"] == "get_post(post_id: int) -> dict"
    assert entries["get_post"]["params"] == [
        {"name": "post_id", "type": "int", "optional": False, "default": None}
    ]
    assert entries["get_post"]["returns"] == "dict"
    assert entries["list_posts"]["signature"] == "list_posts(limit: int = 5) -> list[dict]"
    assert entries["list_posts"]["params"] == [
        {"name": "limit", "type": "int", "optional": True, "default": 5}
    ]
