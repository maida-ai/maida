"""Versioned capture attachment must preserve legacy identity and SDK storage."""

import json

import pytest

from maida.capture.providers import attached_providers, runtime_enabled, updated_pointer
from maida.config import load_config
from maida.project_local import installation
from tests.test_project_capture import initialized


def test_explicit_upgrade_retains_namespace(tmp_path, temp_data_dir, monkeypatch):
    project_id = initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    legacy = installation(tmp_path)[1]
    legacy["evidence_paths"] = {"captures": "retained-local-path"}
    upgraded = updated_pointer(legacy, "codex")
    assert legacy == {
        "version": 1,
        "project_id": project_id,
        "capture": "claude-code",
        "evidence_paths": {"captures": "retained-local-path"},
    }
    assert upgraded["evidence_paths"] == legacy["evidence_paths"]
    assert attached_providers(upgraded) == {"claude-code", "codex"}
    (tmp_path / ".maida/local.json").write_text(json.dumps(upgraded))
    assert load_config(capture=True).data_dir == temp_data_dir / "projects" / project_id
    assert load_config().data_dir == temp_data_dir
    assert updated_pointer(upgraded, "codex") == upgraded


def test_detached_legacy_and_shared_runtime():
    legacy = {"version": 1, "project_id": "a" * 32, "capture": "claude-code", "enabled": False}
    pointer = updated_pointer(legacy, "chatgpt-work")
    assert attached_providers(pointer) == {"chatgpt-work"}
    assert runtime_enabled(pointer, "codex")
    assert not runtime_enabled(pointer, "claude-code")
    pointer = updated_pointer(pointer, "chatgpt-work", enabled=False)
    assert not runtime_enabled(pointer, "codex")


@pytest.mark.parametrize(
    "providers", [{}, [], {"unknown": {"enabled": True}}, {"codex": {"enabled": 1}}, {"codex": None}]
)
def test_invalid_v2_pointer_does_not_fall_back(tmp_path, providers):
    initialized(tmp_path)
    (tmp_path / ".maida/local.json").write_text(
        json.dumps({"version": 2, "project_id": "a" * 32, "providers": providers})
    )
    with pytest.raises(ValueError, match="local.json"):
        installation(tmp_path)
