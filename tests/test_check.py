"""The first-run check owns Claude selection; assertion flags never change it."""

import json
import shlex

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from maida.cli import app
from maida.config import load_config
from maida.demo import run_good_agent
from maida.storage import load_run_for_analysis, resolve_latest_run_id
from tests.test_project_capture import deliver, initialized, task

runner = CliRunner()
ASSERT_FLAGS = ["--expect-status", "ok", "--no-loops", "--no-guardrails"]


@pytest.fixture
def project(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_check_and_printed_viewer_select_the_same_task(project, monkeypatch):
    task(project)
    captured = resolve_latest_run_id(load_config(capture=True))
    run_good_agent()
    sdk = resolve_latest_run_id(load_config())
    # First-run checks do not depend on policy configuration.
    (project / ".maida/policy.yaml").write_text("invalid policy")
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    assert "3 active checks passed" in result.stdout
    assert captured in result.stdout
    assert sdk not in result.stdout
    assert "Coverage:" in result.stdout
    viewer = next(line for line in result.stdout.splitlines() if line.startswith("View: "))
    selected = {}

    def serve(*, app, **kwargs):
        with TestClient(app) as client:
            selected.update(client.get(f"/api/runs/{captured}").json())

    monkeypatch.setattr("uvicorn.run", serve)
    command = shlex.split(viewer.removeprefix("View: "))
    viewed = runner.invoke(app, [*command[command.index("maida") + 1 :], "--no-browser", "--json"])
    assert viewed.exit_code == 0, viewed.output
    assert json.loads(viewed.stdout)["run_id"] == selected["trace_id"] == captured


@pytest.mark.parametrize("flags", [[], ASSERT_FLAGS, ASSERT_FLAGS[:-1], ASSERT_FLAGS + ["--max-steps", "100"]])
def test_assert_flags_never_switch_to_claude(project, flags):
    task(project)
    run_good_agent()
    sdk = resolve_latest_run_id(load_config())
    result = runner.invoke(app, ["assert", *flags, "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == sdk


def test_check_json_keeps_output_machine_readable(project):
    task(project)
    capture = resolve_latest_run_id(load_config(capture=True))
    result = runner.invoke(app, ["check", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == capture
    assert f"maida view {capture}" in result.stderr


def test_check_failure_still_prints_task_and_viewer(project):
    task(project, looping=True)
    captured = resolve_latest_run_id(load_config(capture=True))
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 1, result.output
    assert f"maida view {captured}" in result.stdout


def test_check_completed_session_with_child_failure_passes(project):
    task(project, failure=True)
    config = load_config(capture=True)
    captured = resolve_latest_run_id(config)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 0, result.output
    assert "status is 'ok'" in result.stdout
    _, meta, events = load_run_for_analysis(captured, config)
    assert meta["status"] == "ok"
    assert meta["counts"]["errors"] == 1
    assert next(event for event in events if event["event_type"] == "TOOL_CALL")["payload"]["status"] == "error"


def test_missing_and_unfinished_capture_never_fall_back(project):
    run_good_agent()
    missing = runner.invoke(app, ["check"])
    assert missing.exit_code == 2, missing.output
    assert "new Claude Code session" in missing.stderr
    assert "maida check" in missing.stderr
    task(project)
    assert deliver(project, "SessionStart", session="unfinished").exit_code == 0
    incomplete = runner.invoke(app, ["check"])
    assert incomplete.exit_code == 2, incomplete.output
    assert "Finish and exit" in incomplete.stderr


def test_check_without_setup_or_after_detach_has_one_setup_action(project):
    pointer = project / ".maida/local.json"
    data = json.loads(pointer.read_text())
    pointer.unlink()
    missing = runner.invoke(app, ["check"])
    assert missing.exit_code == 2, missing.output
    assert "maida init" in missing.stderr
    data["providers"]["claude-code"]["enabled"] = False
    pointer.write_text(json.dumps(data))
    detached = runner.invoke(app, ["check"])
    assert detached.exit_code == 2, detached.output
    assert "maida init" in detached.stderr


def test_missing_imported_trace_reports_capture_recovery(project):
    task(project)
    receipt = next((load_config(capture=True).data_dir / "onboarding").glob("*.json"))
    data = json.loads(receipt.read_text())
    data["trace_id"] = "a" * 32
    receipt.write_text(json.dumps(data))
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 2, result.output
    assert "new Claude Code session" in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("launcher", [None, "1"])
def test_check_prints_usable_command_for_its_launcher(project, monkeypatch, launcher):
    if launcher:
        monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", launcher)
    else:
        monkeypatch.delenv("UV_RUN_RECURSION_DEPTH", raising=False)
    task(project)
    result = runner.invoke(app, ["check"])
    prefix = "uv run maida" if launcher else "maida"
    assert result.exit_code == 0, result.output
    assert f"View: {prefix} view " in result.stdout


def test_check_markdown_preserves_report_output(project):
    task(project)
    result = runner.invoke(app, ["check", "--format", "markdown"])
    assert result.exit_code == 0, result.output
    assert "Maida verdict: pass" in result.stdout
    assert "View:" not in result.stdout
    assert "maida view " in result.stderr


def test_check_invalid_format_has_one_recovery(project):
    result = runner.invoke(app, ["check", "--format", "unknown"])
    assert result.exit_code == 2, result.output
    assert "maida check --format text" in result.stderr
    assert not result.stdout


@pytest.mark.parametrize(
    "error, code, recovery", [(PermissionError(), 2, "permissions"), (RuntimeError(), 10, "Rerun")]
)
def test_check_expected_access_and_internal_errors_have_recovery(project, monkeypatch, error, code, recovery):
    task(project)

    def broken(*args, **kwargs):
        raise error

    monkeypatch.setattr("maida._cli.gating.run_assertions", broken)
    result = runner.invoke(app, ["check"])
    assert result.exit_code == code, result.output
    assert recovery in result.stderr
    assert "maida check" in result.stderr
    assert not result.stdout
