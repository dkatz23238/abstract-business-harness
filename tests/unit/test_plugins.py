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
