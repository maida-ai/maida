"""Safe, additive installation of passive Claude observers."""

import json

import pytest

from maida.capture_setup import COMMAND, EVENTS, install, merged_settings


def test_preview_and_repeated_install(tmp_path):
    assert install(tmp_path, apply=False) == list(EVENTS)
    assert not (tmp_path / ".claude").exists()
    assert install(tmp_path, apply=True) == list(EVENTS)
    path = tmp_path / ".claude/settings.json"
    original = path.read_bytes()
    assert install(tmp_path, apply=True) == []
    assert path.read_bytes() == original
    assert json.loads(original)["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 30


def test_preserves_settings_and_restricted_observers():
    original = {
        "permissions": {"deny": ["Write"]},
        "env": {"EXAMPLE": "unchanged"},
        "hooks": {"PostToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": COMMAND}]}]},
    }
    merged, added = merged_settings(original)
    assert merged["permissions"] == original["permissions"]
    assert merged["env"] == original["env"]
    assert merged["hooks"]["PostToolUse"][0] == original["hooks"]["PostToolUse"][0]
    assert len(original["hooks"]["PostToolUse"]) == 1
    assert added == list(EVENTS)


@pytest.mark.parametrize("restriction", [{"if": "Read(*)"}, {"async": True}, {"once": True}])
def test_conditional_observer_does_not_count_as_coverage(restriction):
    settings = {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": COMMAND, **restriction}]}]}}
    merged, added = merged_settings(settings)
    assert "PreToolUse" in added
    assert len(merged["hooks"]["PreToolUse"]) == 2


@pytest.mark.parametrize("content", ["invalid JSON", "[]", '{"hooks": []}', '{"hooks": {"SessionStart": [{}]}}'])
def test_malformed_settings_are_preserved(tmp_path, content):
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text(content)
    with pytest.raises(ValueError, match="settings|hooks"):
        install(tmp_path, apply=True)
    assert path.read_text() == content


def test_symlink_and_mode(tmp_path):
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir()
    target = tmp_path / "original.json"
    target.write_text("{}")
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        install(tmp_path, apply=True)
    assert target.read_text() == "{}"
    path.unlink()
    path.write_text("{}")
    path.chmod(0o600)
    install(tmp_path, apply=True)
    assert path.stat().st_mode & 0o777 == 0o600


def test_settings_changed_after_preview_are_not_overwritten(tmp_path):
    from maida.capture_setup import atomic_replace, read_safe

    path = tmp_path / "settings.json"
    path.write_text("{}")
    before = read_safe(path)
    path.write_text('{"permissions": {}}')
    with pytest.raises(ValueError, match="changed"):
        atomic_replace(path, b"{}", expected=before)
    assert json.loads(path.read_text()) == {"permissions": {}}


def test_write_failure_preserves_original_and_cleans_temporary(tmp_path, monkeypatch):
    from maida.capture_setup import atomic_replace

    path = tmp_path / "settings.json"
    path.write_text("{}")
    monkeypatch.setattr("maida.capture_setup.os.replace", lambda *args: (_ for _ in ()).throw(OSError("read-only")))
    with pytest.raises(OSError):
        atomic_replace(path, b"new", expected=b"{}")
    assert path.read_text() == "{}"
    assert list(tmp_path.iterdir()) == [path]
