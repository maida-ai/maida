"""Only v2 installation identities are accepted; no implicit migration."""

import json
import pytest
from maida.config import load_config
from maida.project_local import installation
from tests.support.capture import initialized


@pytest.mark.parametrize(
    "pointer",
    [
        {"version": 1, "project_id": "a" * 32, "capture": "claude-code"},
        {"version": 2, "project_id": "a" * 32, "capture": "claude-code", "enabled": True},
        {"version": 2, "project_id": "a" * 32, "providers": {"claude-code": {"enabled": True}}, "enabled": False},
    ],
)
def test_old_pointer_requires_manual_reinitialization(tmp_path, monkeypatch, pointer):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".maida/local.json"
    raw = json.dumps(pointer)
    path.write_text(raw)
    with pytest.raises(ValueError, match=r"Move .*local.json.* aside and rerun maida init"):
        installation(tmp_path, command="maida check")
    assert path.read_text() == raw


def test_v2_identity_keeps_sdk_store_independent(tmp_path, temp_data_dir, monkeypatch):
    project_id = initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    pointer = installation(tmp_path)[1]
    assert pointer == {"version": 2, "project_id": project_id, "providers": {"claude-code": {"enabled": True}}}
    assert load_config(capture=True).data_dir == temp_data_dir / "projects" / project_id
    assert load_config().data_dir == temp_data_dir


@pytest.mark.parametrize(
    "providers", [{}, [], {"unknown": {"enabled": True}}, {"claude-code": {"enabled": 1}}, {"claude-code": None}]
)
def test_invalid_provider_state_rejected(tmp_path, providers):
    initialized(tmp_path)
    (tmp_path / ".maida/local.json").write_text(
        json.dumps({"version": 2, "project_id": "a" * 32, "providers": providers})
    )
    with pytest.raises(ValueError, match="local.json"):
        installation(tmp_path)


def test_provider_state_updates_are_independent_and_do_not_mutate_identity():
    from maida.capture.providers import attached_providers, runtime_enabled, updated_pointer

    pointer = {"version": 2, "project_id": "a" * 32, "providers": {"claude-code": {"enabled": True}}}
    disabled = updated_pointer(pointer, "claude-code", enabled=False)
    assert pointer["providers"]["claude-code"]["enabled"] is True
    assert disabled["project_id"] == pointer["project_id"]
    assert not runtime_enabled(disabled, "claude-code")
    assert attached_providers(disabled, enabled_only=False) == {"claude-code"}
    assert updated_pointer(disabled, "claude-code") == pointer
    with pytest.raises(ValueError, match="Unsupported"):
        updated_pointer(pointer, "unknown")
