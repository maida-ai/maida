"""Measure real first-run commands, without measurement changing their output."""

import json
import socket
from contextlib import contextmanager
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.cli import app
from maida.config import load_config
from tests import test_first_run
from tests.test_onboarding import v1_payload
from tests.test_project_capture import deliver, task

runner = CliRunner()
project = test_first_run.project  # Shared isolated first-run fixture.


def invoke(*args, **kwargs):
    result = runner.invoke(app, list(args), **kwargs)
    assert result.exit_code == 0, result.output
    return result


def summary():
    return json.loads(invoke("onboarding", "report", "--json").stdout)


@pytest.mark.parametrize("looping, code", [(False, 0), (True, 1)])
def test_init_capture_check_activates_even_on_fail(project, looping, code):
    invoke("onboarding", "start", "--assistance", "none", "--task-kind", "coding-agent")
    init = invoke("init", input="y\n")
    assert "onboarding" not in init.output
    assert summary()["funnel"][0]["reached"] == 1
    task(project, looping=looping)
    before = summary()
    assert before["funnel"][1]["reached"] == 1
    assert before["activated"] == 0
    result = runner.invoke(app, ["check", "--format", "json"])
    assert result.exit_code == code, result.output
    assert json.loads(result.stdout)["run_id"]
    report = summary()
    assert report["activated"] == report["unassisted"] == 1
    assert report["local_gate_verified"] == report["pr_gate_verified"] == 0
    assert len(report["time_to_first_report_seconds"]) == 1
    # Repeated init, import, hook deliveries and checks keep the original times.
    before = report
    invoke("init")
    invoke("import", "claude-code", "--session-id", "normal-task")
    assert deliver(project, "SessionEnd").exit_code == 0
    assert runner.invoke(app, ["check"]).exit_code == code
    assert summary() == before
    journal = next((Path.home() / ".maida/onboarding").glob("*.json"))
    events = json.loads(journal.read_text())["attempts"][0]["events"]
    assert [e["milestone"] for e in events] == ["setup-ready", "own-task-captured", "first-report"]


def test_setup_cancelled_or_failed_and_incomplete_capture_do_not_count(project):
    invoke("onboarding", "start")
    invoke("init", input="n\n")
    assert summary()["funnel"][0]["reached"] == 0
    assert runner.invoke(app, ["init", "--agent", "unsupported"]).exit_code == 2
    invoke("init", input="y\n")
    assert runner.invoke(app, ["check"]).exit_code == 2
    assert deliver(project, "SessionStart").exit_code == 0
    assert deliver(project, "PreToolUse", tool_use_id="one", tool_name="Read", tool_input={}).exit_code == 0
    assert deliver(project, "SessionEnd").exit_code == 0
    assert runner.invoke(app, ["check"]).exit_code == 2
    assert summary()["funnel"][1]["reached"] == summary()["activated"] == 0


def test_reviewed_init_records_only_configuration_and_pr_remains_explicit(project):
    invoke("onboarding", "start")
    invoke("init", input="y\n")
    task(project)
    first = invoke("check", "--format", "json")
    invoke("init", "--from-run", json.loads(first.stdout)["run_id"])
    assert summary()["funnel"][3]["reached"] == 0
    assert runner.invoke(app, ["init", "--reviewed"]).exit_code == 2
    invoke("init", "--reviewed", "--reason", "Task must complete without loops")
    assert summary()["funnel"][3]["reached"] == 1
    (project / "agent.py").write_text("# isolated fixture entrypoint\n")
    invoke("init", "--github", "--agent-script", "agent.py")
    assert summary()["local_gate_verified"] == summary()["pr_gate_verified"] == 0
    result = runner.invoke(app, ["onboarding", "record", "--milestone", "local-gate-verified"])
    assert result.exit_code == 2
    for milestone in ("gate-pass", "regression-caught", "repair-pass"):
        invoke("onboarding", "record", "--milestone", milestone)
    assert summary()["local_gate_verified"] == 1
    assert summary()["pr_gate_verified"] == 0
    invoke("onboarding", "record", "--milestone", "pr-gate-verified")
    assert summary()["pr_gate_verified"] == 1


@pytest.mark.parametrize("help_before", [True, False])
def test_assistance_freezes_at_first_report(project, help_before):
    invoke("onboarding", "start", "--assistance", "none")
    invoke("init", input="y\n")
    task(project)
    help_args = ("onboarding", "record", "--phase", "investigation", "--minutes", "2", "--actor", "founder")
    if help_before:
        invoke(*help_args)
    invoke("check")
    if not help_before:
        invoke(*help_args)
    assert summary()["assisted"] == int(help_before)
    assert summary()["unassisted"] == int(not help_before)
    assert summary()["effort_minutes"]["founder"]["investigation"] == 2


def test_commands_without_start_never_create_measurement_journal(project):
    invoke("init", input="y\n")
    task(project)
    invoke("check")
    invoke("import", "claude-code", "--session-id", "normal-task")
    assert not (Path.home() / ".maida/onboarding").exists()
    assert summary()["attempts"] == 0


def test_unreadable_capture_is_not_first_report(project):
    invoke("onboarding", "start")
    invoke("init", input="y\n")
    task(project)
    config = load_config(capture=True)
    receipt = next((config.data_dir / "onboarding").glob("*.json"))
    data = json.loads(receipt.read_text())
    data["trace_id"] = "a" * 32
    receipt.write_text(json.dumps(data))
    assert runner.invoke(app, ["check"]).exit_code == 2
    assert summary()["activated"] == 0


