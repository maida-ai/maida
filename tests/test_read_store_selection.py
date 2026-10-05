"""Attaching Claude capture preserves ordinary SDK/Python read defaults."""

import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from maida.cli import app
from maida.config import load_config
from maida.demo import run_good_agent, run_refactored_agent
from maida.storage import resolve_latest_run_id
from tests.test_project_capture import initialized, task

runner = CliRunner()
FIRST_CHECK = ["check"]
ASSERT_CHECKS = ["assert", "--expect-status", "ok", "--no-loops", "--no-guardrails"]


@pytest.fixture
def runs(tmp_path, temp_data_dir, monkeypatch):
    project = tmp_path / "repo"
    initialized(project)
    monkeypatch.chdir(project)
    task(project)
    capture = resolve_latest_run_id(load_config(capture=True))
    run_good_agent()
    sdk = resolve_latest_run_id(load_config())
    return project, capture, sdk


@pytest.mark.parametrize("detached", [False, True])
def test_ordinary_reads_keep_sdk_defaults_after_attach_and_detach(runs, detached):
    project, _, sdk = runs
    if detached:
        pointer = project / ".maida/local.json"
        data = json.loads(pointer.read_text())
        data["providers"]["claude-code"]["enabled"] = False
        pointer.write_text(json.dumps(data))
    listed = runner.invoke(app, ["list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert [run["trace_id"] for run in json.loads(listed.stdout)["runs"]] == [sdk]
    asserted = runner.invoke(app, ["assert", "--expect-status", "ok", "--format", "json"])
    assert asserted.exit_code == 0, asserted.output
    assert json.loads(asserted.stdout)["run_id"] == sdk
    exported = runner.invoke(app, ["export", "--out", "export.json"])
    assert exported.exit_code == 0, exported.output
    assert json.loads((project / "export.json").read_text())["run"]["trace_id"] == sdk
    baselined = runner.invoke(app, ["baseline", "--out", "baseline.json"])
    assert baselined.exit_code == 0, baselined.output
    assert json.loads((project / "baseline.json").read_text())["source_run_id"] == sdk
    diffed = runner.invoke(app, ["diff", "--baseline", "baseline.json"])
    assert diffed.exit_code == 0, diffed.output
    assert sdk[:8] in diffed.stderr
    gated = runner.invoke(app, [*ASSERT_CHECKS, "--baseline", "baseline.json", "--format", "json"])
    assert gated.exit_code == 0, gated.output
    assert json.loads(gated.stdout)["run_id"] == sdk
    accepted = runner.invoke(app, ["accept", "--baseline", "baseline.json", "--reason", "reviewed"])
    assert accepted.exit_code == 0, accepted.output
    assert sdk[:8] in accepted.output
    # Starter selection is an SDK read too; reviewed Claude selections use IDs.
    draft = runner.invoke(app, ["init", "--from-run", "latest"])
    assert draft.exit_code == 0, draft.output
    assert json.loads((project / ".maida/starter/review.json").read_text())["source_trace_ids"] == [sdk]


@pytest.mark.parametrize("selected", [None, "sdk", "capture"])
def test_view_selection_and_http_api_agree(runs, selected, monkeypatch):
    _, capture, sdk = runs
    expected = capture if selected == "capture" else sdk
    received = {}

    def serve(*, app, **kwargs):
        with TestClient(app) as client:
            received["runs"] = client.get("/api/runs").json()["runs"]
            received["meta"] = client.get(f"/api/runs/{expected}").json()

    monkeypatch.setattr("uvicorn.run", serve)
    args = [expected] if selected else []
    result = runner.invoke(app, ["view", *args, "--no-browser", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == expected
    assert [run["trace_id"] for run in received["runs"]] == [expected]
    assert received["meta"]["trace_id"] == expected


def test_only_onboarding_check_refuses_unrelated_sdk_fallback(runs):
    project, _, sdk = runs
    pointer = project / ".maida/local.json"
    data = json.loads(pointer.read_text())
    data["project_id"] = "b" * 32
    pointer.write_text(json.dumps(data))
    assert [run["trace_id"] for run in json.loads(runner.invoke(app, ["list", "--json"]).stdout)["runs"]] == [sdk]
    result = runner.invoke(app, FIRST_CHECK)
    assert result.exit_code == 2, result.output
    assert "No Claude Code task captured" in result.stderr


def test_empty_sdk_store_never_implicitly_selects_a_capture(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    task(tmp_path)
    assert json.loads(runner.invoke(app, ["list", "--json"]).stdout)["runs"] == []
    for command in (["assert"], ["export", "--out", "out.json"], ["baseline", "--out", "baseline.json"]):
        result = runner.invoke(app, command)
        assert result.exit_code == 2, result.output
    assert runner.invoke(app, FIRST_CHECK).exit_code == 0


def test_bad_capture_pointer_does_not_affect_ordinary_reads(runs):
    project, _, sdk = runs
    (project / ".maida/local.json").write_text("malformed")
    listed = runner.invoke(app, ["list", "--json"])
    assert listed.exit_code == 0, listed.output
    assert [run["trace_id"] for run in json.loads(listed.stdout)["runs"]] == [sdk]
    result = runner.invoke(app, ["assert", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == sdk
    baseline = runner.invoke(app, ["baseline", "--out", "sdk-baseline.json"])
    assert baseline.exit_code == 0, baseline.output
    gated = runner.invoke(app, ["assert", "--baseline", "sdk-baseline.json", "--format", "json"])
    assert gated.exit_code == 0, gated.output
    assert json.loads(gated.stdout)["run_id"] == sdk


@pytest.mark.parametrize("command", ["export", "view", "diff"])
def test_damaged_pointer_reports_recovery_for_explicit_capture_reads(runs, command):
    project, capture, _ = runs
    (project / ".maida/local.json").write_text("malformed")
    options = ["--out", "claude.json"] if command == "export" else []
    result = runner.invoke(app, [command, capture, *options])
    assert result.exit_code == 2, result.output
    assert "local.json" in result.stderr
    assert "aside and rerun maida init" in result.stderr
    assert not result.stdout


@pytest.mark.parametrize("detached", [False, True])
def test_explicit_capture_ids_remain_readable(runs, detached):
    project, capture, _ = runs
    if detached:
        pointer = project / ".maida/local.json"
        data = json.loads(pointer.read_text())
        data["providers"]["claude-code"]["enabled"] = False
        pointer.write_text(json.dumps(data))
    result = runner.invoke(app, ["export", capture[:8], "--out", "claude.json"])
    assert result.exit_code == 0, result.output
    assert json.loads((project / "claude.json").read_text())["run"]["trace_id"] == capture
    baseline = runner.invoke(app, ["baseline", capture, "--out", "claude-baseline.json"])
    assert baseline.exit_code == 0, baseline.output
    gated = runner.invoke(app, ["assert", capture, "--baseline", "claude-baseline.json", "--format", "json"])
    assert gated.exit_code == 0, gated.output
    assert json.loads(gated.stdout)["run_id"] == capture


@pytest.mark.parametrize("extra", [["--max-steps", "100"], ["--ignore-check", "step_count"], ["--no-new-tools"]])
def test_additional_sdk_checks_do_not_enter_onboarding_path(runs, extra):
    _, _, sdk = runs
    result = runner.invoke(app, [*ASSERT_CHECKS, *extra, "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == sdk


def test_explicit_policy_keeps_sdk_selection(runs):
    project, _, sdk = runs
    (project / "sdk-policy.yaml").write_text("version: 2\nmetrics: {}\n")
    result = runner.invoke(app, [*ASSERT_CHECKS, "--policy", "sdk-policy.yaml", "--format", "json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["run_id"] == sdk


def test_passing_capture_cannot_hide_a_failing_python_gate(runs):
    _, capture, sdk = runs
    assert runner.invoke(app, ["baseline", sdk, "--out", "python.json"]).exit_code == 0
    run_refactored_agent()
    candidate = resolve_latest_run_id(load_config())
    result = runner.invoke(app, ["assert", "--baseline", "python.json", "--no-new-tools", "--format", "json"])
    assert result.exit_code == 1, result.output
    assert json.loads(result.stdout)["run_id"] == candidate
    first_check = runner.invoke(app, [*FIRST_CHECK, "--format", "json"])
    assert first_check.exit_code == 0, first_check.output
    assert json.loads(first_check.stdout)["run_id"] == capture


@pytest.mark.parametrize("command", ["list", "view", "baseline", "export", "diff", "accept", "assert", "init"])
def test_no_public_store_switches(command):
    result = runner.invoke(app, [command, "--help"])
    assert result.exit_code == 0
    assert "--store" not in result.stdout
