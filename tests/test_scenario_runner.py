"""Tests for isolated, capture-backed Claude Code scenarios."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from urllib.request import urlopen

import pytest

from maida.scenario import (
    ClaudeProcessOutcome,
    ScenarioInputError,
    ScenarioRunReport,
    ScenarioStatus,
    _claude_receiver,
    _run_claude_process,
    load_scenario_manifest,
    run_scenario_manifest,
)


def _git(project: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )


def _project(tmp_path: Path, *, scenario_ids: tuple[str, ...] = ("edit",)) -> Path:
    project = tmp_path / "project"
    fixture = project / "fixtures" / "workspace"
    fixture.mkdir(parents=True)
    (fixture / "input.txt").write_text("before\n", encoding="utf-8")
    (fixture / ".claude").mkdir()
    (fixture / ".claude" / "settings.json").write_text(
        json.dumps({"permissions": {"defaultMode": "default"}}),
        encoding="utf-8",
    )
    (fixture / ".mcp.json").write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
    (project / "baselines").mkdir()
    (project / "baselines" / "expected.json").write_text(
        json.dumps(
            {
                "schema_version": "0.3.0",
                "source_run_id": "0" * 32,
                "source_run_name": "expected",
                "summary": {},
                "tool_path": [],
                "tool_call_sequence": [],
                "tool_call_counts": {},
                "llm_models_used": [],
                "event_type_sequence": [],
                "guardrail_events": [],
            }
        ),
        encoding="utf-8",
    )
    (project / "policies").mkdir()
    (project / "policies" / "gate.yaml").write_text(
        "version: 2\n"
        "trials: 1\n"
        "fail_fast: true\n"
        "metrics:\n"
        "  forbidden_tools: {kind: invariant, none_of: [Bash]}\n",
        encoding="utf-8",
    )
    (project / "ignored-secret.txt").write_text(
        "must not enter workspace", encoding="utf-8"
    )

    scenarios = [
        {
            "id": scenario_id,
            "fixture": {
                "root": "fixtures/workspace",
                "files": ["input.txt", ".claude/settings.json", ".mcp.json"],
            },
            "prompt": f"Update input.txt for {scenario_id}.",
            "baseline": "baselines/expected.json",
            "policy": "policies/gate.yaml",
        }
        for scenario_id in scenario_ids
    ]
    manifest = {
        "version": 1,
        "claude": {
            "executable": "claude",
            "version": "2.1.220",
            "model": "claude-haiku-4-5-20251001",
            "settings": ".claude/settings.json",
            "mcp_config": ".mcp.json",
            "timeout_seconds": 60,
            "max_budget_usd": 0.10,
            "max_turns": 2,
            "allowed_tools": ["Read", "Write"],
        },
        "scenarios": scenarios,
    }
    maida_dir = project / ".maida"
    maida_dir.mkdir()
    (maida_dir / "scenarios.yaml").write_text(
        __import__("yaml").safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
    _git(project, "init", "--quiet")
    _git(
        project,
        "add",
        "--force",
        ".maida/scenarios.yaml",
        "fixtures/workspace/input.txt",
        "fixtures/workspace/.claude/settings.json",
        "fixtures/workspace/.mcp.json",
        "baselines/expected.json",
        "policies/gate.yaml",
    )
    return project


class _Evaluation:
    def __init__(self, passed: bool):
        self.passed = passed

    def render(self, output_format: str, *, baseline_path=None) -> str:
        del baseline_path
        if output_format == "json":
            return json.dumps({"passed": self.passed, "results": []})
        if output_format == "markdown":
            return "## Maida verdict: " + ("pass" if self.passed else "fail")
        return "RESULT: " + ("PASSED" if self.passed else "FAILED")


class _Imported:
    trace_id = "a" * 32


@contextmanager
def _receiver(_config):
    yield "http://127.0.0.1:43210"


def test_manifest_and_runner_isolate_workspace_and_harden_claude_argv(
    tmp_path, temp_data_dir
):
    project = _project(tmp_path)
    manifest = load_scenario_manifest(
        project / ".maida" / "scenarios.yaml", project_root=project
    )
    observed: dict[str, object] = {}

    def version_runner(argv, **kwargs):
        observed["version_argv"] = argv
        return subprocess.CompletedProcess(argv, 0, "2.1.220 (Claude Code)\n", "")

    def process_runner(argv, *, cwd, env, timeout_seconds):
        observed.update(argv=argv, cwd=cwd, env=env, timeout=timeout_seconds)
        assert sorted(
            path.relative_to(cwd).as_posix()
            for path in cwd.rglob("*")
            if path.is_file()
        ) == [".claude/settings.json", ".mcp.json", "input.txt"]
        assert not (cwd / "ignored-secret.txt").exists()
        return ClaudeProcessOutcome(returncode=0, timed_out=False, cost_usd=0.01)

    report = run_scenario_manifest(
        manifest,
        config=__import__("maida.config", fromlist=["load_config"]).load_config(),
        version_runner=version_runner,
        process_runner=process_runner,
        receiver_factory=_receiver,
        capture_importer=lambda session_id, config: _Imported(),
        evaluator=lambda *args, **kwargs: _Evaluation(True),
        session_id_factory=lambda: "11111111-1111-4111-8111-111111111111",
        environment={
            "PATH": os.environ["PATH"],
            "ANTHROPIC_API_KEY": "test-credential",
            "UNRELATED_SECRET": "must-not-leak",
        },
    )

    assert report.exit_code == 0
    assert report.results[0].status is ScenarioStatus.PASS
    assert observed["version_argv"] == ["claude", "--version"]
    argv = observed["argv"]
    assert isinstance(argv, list)
    assert argv[:3] == ["claude", "-p", "Update input.txt for edit."]
    assert "--model" in argv
    assert "claude-haiku-4-5-20251001" in argv
    assert "--setting-sources" in argv and "project" in argv
    assert "--strict-mcp-config" in argv
    assert "--no-session-persistence" in argv
    assert "--permission-mode" in argv and "dontAsk" in argv
    assert "--dangerously-skip-permissions" not in argv
    assert argv[argv.index("--max-budget-usd") + 1] == "0.1"
    assert argv[argv.index("--max-turns") + 1] == "2"
    assert observed["timeout"] == 60
    env = observed["env"]
    assert isinstance(env, dict)
    assert env["OTEL_EXPORTER_OTLP_ENDPOINT"] == "http://127.0.0.1:43210"
    assert env["OTEL_LOG_USER_PROMPTS"] == "0"
    assert env["OTEL_LOG_TOOL_DETAILS"] == "0"
    assert env["ANTHROPIC_API_KEY"] == "test-credential"
    assert "UNRELATED_SECRET" not in env


def test_agent_failure_precedes_assertion_failure_and_never_reports_raw_output(
    tmp_path, temp_data_dir
):
    project = _project(tmp_path, scenario_ids=("regression", "crash"))
    manifest = load_scenario_manifest(
        project / ".maida" / "scenarios.yaml", project_root=project
    )
    calls = iter(
        [
            ClaudeProcessOutcome(0, False, 0.02),
            ClaudeProcessOutcome(7, False, None),
        ]
    )

    report = run_scenario_manifest(
        manifest,
        config=__import__("maida.config", fromlist=["load_config"]).load_config(),
        version_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, "2.1.220\n", ""
        ),
        process_runner=lambda *args, **kwargs: next(calls),
        receiver_factory=_receiver,
        capture_importer=lambda *args, **kwargs: _Imported(),
        evaluator=lambda *args, **kwargs: _Evaluation(False),
        session_id_factory=lambda: "22222222-2222-4222-8222-222222222222",
    )

    assert report.exit_code == 10
    assert [result.status for result in report.results] == [
        ScenarioStatus.ASSERTION_FAILED,
        ScenarioStatus.AGENT_FAILED,
    ]
    rendered = "\n".join(report.render(fmt) for fmt in ("text", "json", "markdown"))
    assert "stdout" not in rendered.lower()
    assert "stderr" not in rendered.lower()
    assert "must not enter workspace" not in rendered
    assert "process_exit" in rendered


def test_capture_import_failure_is_sanitized_agent_failure(tmp_path, temp_data_dir):
    project = _project(tmp_path)
    manifest = load_scenario_manifest(
        project / ".maida" / "scenarios.yaml", project_root=project
    )

    def fail_import(*args, **kwargs):
        raise RuntimeError("private agent stream content")

    report = run_scenario_manifest(
        manifest,
        config=__import__("maida.config", fromlist=["load_config"]).load_config(),
        version_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, "2.1.220\n", ""
        ),
        process_runner=lambda *args, **kwargs: ClaudeProcessOutcome(0, False, 0.01),
        receiver_factory=_receiver,
        capture_importer=fail_import,
    )

    assert report.exit_code == 10
    assert report.results[0].failure_reason == "capture_import"
    assert "private agent stream content" not in report.render("json")


def test_scenario_selection_runs_only_requested_id_and_rejects_missing(
    tmp_path, temp_data_dir
):
    project = _project(tmp_path, scenario_ids=("first", "second"))
    manifest = load_scenario_manifest(
        project / ".maida" / "scenarios.yaml", project_root=project
    )
    calls = 0

    def process_runner(*args, **kwargs):
        nonlocal calls
        calls += 1
        return ClaudeProcessOutcome(0, False, 0.01)

    report = run_scenario_manifest(
        manifest,
        config=__import__("maida.config", fromlist=["load_config"]).load_config(),
        scenario_id="second",
        version_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, "2.1.220\n", ""
        ),
        process_runner=process_runner,
        receiver_factory=_receiver,
        capture_importer=lambda *args, **kwargs: _Imported(),
        evaluator=lambda *args, **kwargs: _Evaluation(True),
    )
    assert calls == 1
    assert [item.scenario_id for item in report.results] == ["second"]

    with pytest.raises(ScenarioInputError, match="was not found"):
        run_scenario_manifest(
            manifest,
            config=__import__("maida.config", fromlist=["load_config"]).load_config(),
            scenario_id="missing",
        )


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (ClaudeProcessOutcome(-15, True, None), "timeout"),
        (ClaudeProcessOutcome(0, False, 0.11), "budget_exceeded"),
    ],
)
def test_timeout_and_budget_failures_are_agent_failures(
    tmp_path, temp_data_dir, outcome, reason
):
    project = _project(tmp_path)
    manifest = load_scenario_manifest(
        project / ".maida" / "scenarios.yaml", project_root=project
    )
    report = run_scenario_manifest(
        manifest,
        config=__import__("maida.config", fromlist=["load_config"]).load_config(),
        version_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 0, "2.1.220\n", ""
        ),
        process_runner=lambda *args, **kwargs: outcome,
        receiver_factory=_receiver,
    )
    assert report.exit_code == 10
    assert report.results[0].failure_reason == reason


def test_manifest_rejects_aliases_duplicates_untracked_and_unsafe_config(tmp_path):
    project = _project(tmp_path)
    path = project / ".maida" / "scenarios.yaml"
    original = __import__("yaml").safe_load(path.read_text(encoding="utf-8"))

    cases = []
    alias = json.loads(json.dumps(original))
    alias["claude"]["model"] = "haiku"
    cases.append((alias, "full Claude model ID"))
    canned_alias = json.loads(json.dumps(original))
    canned_alias["claude"]["model"] = "claude-test"
    cases.append((canned_alias, "full Claude model ID"))
    duplicate = json.loads(json.dumps(original))
    duplicate["scenarios"].append(dict(duplicate["scenarios"][0]))
    cases.append((duplicate, "scenario IDs must be unique"))
    untracked = json.loads(json.dumps(original))
    untracked["scenarios"][0]["fixture"]["files"].append("untracked.txt")
    (project / "fixtures" / "workspace" / "untracked.txt").write_text(
        "not tracked", encoding="utf-8"
    )
    cases.append((untracked, "tracked file"))
    wildcard = json.loads(json.dumps(original))
    wildcard["claude"]["allowed_tools"] = ["*"]
    cases.append((wildcard, "wildcards"))
    traversal = json.loads(json.dumps(original))
    traversal["scenarios"][0]["fixture"]["root"] = "../outside"
    cases.append((traversal, "traversal-safe"))

    for payload, message in cases:
        path.write_text(
            __import__("yaml").safe_dump(payload, sort_keys=False), encoding="utf-8"
        )
        with pytest.raises(ScenarioInputError, match=message):
            load_scenario_manifest(path, project_root=project)

    unsafe_settings = project / "fixtures" / "workspace" / ".claude" / "settings.json"
    unsafe_settings.write_text(
        json.dumps({"permissions": {"defaultMode": "bypassPermissions"}}),
        encoding="utf-8",
    )
    _git(project, "add", "--force", "fixtures/workspace/.claude/settings.json")
    path.write_text(
        __import__("yaml").safe_dump(original, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(ScenarioInputError, match="bypass permissions"):
        load_scenario_manifest(path, project_root=project)


def test_version_mismatch_is_preflight_input_error(tmp_path, temp_data_dir):
    project = _project(tmp_path)
    manifest = load_scenario_manifest(
        project / ".maida" / "scenarios.yaml", project_root=project
    )
    with pytest.raises(ScenarioInputError, match="requires Claude Code 2.1.220"):
        run_scenario_manifest(
            manifest,
            config=__import__("maida.config", fromlist=["load_config"]).load_config(),
            version_runner=lambda argv, **kwargs: subprocess.CompletedProcess(
                argv, 0, "2.1.221\n", ""
            ),
        )


def test_process_runner_times_out_without_retaining_raw_streams(tmp_path):
    outcome = _run_claude_process(
        [
            sys.executable,
            "-c",
            "import time; print('private output', flush=True); time.sleep(30)",
        ],
        cwd=tmp_path,
        env={},
        timeout_seconds=0.1,
    )

    assert outcome.timed_out is True
    assert outcome.returncode != 0
    assert not hasattr(outcome, "stdout")
    assert not hasattr(outcome, "stderr")


def test_ephemeral_receiver_binds_loopback_and_reports_health(temp_data_dir):
    config = __import__("maida.config", fromlist=["load_config"]).load_config()

    with _claude_receiver(config) as endpoint:
        assert endpoint.startswith("http://127.0.0.1:")
        with urlopen(f"{endpoint}/healthz", timeout=2) as response:  # noqa: S310
            assert response.status == 200
            assert json.load(response) == {"status": "ok"}


def test_report_rejects_unknown_format():
    with pytest.raises(ValueError, match="text, json, or markdown"):
        ScenarioRunReport(results=[]).render("xml")