def test_measurement_never_uses_network(project, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Local onboarding must never use the network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    invoke("onboarding", "start", "--assistance", "none")
    invoke("init", input="y\n")
    task(project)
    invoke("check")
    assert summary()["activated"] == 1


def test_failed_import_and_invalid_payload_do_not_record_a_task(project, monkeypatch):
    invoke("onboarding", "start")
    invoke("init", input="y\n")
    result = runner.invoke(app, ["capture", "claude-hook"], input="bad json")
    assert result.exit_code == 10
    assert runner.invoke(app, ["import", "claude-code", "--session-id", "missing"]).exit_code == 2
    assert deliver(project, "SessionStart").exit_code == 0
    tool = {"tool_use_id": "read", "tool_name": "Read", "tool_input": {}}
    assert deliver(project, "PreToolUse", **tool).exit_code == 0
    assert deliver(project, "PostToolUse", **tool, tool_response={}).exit_code == 0

    def failed(*args, **kwargs):
        raise ValueError("fixture failed import")

    with monkeypatch.context() as patch:
        patch.setattr("maida.capture.claude_hook.import_claude_capture", failed)
        assert deliver(project, "SessionEnd").exit_code == 10
        assert summary()["funnel"][1]["reached"] == summary()["activated"] == 0
    invoke("import", "claude-code", "--session-id", "normal-task")
    assert summary()["funnel"][1]["reached"] == 1
    assert summary()["activated"] == 0


def test_quiet_measurement_failure_does_not_break_product_commands(project, monkeypatch):
    invoke("onboarding", "start")
    journal = next((Path.home() / ".maida/onboarding").glob("*.json"))
    journal.write_text("broken journal")
    before = journal.read_bytes()
    assert "onboarding" not in invoke("init", input="y\n").output
    task(project)
    assert "onboarding" not in invoke("check").output
    assert journal.read_bytes() == before
    assert runner.invoke(app, ["onboarding", "report"]).exit_code == 2


def test_new_attempt_is_shared_by_subdirectory_and_hook_origin(project, monkeypatch):
    sub = project / "src"
    sub.mkdir()
    monkeypatch.chdir(sub)
    invoke("onboarding", "start")
    invoke("init", input="y\n")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project))
    task(sub)
    invoke("check")
    monkeypatch.chdir(project)
    assert summary()["activated"] == 1


def test_ended_attempts_ignore_automatic_recording(project):
    invoke("onboarding", "start")
    invoke("onboarding", "record", "--outcome", "abandoned")
    before = summary()
    invoke("init", input="y\n")
    task(project)
    invoke("check")
    assert summary() == before
    result = runner.invoke(app, ["onboarding", "record", "--phase", "maintenance", "--minutes", "1"])
    assert result.exit_code == 2
    assert "attempt has ended" in result.stderr
    assert summary() == before


@pytest.mark.parametrize("completed", [True, False])
def test_v1_journal_stays_untouched_during_product_commands_and_requires_archiving(project, completed):
    invoke("onboarding", "start")
    journal = next((Path.home() / ".maida/onboarding").glob("*.json"))
    payload = v1_payload()
    if not completed:
        attempt = payload["attempts"][0]
        attempt["outcome"] = "in-progress"
        attempt["events"] = attempt["events"][:1]
        attempt.pop("activated_at")
        attempt.pop("activation_assistance")
    journal.write_text(json.dumps(payload))
    before = journal.read_bytes()
    for args in (["onboarding", "report"], ["onboarding", "start"]):
        result = runner.invoke(app, args)
        assert result.exit_code == 2
        assert "archive" in result.stderr
    assert "onboarding" not in invoke("init", input="y\n").output
    task(project)
    first = invoke("check", "--format", "json")
    invoke("init", "--from-run", json.loads(first.stdout)["run_id"])
    invoke("init", "--reviewed", "--reason", "Task must complete without loops")
    assert journal.read_bytes() == before
    archived = journal.with_suffix(".archived")
    journal.rename(archived)  # Explicit user archival, never automatic migration.
    invoke("onboarding", "start", "--assistance", "none")
    invoke("init")
    task(project, session="new-v2-task")
    invoke("check")
    report = summary()
    assert report["attempts"] == report["activated"] == 1
    assert len(report["time_to_first_report_seconds"]) == 1
    assert json.loads(journal.read_text())["journal_version"] == 2
    assert archived.read_bytes() == before


@pytest.mark.parametrize("failure", ["lock", "save"])
def test_measurement_io_failures_do_not_change_product_outcomes(project, monkeypatch, failure):
    invoke("onboarding", "start")

    @contextmanager
    def denied_lock(*args):
        raise PermissionError("measurement lock unavailable")
        yield  # pragma: no cover

    def denied_save(*args):
        raise PermissionError("measurement save unavailable")

    monkeypatch.setattr(
        "maida.onboarding.file_lock" if failure == "lock" else "maida.onboarding._save",
        denied_lock if failure == "lock" else denied_save,
    )
    assert "onboarding" not in invoke("init", input="y\n").output
    task(project)
    assert "onboarding" not in invoke("check").output
    assert summary()["activated"] == 0
