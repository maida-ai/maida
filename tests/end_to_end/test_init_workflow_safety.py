"""First-run setup and detach must preserve established user workflows."""

import json
import shutil
import subprocess
from pathlib import Path
import pytest
from typer.testing import CliRunner
from maida.cli import _normalize_demo_trace_duration, app
from maida.config import load_config
from maida.demo._agents import run_good_agent
from maida.storage import resolve_latest_run_id
from tests.support.capture import initialized, task


runner = CliRunner()


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    monkeypatch.chdir(root)
    monkeypatch.setattr("maida.first_run.is_interactive", lambda: True)
    monkeypatch.setattr(
        "maida.first_run.shutil.which", lambda name: "/fixture/bin/" + name if name in {"claude", "maida"} else None
    )
    return root


@pytest.mark.parametrize("configured_directory", [False, True])
def test_sdk_gate_and_claude_onboarding_survive_init_and_detach(
    project, temp_data_dir, monkeypatch, configured_directory
):
    if not configured_directory:
        monkeypatch.delenv("MAIDA_DATA_DIR")
    expected_directory = temp_data_dir if configured_directory else Path.home() / ".maida"
    run_good_agent()
    trace_id = resolve_latest_run_id(load_config())
    # Storage selection must not depend on wall-clock variation between demo runs.
    _normalize_demo_trace_duration(trace_id, load_config())
    baseline = project / "baseline.json"
    assert runner.invoke(app, ["baseline", trace_id, "--out", str(baseline)]).exit_code == 0
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    assert load_config().data_dir == expected_directory
    assert runner.invoke(app, ["assert", trace_id, "--expect-status", "ok"]).exit_code == 0
    assert runner.invoke(app, ["export", trace_id, "--out", "old-run.json"]).exit_code == 0
    task(project)
    run_good_agent()
    latest_python = resolve_latest_run_id(load_config())
    _normalize_demo_trace_duration(latest_python, load_config())
    result = runner.invoke(app, ["assert", "--baseline", str(baseline), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == latest_python
    captured = resolve_latest_run_id(load_config(capture=True))
    first_check = runner.invoke(app, ["check", "--format", "json"])
    assert first_check.exit_code == 0, first_check.output
    assert json.loads(first_check.stdout)["run_id"] == captured
    # The local integration does not hide pre-init or current SDK runs.
    listed = runner.invoke(app, ["list", "--json"])
    assert {run["trace_id"] for run in json.loads(listed.stdout)["runs"]} == {trace_id, latest_python}
    assert runner.invoke(app, ["detach", "--agent", "claude-code"], input="y\n").exit_code == 0
    assert (project / ".maida/local.json").exists()
    listed = runner.invoke(app, ["list", "--json"])
    assert {run["trace_id"] for run in json.loads(listed.stdout)["runs"]} == {trace_id, latest_python}
    after_detach = runner.invoke(app, ["assert", "--baseline", str(baseline), "--format", "json"])
    assert after_detach.exit_code == 0, after_detach.output
    assert json.loads(after_detach.stdout)["run_id"] == latest_python
    detached_check = runner.invoke(
        app, ["assert", "--expect-status", "ok", "--no-loops", "--no-guardrails", "--format", "json"]
    )
    assert detached_check.exit_code == 0, detached_check.output
    assert json.loads(detached_check.stdout)["run_id"] == latest_python
    assert runner.invoke(app, ["check"]).exit_code == 2
    assert runner.invoke(app, ["export", captured, "--out", "saved-claude.json"]).exit_code == 0
    # A checked-in baseline may have been captured on another developer's machine.
    foreign = json.loads(baseline.read_text())
    foreign["source_run_id"] = "a" * 32
    baseline.write_text(json.dumps(foreign))
    result = runner.invoke(app, ["assert", "--baseline", str(baseline), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == latest_python


def test_corrupt_local_pointer_does_not_break_sdk_or_explicit_python_reads(project, temp_data_dir):
    run_good_agent()
    trace_id = resolve_latest_run_id(load_config())
    (project / ".maida").mkdir()
    (project / ".maida/local.json").write_text("broken")
    assert load_config().data_dir == temp_data_dir
    result = runner.invoke(app, ["assert", trace_id, "--expect-status", "ok"])
    assert result.exit_code == 0, result.output


def test_python_baseline_from_old_scoped_storage_uses_new_python_candidate(project, temp_data_dir):
    run_good_agent()
    trace_id = resolve_latest_run_id(load_config())
    _normalize_demo_trace_duration(trace_id, load_config())
    baseline = project / "baseline.json"
    assert runner.invoke(app, ["baseline", trace_id, "--out", str(baseline)]).exit_code == 0
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    # The earlier init implementation also put SDK runs in the capture namespace.
    scoped_runs = load_config(capture=True).data_dir / "runs"
    scoped_runs.mkdir(parents=True)
    shutil.move(temp_data_dir / "runs" / trace_id, scoped_runs / trace_id)
    task(project)
    run_good_agent()
    current = resolve_latest_run_id(load_config())
    _normalize_demo_trace_duration(current, load_config())
    result = runner.invoke(app, ["assert", "--baseline", str(baseline), "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == current


def test_new_setup_does_not_add_hooks_to_shared_settings(project):
    shared = project / ".claude/settings.json"
    shared.parent.mkdir()
    original = '{"permissions": {"deny": ["Write"]}}\n'
    shared.write_text(original)
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    assert shared.read_text() == original
    local = project / ".claude/settings.local.json"
    from maida.capture_setup import bound_hook_command

    assert json.loads(local.read_text())["hooks"]["SessionStart"][0]["hooks"][0]["command"] == bound_hook_command()
    assert subprocess.run(["git", "check-ignore", str(local)], capture_output=True).returncode == 0


def test_detach_preserves_other_hooks_and_evidence_and_reinit_identity(project, temp_data_dir):
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    pointer = project / ".maida/local.json"
    identity = json.loads(pointer.read_text())["project_id"]
    task(project)
    evidence = temp_data_dir / "projects" / identity
    saved = {p.relative_to(evidence): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
    path = project / ".claude/settings.local.json"
    settings = json.loads(path.read_text())
    other = {"type": "command", "command": "existing-safety-hook", "timeout": 12}
    settings["hooks"]["PreToolUse"][0]["hooks"].append(other)
    settings["permissions"] = {"deny": ["Write"]}
    path.write_text(json.dumps(settings))
    result = runner.invoke(app, ["detach", "--agent", "claude-code"], input="y\n")
    assert result.exit_code == 0, result.output
    assert result.output.index("Warning:") < result.output.index("[y/N]")
    assert result.output.count("[y/N]") == 1
    remaining = json.loads(path.read_text())
    assert remaining["permissions"] == settings["permissions"]
    assert remaining["hooks"]["PreToolUse"][0]["hooks"] == [other]
    assert "maida capture claude-hook" not in path.read_text()
    assert saved == {p.relative_to(evidence): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
    assert json.loads(pointer.read_text())["project_id"] == identity
    # A cached hook in an already running session cannot keep writing after detach.
    task(project, "after-detach")
    assert saved == {p.relative_to(evidence): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
    assert runner.invoke(app, ["detach", "--agent", "claude-code"]).exit_code == 0
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    assert json.loads(pointer.read_text())["project_id"] == identity


def test_detach_decline_noninteractive_and_unsupported_leave_settings(project, monkeypatch):
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    path = project / ".claude/settings.local.json"
    before = path.read_bytes()
    assert runner.invoke(app, ["detach", "--agent", "claude-code"], input="n\n").exit_code == 0
    monkeypatch.setattr("maida.first_run.is_interactive", lambda: False)
    result = runner.invoke(app, ["detach", "--agent", "claude-code"])
    assert result.exit_code == 2
    assert "interactive terminal" in result.output
    assert runner.invoke(app, ["detach", "--agent", "unknown"], input="y\n").exit_code == 2
    assert path.read_bytes() == before


def test_detach_legacy_shared_hooks_with_corrupt_pointer(project):
    initialized_project = project / "legacy"
    initialized(initialized_project)
    # Removing observers must remain possible if capture metadata is damaged.
    (initialized_project / ".maida/local.json").write_text("broken")
    path = initialized_project / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "hooks": [
                                {"type": "command", "command": "maida capture claude-hook"},
                                {"type": "command", "command": "echo maida capture claude-hook"},
                            ]
                        }
                    ]
                }
            }
        )
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.chdir(initialized_project)
        result = runner.invoke(app, ["detach", "--agent", "claude-code"], input="y\n")
    assert result.exit_code == 0, result.output
    assert json.loads(path.read_text())["hooks"]["PreToolUse"][0]["hooks"] == [
        {"type": "command", "command": "echo maida capture claude-hook"}
    ]
    assert (initialized_project / ".maida/local.json").read_text() == "broken"


def test_detach_flags_tracked_shared_settings_before_confirmation(project):
    path = project / ".claude/settings.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {"hooks": {"SessionEnd": [{"hooks": [{"type": "command", "command": "maida capture claude-hook"}]}]}}
        )
    )
    subprocess.run(["git", "add", str(path)], check=True)
    before = path.read_bytes()
    result = runner.invoke(app, ["detach", "--agent", "claude-code"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "TRACKED" in result.output
    assert result.output.index("TRACKED") < result.output.index("[y/N]")
    assert "team" in result.output
    assert path.read_bytes() == before


def test_detached_hook_debug_diagnostic_is_opt_in(project, monkeypatch):
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    assert runner.invoke(app, ["detach", "--agent", "claude-code"], input="y\n").exit_code == 0
    silent = runner.invoke(app, ["capture", "claude-hook"], input="sensitive-payload")
    assert silent.exit_code == 0
    assert silent.stdout == silent.stderr == ""
    monkeypatch.setenv("MAIDA_DEBUG", "1")
    debug = runner.invoke(app, ["capture", "claude-hook"], input="sensitive-payload")
    assert debug.exit_code == 0
    assert "capture disabled" in debug.stderr
    assert "maida init --agent claude-code" in debug.stderr
    assert not debug.stdout
    assert "sensitive-payload" not in debug.stderr


def test_detach_checks_all_settings_before_writing(project):
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    local = project / ".claude/settings.local.json"
    before = local.read_bytes()
    shared = project / ".claude/settings.json"
    shared.write_text('{"hooks": {"SessionEnd": "invalid"}}')
    result = runner.invoke(app, ["detach", "--agent", "claude-code"], input="y\n")
    assert result.exit_code == 2
    assert "Repair" in result.output
    assert "rerun maida detach" in result.output
    assert local.read_bytes() == before


def test_detach_race_and_write_failure_restore_configuration(project, monkeypatch):
    import maida.first_run as first_run

    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    local = project / ".claude/settings.local.json"
    pointer = project / ".maida/local.json"
    before = local.read_bytes()
    pointer_before = pointer.read_bytes()

    def raced(*args, **kwargs):
        settings = json.loads(local.read_text())
        settings["permissions"] = {"deny": ["Read"]}
        local.write_text(json.dumps(settings))
        return True

    monkeypatch.setattr(first_run.typer, "confirm", raced)
    result = runner.invoke(app, ["detach", "--agent", "claude-code"])
    assert result.exit_code == 2
    assert "changed after preview" in result.output
    assert pointer.read_bytes() == pointer_before
    assert json.loads(local.read_text())["permissions"] == {"deny": ["Read"]}
    local.write_bytes(before)
    monkeypatch.setattr(first_run.typer, "confirm", lambda *a, **k: True)
    replace = first_run.atomic_replace

    def denied(path, content, *, expected, **kwargs):
        if path == pointer:
            raise PermissionError("fixture write denied")
        return replace(path, content, expected=expected, **kwargs)

    monkeypatch.setattr(first_run, "atomic_replace", denied)
    result = runner.invoke(app, ["detach", "--agent", "claude-code"])
    assert result.exit_code == 2
    assert "previous files were restored" in result.output
    assert local.read_bytes() == before
    assert pointer.read_bytes() == pointer_before


def test_existing_global_observers_are_reused_and_not_removed(project, temp_data_dir):
    from maida.capture_setup import bound_hook_command, merged_settings

    user = Path.home() / ".claude/settings.json"
    user.parent.mkdir()
    user.write_text(json.dumps(merged_settings({}, observer_command=bound_hook_command())[0]))
    before = user.read_bytes()
    assert runner.invoke(app, ["init"], input="y\n").exit_code == 0
    assert not (project / ".claude/settings.local.json").exists()
    assert "/.claude/settings.local.json" not in (project / ".git/info/exclude").read_text()
    task(project)
    result = runner.invoke(app, ["detach", "--agent", "claude-code"], input="y\n")
    assert result.exit_code == 0, result.output
    assert "User-wide and managed hooks" in result.output
    assert user.read_bytes() == before
    identity = json.loads((project / ".maida/local.json").read_text())["project_id"]
    evidence = temp_data_dir / "projects" / identity
    saved = {p.relative_to(evidence): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
    task(project, "cached-global-hook")
    assert saved == {p.relative_to(evidence): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}


def test_init_refuses_tracked_local_settings(project):
    local = project / ".claude/settings.local.json"
    local.parent.mkdir()
    local.write_text("{}")
    subprocess.run(["git", "add", "--force", str(local)], check=True)
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 2
    assert "git rm --cached -- .claude/settings.local.json" in result.output
    assert not (project / ".maida").exists()


def test_init_refuses_symlinked_settings(project, symlink_supported):
    local = project / ".claude/settings.local.json"
    local.parent.mkdir()
    outside = project.parent / "external-settings.json"
    outside.write_text("{}")
    local.symlink_to(outside)
    result = runner.invoke(app, ["init"], input="y\n")
    assert result.exit_code == 2
    assert "symlinked" in result.output
    assert outside.read_text() == "{}"
