"""The default init journey needs no tutorial checkout or policy knowledge."""

from tests.support.fixtures import project as project

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.cli import app
from maida.config import load_config

runner = CliRunner()
UV_EXECUTABLE = shutil.which("uv")


def test_fresh_preview_approval_and_repeat(project):
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Detected Claude Code" in result.output
    assert result.output.index("Would add") < result.output.index("[y/N]")
    assert result.output.count("[y/N]") == 1
    assert result.output.count("Next:") == 1
    assert "maida check" in result.output
    assert not (project / ".maida/policy.yaml").exists()
    assert not (project / ".maida/starter").exists()
    pointer = json.loads((project / ".maida/local.json").read_text())
    assert pointer["version"] == 2
    assert pointer["providers"] == {"claude-code": {"enabled": True}}
    assert load_config(capture=True).data_dir == Path.home() / ".maida/projects" / pointer["project_id"]
    excluded = subprocess.run(["git", "check-ignore", ".maida/local.json"], text=True, capture_output=True, check=True)
    assert excluded.stdout.strip() == ".maida/local.json"
    paths = [project / ".claude/settings.local.json", project / ".maida/local.json", project / ".git/info/exclude"]
    before = [path.read_bytes() for path in paths]
    repeat = runner.invoke(app, ["init"])
    assert repeat.exit_code == 0, repeat.output
    assert "[y/N]" not in repeat.output
    assert before == [path.read_bytes() for path in paths]


@pytest.mark.parametrize("answer", ["n\n", "\n"])
def test_declining_leaves_no_configuration(project, answer):
    exclude = (project / ".git/info/exclude").read_bytes()
    result = runner.invoke(app, ["init"], input=answer)
    assert result.exit_code == 0
    assert not (project / ".claude").exists()
    assert not (project / ".maida").exists()
    assert (project / ".git/info/exclude").read_bytes() == exclude


def test_noninteractive_is_preview_only(project, monkeypatch):
    monkeypatch.setattr("maida.first_run.is_interactive", lambda: False)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "interactive terminal" in result.output
    assert not (project / ".maida").exists()


