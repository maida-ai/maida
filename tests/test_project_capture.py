"""Initialized projects must never report another repository's evidence."""

import json
import subprocess
import uuid

import pytest
from typer.testing import CliRunner

from maida.cli import app
from maida.config import load_config

runner = CliRunner()


def initialized(root):
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".maida").mkdir()
    project_id = uuid.uuid4().hex
    (root / ".maida/local.json").write_text(
        json.dumps({"version": 1, "project_id": project_id, "capture": "claude-code"})
    )
    return project_id


def deliver(root, event, *, session="normal-task", **extra):
    payload = {"session_id": session, "cwd": str(root), "hook_event_name": event, **extra}
    if event == "SessionStart":
        payload["source"] = "startup"
    if event == "SessionEnd":
        payload["reason"] = "other"
    return runner.invoke(app, ["capture", "claude-hook"], input=json.dumps(payload))


def task(root, session="normal-task", failure=False):
    assert deliver(root, "SessionStart", session=session).exit_code == 0
    tool = {"tool_use_id": "one", "tool_name": "Read", "tool_input": {"file_path": "pyproject.toml"}}
    assert deliver(root, "PreToolUse", session=session, **tool).exit_code == 0
    terminal = "PostToolUseFailure" if failure else "PostToolUse"
    extra = {"error": "fixture failure"} if failure else {"tool_response": {"content": "pytest"}}
    assert deliver(root, terminal, session=session, **tool, **extra).exit_code == 0
    assert deliver(root, "SessionEnd", session=session).exit_code == 0


def report():
    return runner.invoke(app, ["assert", "--expect-status", "ok", "--no-loops", "--no-guardrails"])


def test_project_storage_and_subdirectories(tmp_path, temp_data_dir, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    first_id, second_id = initialized(one), initialized(two)
    monkeypatch.chdir(one)
    assert load_config(capture=True).data_dir == temp_data_dir / "projects" / first_id
    sub = one / "src"
    sub.mkdir()
    monkeypatch.chdir(sub)
    assert load_config(capture=True).data_dir == temp_data_dir / "projects" / first_id
    assert load_config(project_root=two, capture=True).data_dir == temp_data_dir / "projects" / second_id


def test_first_capture_report_and_isolation(tmp_path, temp_data_dir, monkeypatch):
    one, two = tmp_path / "one", tmp_path / "two"
    initialized(one)
    initialized(two)
    # An unrelated run in the global storage must never become the first report.
    monkeypatch.chdir(tmp_path)
    task(tmp_path, "unrelated-global")
    monkeypatch.chdir(one)
    missing = report()
    assert missing.exit_code == 2
    assert "new Claude Code session" in missing.output
    task(one)
    result = report()
    assert result.exit_code == 0, result.output
    assert "3 active checks passed" in result.stdout
    assert "Coverage:" in result.stdout
    assert str(one) in result.stdout
    monkeypatch.chdir(two)
    assert report().exit_code == 2
    task(two, "failing-task", failure=True)
    assert report().exit_code == 1


def test_new_active_session_does_not_fall_back(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    task(tmp_path, "completed")
    assert report().exit_code == 0
    assert deliver(tmp_path, "SessionStart", session="unfinished").exit_code == 0
    result = report()
    assert result.exit_code == 2
    assert "Finish" in result.output


def test_empty_or_unpaired_capture_is_not_approval(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert deliver(tmp_path, "SessionStart").exit_code == 0
    assert deliver(tmp_path, "SessionEnd").exit_code == 0
    assert report().exit_code == 2
    assert "bounded task" in report().output


def test_unpaired_tool_and_import_failure_are_setup_errors(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert deliver(tmp_path, "SessionStart").exit_code == 0
    assert deliver(tmp_path, "PreToolUse", tool_use_id="one", tool_name="Read", tool_input={}).exit_code == 0
    assert deliver(tmp_path, "SessionEnd").exit_code == 0
    assert report().exit_code == 2
    monkeypatch.setattr(
        "maida.capture.claude_hook.import_claude_capture", lambda *a, **k: (_ for _ in ()).throw(ValueError("fixture"))
    )
    assert deliver(tmp_path, "SessionStart", session="cannot-import").exit_code == 0
    tool = {"tool_use_id": "one", "tool_name": "Read", "tool_input": {}}
    assert deliver(tmp_path, "PreToolUse", session="cannot-import", **tool).exit_code == 0
    assert deliver(tmp_path, "PostToolUse", session="cannot-import", **tool, tool_response={}).exit_code == 0
    result = deliver(tmp_path, "SessionEnd", session="cannot-import")
    assert result.exit_code == 10
    failed = report()
    assert failed.exit_code == 2
    assert "could not be imported" in failed.output


def test_explicit_selection_still_works_and_json_stays_machine_readable(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    task(tmp_path)
    receipt = json.loads(next((load_config(capture=True).data_dir / "onboarding").glob("*.json")).read_text())
    result = runner.invoke(app, ["assert", receipt["trace_id"], "--expect-status", "ok", "--format", "json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["run_id"] == receipt["trace_id"]
    assert deliver(tmp_path, "SessionStart", session="unfinished").exit_code == 0
    assert runner.invoke(app, ["assert", receipt["trace_id"], "--expect-status", "ok"]).exit_code == 0


def test_default_home_storage_and_nested_repository_boundary(tmp_path, monkeypatch):
    from pathlib import Path

    project = tmp_path / "repo"
    project_id = initialized(project)
    monkeypatch.delenv("MAIDA_DATA_DIR", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path / "home"))
    monkeypatch.chdir(project)
    assert load_config(capture=True).data_dir == tmp_path / "home/.maida/projects" / project_id
    nested = project / "nested"
    nested.mkdir()
    subprocess.run(["git", "init", "--quiet", str(nested)], check=True)
    monkeypatch.chdir(nested)
    assert load_config(capture=True).project_id is None


def test_receipt_corruption_is_not_silently_ignored(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    task(tmp_path)
    next((load_config(capture=True).data_dir / "onboarding").glob("*.json")).write_text("broken")
    result = report()
    assert result.exit_code == 2
    assert "capture state is unreadable" in result.output


def test_malformed_pointer_is_actionable(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    (tmp_path / ".maida/local.json").write_text("broken JSON")
    monkeypatch.chdir(tmp_path)
    result = report()
    assert result.exit_code == 2
    assert "Move it aside" in result.output


def test_capture_uses_original_project_after_cd(tmp_path, temp_data_dir, monkeypatch):
    project, outside = tmp_path / "repo", tmp_path / "elsewhere"
    project_id = initialized(project)
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    task(outside)
    monkeypatch.chdir(project)
    assert report().exit_code == 0
    assert (temp_data_dir / "projects" / project_id / "runs").is_dir()
    assert not (temp_data_dir / "runs").exists()


@pytest.mark.parametrize(
    "pointer", [{"version": 9}, {"version": 1, "project_id": "../../escape", "capture": "claude-code"}]
)
def test_invalid_pointer_never_falls_back(tmp_path, temp_data_dir, monkeypatch, pointer):
    initialized(tmp_path)
    (tmp_path / ".maida/local.json").write_text(json.dumps(pointer))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="local.json"):
        load_config(capture=True)
