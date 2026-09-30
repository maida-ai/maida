"""The default init journey needs no tutorial checkout or policy knowledge."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.cli import app
from maida.config import load_config

runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MAIDA_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr("maida.first_run.is_interactive", lambda: True)
    monkeypatch.setattr(
        "maida.first_run.shutil.which",
        lambda name: str(Path(sys.executable).parent / name) if name in {"claude", "maida"} else None,
    )
    return root


def test_fresh_preview_approval_and_repeat(project):
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "Detected Claude Code" in result.output
    assert result.output.index("Would add") < result.output.index("[y/N]")
    assert result.output.count("[y/N]") == 1
    assert result.output.count("Next:") == 1
    assert "maida assert --expect-status ok --no-loops --no-guardrails" in result.output
    assert not (project / ".maida/policy.yaml").exists()
    assert not (project / ".maida/starter").exists()
    pointer = json.loads((project / ".maida/local.json").read_text())
    assert pointer["version"] == 1
    assert pointer["capture"] == "claude-code"
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


def test_installed_hook_processes_produce_first_report(project, monkeypatch):
    """Use the exact installed observer commands, with no storage environment variable."""
    import os
    import pty
    import shlex

    monkeypatch.setenv("PATH", str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.delenv("CI", raising=False)
    (project / "CLAUDE.md").write_text("fixture instructions")
    primary, terminal = pty.openpty()
    try:
        with subprocess.Popen(
            [str(Path(sys.executable).parent / "maida"), "init"],
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
    finally:
        os.close(primary)
        if terminal is not None:
            os.close(terminal)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    settings = json.loads((project / ".claude/settings.local.json").read_text())
    tool = {"tool_use_id": "read-config", "tool_name": "Read", "tool_input": {"file_path": "pyproject.toml"}}
    for event, extra in (
        ("SessionStart", {"source": "startup"}),
        ("PreToolUse", tool),
        ("PostToolUse", {**tool, "tool_response": {"content": "pytest"}}),
        ("SessionEnd", {"reason": "other"}),
    ):
        observer = settings["hooks"][event][-1]["hooks"][0]["command"]
        payload = {"session_id": "first-real-hook-process", "cwd": str(project), "hook_event_name": event, **extra}
        captured = subprocess.run(
            shlex.split(observer), input=json.dumps(payload), text=True, capture_output=True, check=False
        )
        assert captured.returncode == 0, captured.stderr
        assert captured.stdout == captured.stderr == ""
    report = subprocess.run(
        [
            str(Path(sys.executable).parent / "maida"),
            "assert",
            "--expect-status",
            "ok",
            "--no-loops",
            "--no-guardrails",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    assert report.returncode == 0, report.stderr
    assert "3 active checks passed" in report.stdout
    assert str(project) in report.stdout
    assert "Coverage:" in report.stdout


def test_installed_ambiguity_and_unsupported_project_signal(project, monkeypatch):
    monkeypatch.setattr(
        "maida.first_run.shutil.which", lambda name: "/fixture/bin" if name in {"maida", "claude", "codex"} else None
    )
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "installed commands" in result.output
    assert "Claude Code, Codex" in result.output
    (project / ".codex").mkdir()
    unsupported = runner.invoke(app, ["init"])
    assert unsupported.exit_code == 2
    assert "Detected Codex" in unsupported.output
    assert not (project / ".maida").exists()


@pytest.mark.parametrize("args", [["--agent", "other"], ["--agent", "claude-code", "--from-run", "latest"]])
def test_agent_override_validation(project, args):
    result = runner.invoke(app, ["init", *args])
    assert result.exit_code == 2
    assert not (project / ".maida").exists()


def test_missing_hook_executable_and_disabled_setting_type(project, monkeypatch):
    monkeypatch.setattr("maida.first_run.shutil.which", lambda name: "/fixture/claude" if name == "claude" else None)
    missing = runner.invoke(app, ["init"])
    assert missing.exit_code == 2
    assert "PATH" in missing.output
    assert not (project / ".maida").exists()
    monkeypatch.setattr("maida.first_run.shutil.which", lambda name: "/fixture/bin")
    path = project / ".claude/settings.local.json"
    path.parent.mkdir()
    path.write_text('{"disableAllHooks": "false"}')
    invalid = runner.invoke(app, ["init"])
    assert invalid.exit_code == 2
    assert "boolean" in invalid.output
    assert path.read_text() == '{"disableAllHooks": "false"}'


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
    from tests.test_project_capture import task

    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    task(project, "first-task")
    check_args = ["assert", "--expect-status", "ok", "--no-loops", "--no-guardrails", "--format", "json"]
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