def test_existing_settings_and_exclude_survive(project):
    path = project / ".claude/settings.local.json"
    path.parent.mkdir()
    original = {
        "permissions": {"deny": ["Write"]},
        "hooks": {"PreToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "existing"}]}]},
    }
    path.write_text(json.dumps(original))
    exclude = project / ".git/info/exclude"
    exclude.write_text("# user excludes\nprivate-file")
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 0, result.output
    settings = json.loads(path.read_text())
    assert settings["permissions"] == original["permissions"]
    assert settings["hooks"]["PreToolUse"][0] == original["hooks"]["PreToolUse"][0]
    assert exclude.read_text().startswith("# user excludes\nprivate-file\n")


@pytest.mark.parametrize("content", ["invalid", "[]", '{"hooks": []}', '{"disableAllHooks": true}'])
def test_bad_settings_have_actionable_error_without_writes(project, content):
    path = project / ".claude/settings.local.json"
    path.parent.mkdir()
    path.write_text(content)
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 2
    assert "rerun maida init" in result.output
    assert path.read_text() == content
    assert not (project / ".maida").exists()


def test_unsupported_and_ambiguous_detection(project, monkeypatch):
    monkeypatch.setattr("maida.first_run.shutil.which", lambda name: None)
    unsupported = runner.invoke(app, ["init"])
    assert unsupported.exit_code == 2
    assert "No supported coding agent" in unsupported.output
    assert not (project / ".maida").exists()
    (project / ".claude").mkdir()
    (project / ".cursor").mkdir()
    mixed = runner.invoke(app, ["init"])
    assert mixed.exit_code == 2
    assert "Claude Code" in mixed.output and "Cursor" in mixed.output
    assert "maida init --agent claude-code" in mixed.output
    monkeypatch.setattr(
        "maida.first_run.shutil.which",
        lambda name: str(Path(sys.executable).parent / name) if name == "maida" else None,
    )
    explicit = runner.invoke(app, ["init", "--agent", "claude-code"], input="y\n")
    assert explicit.exit_code == 0, explicit.output


def test_project_signal_beats_multiple_binaries(project, monkeypatch):
    (project / "CLAUDE.md").write_text("Project instructions")
    monkeypatch.setattr("maida.first_run.shutil.which", lambda name: str(Path(sys.executable).parent / name))
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 0, result.output


def test_concurrent_change_and_write_failure_do_not_partially_install(project, monkeypatch):
    import maida.first_run as first_run

    path = project / ".claude/settings.local.json"
    path.parent.mkdir()
    path.write_text("{}")

    def concurrent(*args, **kwargs):
        path.write_text('{"permissions": {}}')
        return True

    monkeypatch.setattr(first_run.typer, "confirm", concurrent)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "changed after preview" in result.output
    assert not (project / ".maida/local.json").exists()
    monkeypatch.setattr(first_run.typer, "confirm", lambda *a, **k: True)
    replace = first_run.atomic_replace

    def failing(path, content, *, expected, **kwargs):
        if path.name == "exclude":
            raise PermissionError("fixture write denied")
        return replace(path, content, expected=expected, **kwargs)

    monkeypatch.setattr(first_run, "atomic_replace", failing)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2, result.output
    assert json.loads(path.read_text()) == {"permissions": {}}
    assert not (project / ".maida/local.json").exists()


@pytest.mark.skipif(os.name != "posix", reason="Exercises POSIX pty and shell integration")
def test_uv_init_plain_claude_hooks_and_check_produce_first_report(project, monkeypatch):
    """Exercise uv's executable, then plain Claude's environment, without a global Maida."""
    import os
    import pty
    import shlex

    assert UV_EXECUTABLE, "uv is required by the repository's test workflow"
    monkeypatch.setenv("PATH", os.defpath)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(Path(sys.executable).parent.parent))
    # Reuse the installed test environment offline. --no-sync avoids resolving
    # fixture dependencies; uv still launches the real Maida entrypoint.
    (project / "pyproject.toml").write_text('[project]\nname = "first-task"\nversion = "0.0.0"\n')
    uv_maida = [UV_EXECUTABLE, "run", "--no-sync", "maida"]
    (project / "CLAUDE.md").write_text("fixture instructions")
    primary, terminal = pty.openpty()
    try:
        with subprocess.Popen(
            [*uv_maida, "init"],
            stdin=terminal,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        ) as process:
            os.close(terminal)
            terminal = None
            os.write(primary, b"y\n")
            output, _ = process.communicate(timeout=30)
            assert process.returncode == 0, output
            assert output.count("[y/N]") == 1
            assert "Claude Code capture is ready" in output
            assert "uv run maida check" in output
    finally:
        os.close(primary)
        if terminal is not None:
            os.close(terminal)
    # A deterministic Claude stand-in delivers real hook payloads through its
    # configured shell commands. No uv, Maida, or Python is discoverable on PATH.
    (project / "maida.py").write_text('raise RuntimeError("Project files must not shadow the installed hook")\n')
    fake_bin = project.parent / "agent-bin"
    fake_bin.mkdir()
    driver = fake_bin / "deliver.py"
    driver.write_text(
        """import json, os, subprocess
from pathlib import Path
root = Path.cwd()
assert not any((Path(p) / "maida").exists() for p in os.environ["PATH"].split(os.pathsep))
settings = json.loads((root / ".claude/settings.local.json").read_text())
tool = {"tool_use_id": "read-config", "tool_name": "Read", "tool_input": {"file_path": "pyproject.toml"}}
for event, extra in (
    ("SessionStart", {"source": "startup"}),
    ("PreToolUse", tool),
    ("PostToolUse", {**tool, "tool_response": {"content": "pytest"}}),
    ("SessionEnd", {"reason": "other"}),
):
    command = settings["hooks"][event][-1]["hooks"][0]["command"]
    payload = {"session_id": "first-real-hook-process", "cwd": str(root), "hook_event_name": event, **extra}
    result = subprocess.run(command, shell=True, executable="/bin/sh", input=json.dumps(payload),
                            text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
"""
    )
    claude = fake_bin / "claude"
    claude.write_text("#!/bin/sh\nexec " + shlex.join([sys.executable, str(driver)]) + "\n")
    claude.chmod(0o700)
    monkeypatch.setenv("PATH", str(fake_bin))
    monkeypatch.delenv("UV_RUN_RECURSION_DEPTH", raising=False)
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT")
    completed = subprocess.run(["claude"], text=True, capture_output=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    monkeypatch.setenv("UV_PROJECT_ENVIRONMENT", str(Path(sys.executable).parent.parent))
    report = subprocess.run([*uv_maida, "check"], text=True, capture_output=True, check=False, timeout=30)
    assert report.returncode == 0, report.stderr
    assert "3 active checks passed" in report.stdout
    assert str(project) in report.stdout
    assert "Coverage:" in report.stdout
    trace_id = next(line.removeprefix("Trace: ") for line in report.stdout.splitlines() if line.startswith("Trace: "))
    assert f"View: uv run maida view {trace_id}" in report.stdout


def test_installed_ambiguity_and_unsupported_project_signal(project, monkeypatch):
    monkeypatch.setattr(
        "maida.first_run.shutil.which", lambda name: "/fixture/bin" if name in {"maida", "claude", "codex"} else None
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "installed commands" in result.output
    assert "Claude Code, Codex" in result.output
    (project / "opencode.json").write_text("{}")
    unsupported = runner.invoke(app, ["init"])
    assert unsupported.exit_code == 2
    assert "Detected OpenCode" in unsupported.output
    assert not (project / ".maida").exists()


@pytest.mark.parametrize("args", [["--agent", "other"], ["--agent", "claude-code", "--from-run", "latest"]])
def test_agent_override_validation(project, args):
    result = runner.invoke(app, ["init", *args])
    assert result.exit_code == 2
    assert not (project / ".maida").exists()


def test_init_does_not_require_maida_on_path(project, monkeypatch):
    monkeypatch.setattr("maida.first_run.shutil.which", lambda name: "/fixture/claude" if name == "claude" else None)
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 0, result.output
    settings = json.loads((project / ".claude/settings.local.json").read_text())
    from maida.capture_setup import hook_arguments

    command = hook_arguments(settings["hooks"]["SessionStart"][0]["hooks"][0]["command"])
    assert command == [sys.executable, "-E", "-P", "-m", "maida.cli", "capture", "claude-hook"]


def test_disabled_setting_type(project):
    path = project / ".claude/settings.local.json"
    path.parent.mkdir()
    path.write_text('{"disableAllHooks": "false"}')
    invalid = runner.invoke(app, ["init"])
    assert invalid.exit_code == 2
    assert "boolean" in invalid.output
    assert path.read_text() == '{"disableAllHooks": "false"}'


def test_failed_validation_never_reports_ready_or_changes_settings(project, monkeypatch):
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    paths = [project / ".claude/settings.local.json", project / ".maida/local.json", project / ".git/info/exclude"]
    before = [path.read_bytes() for path in paths]

    def broken(command):
        raise ValueError("Cannot run the capture hook. Repair this Maida installation, then rerun maida init.")

    monkeypatch.setattr("maida.first_run.validate_hook_command", broken)
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 2, result.output
    assert "Repair this Maida installation" in result.output
    assert "ready" not in result.output
    assert "[y/N]" not in result.output
    assert before == [path.read_bytes() for path in paths]


def test_init_legacy_local_hook_upgrade_requires_approval(project):
    from maida.capture_setup import bound_hook_command, merged_settings

    path = project / ".claude/settings.local.json"
    path.parent.mkdir()
    settings = merged_settings({"permissions": {"deny": ["Write"]}})[0]
    path.write_text(json.dumps(settings))
    before = path.read_bytes()
    declined = runner.invoke(app, ["init"], input="n\n")
    assert declined.exit_code == 0, declined.output
    assert path.read_bytes() == before
    updated = runner.invoke(app, ["init"], input="y\n")
    assert updated.exit_code == 0, updated.output
    assert updated.output.index(bound_hook_command()) < updated.output.index("[y/N]")
    upgraded = json.loads(path.read_text())
    assert upgraded["permissions"] == settings["permissions"]
    assert all(len(groups) == 1 for groups in upgraded["hooks"].values())
    assert runner.invoke(app, ["detach", "--agent", "claude-code"], input="y\n").exit_code == 0
    assert json.loads(path.read_text()) == {"permissions": settings["permissions"]}


@pytest.mark.parametrize("user_wide", [False, True])
def test_inherited_legacy_hooks_have_specific_recovery_without_writes(project, user_wide):
    from maida.capture_setup import merged_settings

    path = (Path.home() if user_wide else project) / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text(json.dumps(merged_settings({})[0]))
    before = path.read_bytes()
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 2, result.output
    assert str(path) in result.output
    assert ("/hooks" if user_wide else "maida detach --agent claude-code") in result.output
    assert path.read_bytes() == before
    assert not (project / ".maida").exists()


@pytest.mark.parametrize("scope", ["group", "hook"])
def test_disabled_inherited_legacy_hooks_do_not_block_local_setup(project, scope):
    from maida.capture_setup import COMMAND

    path = project / ".claude/settings.json"
    path.parent.mkdir()
    hook = {"type": "command", "command": COMMAND}
    group = {"hooks": [hook]}
    (group if scope == "group" else hook)["enabled"] = False
    path.write_text(json.dumps({"hooks": {"SessionStart": [group]}}))
    before = path.read_bytes()
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 0, result.output
    assert path.read_bytes() == before
    assert (project / ".claude/settings.local.json").exists()


def test_cancelled_input_and_io_failure_are_actionable(project, monkeypatch):
    import maida.first_run as first_run

    monkeypatch.setattr(first_run.typer, "confirm", lambda *a, **k: (_ for _ in ()).throw(EOFError()))
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert not (project / ".maida").exists()
    monkeypatch.setattr(first_run, "prepare_settings", lambda *a, **k: (_ for _ in ()).throw(PermissionError()))
    denied = runner.invoke(app, ["init"])
    assert denied.exit_code == 2
    assert "Check permissions" in denied.output


def test_existing_review_flow_after_first_run_setup(project):
    from tests.support.capture import task

    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    task(project, "first-task")
    check_args = ["check", "--format", "json"]
    first_check = runner.invoke(app, check_args)
    assert first_check.exit_code == 0, first_check.output
    first_id = json.loads(first_check.stdout)["run_id"]
    draft = runner.invoke(app, ["init", "--from-run", first_id])
    assert draft.exit_code == 0, draft.output
    assert not (project / ".maida/policy.yaml").exists()
    reviewed = runner.invoke(app, ["init", "--reviewed", "--reason", "This task must complete without loops"])
    assert reviewed.exit_code == 0, reviewed.output
    baseline = project / ".maida/baselines/agent.json"
    before = baseline.read_bytes()
    task(project, "fresh-task")
    new_check = runner.invoke(app, check_args)
    assert new_check.exit_code == 0, new_check.output
    new_id = json.loads(new_check.stdout)["run_id"]
    result = runner.invoke(app, ["assert", new_id, "--baseline", str(baseline), "--policy", ".maida/policy.yaml"])
    assert result.exit_code == 0, result.output
    assert baseline.read_bytes() == before


def test_local_settings_override_and_tracked_pointer(project):
    user = Path.home() / ".claude/settings.json"
    user.parent.mkdir()
    user.write_text('{"disableAllHooks": true}')
    local = project / ".claude/settings.local.json"
    local.parent.mkdir()
    local.write_text('{"disableAllHooks": false}')
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    subprocess.run(["git", "add", "--force", ".maida/local.json"], check=True)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "git rm --cached -- .maida/local.json" in result.output


def test_git_worktree_has_its_own_pointer_and_resolved_exclude(project, monkeypatch):
    (project / "README.md").write_text("fixture")
    subprocess.run(["git", "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"],
        check=True,
    )
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    first_id = load_config(capture=True).project_id
    worktree = project.parent / "worktree"
    subprocess.run(["git", "worktree", "add", "--detach", str(worktree)], capture_output=True, check=True)
    monkeypatch.chdir(worktree)
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 0, result.output
    assert load_config(capture=True).project_id != first_id
    assert subprocess.run(["git", "check-ignore", ".maida/local.json"], capture_output=True).returncode == 0
