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
    assert p.model.effort == "medium"
    text = p.resolve_instructions()
    assert "data_code" in text
    assert "the demo API" in text
    assert p.missing_env() == []
    ui = p.ui_config()
    assert ui["default_effort"] == "medium"
    assert ui["effort_levels"] == ["low", "medium", "high", "xhigh"]
    assert ui["redact_terms"] == ["Acme Grain", "North Elevator"]


def test_invalid_effort_is_a_profile_error(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path))
    dest = tmp_path / "copy"
    shutil.copytree(EXAMPLE, dest)
    text = (dest / "profile.toml").read_text()
    (dest / "profile.toml").write_text(text.replace("[model]\n", '[model]\neffort = "ludicrous"\n'))
    with pytest.raises(ProfileError, match="model.effort"):
        load(dest)


def test_profile_can_override_default_effort(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path))
    dest = tmp_path / "copy"
    shutil.copytree(EXAMPLE, dest)
    text = (dest / "profile.toml").read_text()
    (dest / "profile.toml").write_text(text.replace("[model]\n", '[model]\neffort = "high"\n'))
    p = load(dest)
    assert p.model.effort == "high"
    assert p.ui_config()["default_effort"] == "high"


def test_missing_toml_is_a_profile_error(tmp_path):
    with pytest.raises(ProfileError):
        load(tmp_path)


def test_missing_absolute_path_follows_the_file_next_to_the_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "state"))
    tree = tmp_path / "tree"
    dest = tree / "profile"
    shutil.copytree(EXAMPLE, dest)
    (tree / "notes.sqlite").write_bytes(b"")
    text = (dest / "profile.toml").read_text().replace(
        'JSON_API_BASE = "https://jsonplaceholder.typicode.com"',
        'JSON_API_BASE = "https://jsonplaceholder.typicode.com"\n'
        'NOTES = "/home/someone/tree/notes.sqlite"',
    )
    (dest / "profile.toml").write_text(text)
    profile = load(dest)
    assert profile.env["NOTES"] == str(tree / "notes.sqlite")


def test_redact_terms_accept_a_string_or_a_list(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "state"))
    dest = tmp_path / "copy"
    shutil.copytree(EXAMPLE, dest)
    (dest / "profile.toml").write_text(
        (dest / "profile.toml").read_text().replace(
            'terms = ["Acme Grain", "North Elevator"]',
            'terms = "Acme Grain, north elevator"',
        )
    )
    listed = load(dest)
    assert listed.redact_terms == ["Acme Grain", "north elevator"]

    (dest / "profile.toml").write_text(
        (dest / "profile.toml").read_text().replace(
            'terms = "Acme Grain, north elevator"',
            'terms = ["Acme Grain", "North Elevator", "acme grain"]',
        )
    )
    from_list = load(dest)
    assert from_list.redact_terms == ["Acme Grain", "North Elevator"]
    assert from_list.ui_config()["redact_terms"] == ["Acme Grain", "North Elevator"]


def test_redact_terms_file_merges_with_inline_terms(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "state"))
    dest = tmp_path / "copy"
    shutil.copytree(EXAMPLE, dest)
    (dest / "redact_terms.txt").write_text("# customers\nSouth Co-op\nAcme Grain, River Mill\n")
    (dest / "profile.toml").write_text(
        (dest / "profile.toml").read_text().replace(
            'terms = ["Acme Grain", "North Elevator"]',
            'terms = ["North Elevator"]\nterms_file = "redact_terms.txt"',
        )
    )
    profile = load(dest)
    assert profile.redact_terms == ["North Elevator", "South Co-op", "Acme Grain", "River Mill"]
    before = profile.hash
    (dest / "redact_terms.txt").write_text("South Co-op\n")
    assert load(dest).hash != before


def test_redact_terms_file_must_stay_in_the_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "state"))
    dest = tmp_path / "copy"
    shutil.copytree(EXAMPLE, dest)
    (dest / "profile.toml").write_text(
        (dest / "profile.toml").read_text().replace(
            'terms = ["Acme Grain", "North Elevator"]',
            'terms_file = "../secrets.txt"',
        )
    )
    with pytest.raises(ProfileError, match="terms_file"):
        load(dest)


def test_hash_changes_when_instructions_change(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path))
    dest = tmp_path / "copy"
    shutil.copytree(EXAMPLE, dest)
    p1 = load(dest)
    (dest / "instructions.md").write_text(p1.instructions_template + "\n# extra\n")
    p2 = load(dest)
    assert p1.hash != p2.hash
