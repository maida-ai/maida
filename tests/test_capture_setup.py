"""Safe, additive installation of passive Claude observers."""

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from maida.capture_setup import (
    COMMAND,
    EVENTS,
    atomic_replace,
    bound_hook_command,
    hook_arguments,
    install,
    is_maida_hook_command,
    merged_settings,
    removed_settings,
    validate_hook_command,
)


@pytest.mark.parametrize("scope", ["group", "hook"])
@pytest.mark.parametrize("restriction", [{"enabled": False}, {"disabled": True}])
def test_disabled_inherited_observer_does_not_count_as_coverage(scope, restriction):
    hook = {"type": "command", "command": COMMAND}
    group = {"hooks": [hook]}
    (group if scope == "group" else hook).update(restriction)
    inherited = {"hooks": {"PreToolUse": [group]}}
    merged, added = merged_settings({}, inherited=(inherited,))
    assert "PreToolUse" in added
    assert merged["hooks"]["PreToolUse"][0]["hooks"] == [{"type": "command", "command": COMMAND}]


def test_effective_inherited_disable_all_hooks_and_local_override():
    inherited = merged_settings({})[0]
    inherited["disableAllHooks"] = True
    with pytest.raises(ValueError, match="Enable hooks"):
        merged_settings({}, inherited=(inherited,))
    # disableAllHooks applies to the merged configuration, not to each source.
    _, added = merged_settings({"disableAllHooks": False}, inherited=(inherited,))
    assert added == []


def test_absent_replacement_is_rejected_before_creating_files(tmp_path):
    path = tmp_path / ".claude/settings.json"
    with pytest.raises(TypeError, match="must be bytes"):
        atomic_replace(path, None, expected=None)
    assert not path.parent.exists()


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


def test_symlink_and_mode(tmp_path, symlink_supported):
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
    if os.name == "posix":
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


def test_bound_command_preserves_venv_path_and_shell_quoting(tmp_path, monkeypatch, symlink_supported):
    alias = tmp_path / "environment with ' spaces"
    alias.symlink_to(Path(sys.prefix), target_is_directory=True)
    executable = str(alias / Path(sys.executable).relative_to(sys.prefix))
    monkeypatch.setattr("maida.capture_setup.sys.executable", executable)
    command = bound_hook_command()
    assert hook_arguments(command) == [executable, "-E", "-P", "-m", "maida.cli", "capture", "claude-hook"]
    assert is_maida_hook_command(command)
    validate_hook_command(command)


def test_bound_hook_runs_a_user_site_installation(tmp_path, monkeypatch, symlink_supported):
    import sysconfig
    import venv

    from maida.config import load_config
    from tests.test_project_capture import initialized

    dependencies = sysconfig.get_path("purelib")
    environment = tmp_path / "python-environment"
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
    executable = str(environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python"))
    locations = subprocess.run(
        [
            executable,
            "-E",
            "-P",
            "-c",
            "import json, site, sysconfig; print(json.dumps([site.getusersitepackages(), sysconfig.get_path('purelib')]))",
        ],
        text=True,
        capture_output=True,
        check=True,
    )
    user_site, environment_site = map(Path, json.loads(locations.stdout))
    assert user_site.is_relative_to(Path.home())
    user_site.mkdir(parents=True)
    # Install real Maida in the isolated HOME's user site; reuse only its already
    # installed dependencies through the new environment's ordinary site path.
    user_site.joinpath("maida").symlink_to(Path(__file__).resolve().parents[1] / "maida", target_is_directory=True)
    environment_site.joinpath("dependencies.pth").write_text(dependencies + "\n")
    project = tmp_path / "repo"
    initialized(project)
    monkeypatch.chdir(project)
    monkeypatch.setattr("maida.capture_setup.sys.executable", executable)
    command = bound_hook_command()
    validate_hook_command(command)
    payload = {
        "session_id": "user-site-task",
        "cwd": str(project),
        "hook_event_name": "SessionStart",
        "source": "startup",
    }
    result = subprocess.run(
        hook_arguments(command), input=json.dumps(payload), text=True, capture_output=True, timeout=15
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    assert list((load_config(capture=True).data_dir / "onboarding").glob("*.json"))


@pytest.mark.parametrize("failure", ["exit", "missing", "timeout"])
def test_bound_command_validation_failures_are_actionable(monkeypatch, failure):
    def probe(arguments, **kwargs):
        assert arguments[-1] == "--help"
        assert "PYTHONPATH" not in kwargs["env"]
        if failure == "missing":
            raise FileNotFoundError("fixture missing interpreter")
        if failure == "timeout":
            raise subprocess.TimeoutExpired(arguments, 15)
        return subprocess.CompletedProcess(arguments, 1)

    monkeypatch.setattr("maida.capture_setup.subprocess.run", probe)
    with pytest.raises(ValueError, match="Reinstall Maida"):
        validate_hook_command(bound_hook_command())


def test_local_legacy_and_old_bound_hooks_migrate_without_duplicates(tmp_path):
    old = shlex.join([str(tmp_path / "old/environment/python"), "-m", "maida.cli", "capture", "claude-hook"])
    for previous in (COMMAND, old):
        settings = merged_settings({}, observer_command=previous)[0]
        settings["permissions"] = {"deny": ["Write"]}
        original = json.loads(json.dumps(settings))
        migrated, changed = merged_settings(settings, observer_command=bound_hook_command())
        assert changed == list(EVENTS)
        assert settings == original
        assert migrated["permissions"] == settings["permissions"]
        for event in EVENTS:
            assert len(migrated["hooks"][event]) == 1
            assert migrated["hooks"][event][0]["hooks"][0]["command"] == bound_hook_command()
        assert migrated["hooks"]["SessionEnd"][0]["hooks"][0]["timeout"] == 30
        assert merged_settings(migrated, observer_command=bound_hook_command())[1] == []


def test_legacy_installer_preserves_existing_bound_observers(tmp_path):
    path = tmp_path / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(merged_settings({}, observer_command=bound_hook_command())[0]))
    before = path.read_bytes()
    assert install(tmp_path, apply=True) == []
    assert path.read_bytes() == before


def test_detach_only_removes_exact_legacy_and_bound_commands():
    bound = bound_hook_command()
    preserved = [
        f"echo {COMMAND}",
        f"{bound} --extra",
        f"{bound} && echo keep",
        "'malformed quote",
    ]
    commands = [COMMAND, bound, *preserved]
    settings = {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": c} for c in commands]}]}}
    result, removed = removed_settings(settings)
    assert removed == 2
    assert [h["command"] for h in result["hooks"]["SessionEnd"][0]["hooks"]] == preserved
