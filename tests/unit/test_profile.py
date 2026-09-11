"""Profile loader tests using the public example profile (no customer data)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from bizharness.profile import ProfileError, load

EXAMPLE = Path(__file__).resolve().parents[2] / "profiles" / "example"


def test_example_profile_loads(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path))
    p = load(EXAMPLE)
    assert p.id == "example"
    assert p.code_tool.name == "data_code"
    text = p.resolve_instructions()
    assert "data_code" in text
    assert "the demo API" in text
    assert p.missing_env() == []


def test_missing_toml_is_a_profile_error(tmp_path):
    with pytest.raises(ProfileError):
        load(tmp_path)


def test_hash_changes_when_instructions_change(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path))
    dest = tmp_path / "copy"
    shutil.copytree(EXAMPLE, dest)
    p1 = load(dest)
    (dest / "instructions.md").write_text(p1.instructions_template + "\n# extra\n")
    p2 = load(dest)
    assert p1.hash != p2.hash
