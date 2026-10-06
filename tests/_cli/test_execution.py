"""CLI execution behavior."""

import json
import subprocess
import pytest
from typer.testing import CliRunner
from maida.cli import app
from maida.schema_versions import REPORT_SCHEMA_VERSION


runner = CliRunner()


def test_run_command_executes_requested_trials(empty_data_dir, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    script = project / "agent.py"
    script.write_text(
        "from maida import traced_run\nwith traced_run(name='cli-agent'):\n    pass\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "agent.py"], cwd=project, check=True)
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        ["run", "agent.py", "--trials", "2", "--max-steps", "10", "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["trials_requested"] == 2
    assert len(payload["trials"]) == 2


def test_run_command_missing_script_exits_two(empty_data_dir, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "missing.py"])

    assert result.exit_code == 2
    assert "Agent script not found" in result.stderr


def test_run_inconclusive_is_neutral_and_writes_json_sidecar(empty_data_dir, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    script = project / "agent.py"
    script.write_text(
        """
import os
from maida import traced_run

with traced_run(name="mixed-agent"):
    pass
if os.environ["MAIDA_TRIAL_INDEX"] == "3":
    raise SystemExit(1)
""".lstrip(),
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "agent.py"], cwd=project, check=True)
    monkeypatch.chdir(project)
    sidecar = project / "gate.json"

    result = runner.invoke(
        app,
        [
            "run",
            "agent.py",
            "--trials",
            "3",
            "--format",
            "markdown",
            "--json-out",
            str(sidecar),
        ],
    )

    assert result.exit_code == 1, result.output
    assert result.stdout.startswith("## ❌ Maida verdict: fail")
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["report_version"] == REPORT_SCHEMA_VERSION
    assert payload["verdict"] == "fail"
    assert payload["passed"] is False
    assert payload["aggregate_results"][0]["decision_rule"] == "invariant"


def test_run_statistical_cli_overrides_policy(empty_data_dir, tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=project, check=True)
    (project / "agent.py").write_text(
        "from maida import traced_run\nwith traced_run(name='configured'):\n    pass\n",
        encoding="utf-8",
    )
    (project / "policy.yaml").write_text(
        "version: 2\ntrials: 25\nmetrics:\n"
        "  task_pass_rate: {kind: statistical, direction: lower, confidence: 0.95, threshold: 0.9}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "agent.py", "policy.yaml"], cwd=project, check=True)
    monkeypatch.chdir(project)

    result = runner.invoke(
        app,
        [
            "run",
            "agent.py",
            "--policy",
            "policy.yaml",
            "--trials",
            "5",
            "--confidence-level",
            "0.9",
            "--pass-rate-threshold",
            "0.7",
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["metadata"]["trials_used"] == 5
    assert payload["metadata"]["trials_budgeted"] == 5
    task = next(item for item in payload["aggregate_results"] if item["check_name"] == "task_pass_rate")
    assert task["evidence"]["confidence"] == 0.9
    assert task["evidence"]["threshold"] == 0.7
    assert task["decision_rule"] == "wilson_one_sided"


def test_run_invalid_statistical_policy_exits_two(empty_data_dir, tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet"], cwd=tmp_path, check=True)
    (tmp_path / "agent.py").write_text("print('unused')\n", encoding="utf-8")
    (tmp_path / "policy.yaml").write_text(
        "version: 2\nmetrics:\n  task_pass_rate: {kind: statistical, direction: lower, confidence: 1.5, threshold: 0.9, mode: report_only}\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "agent.py", "policy.yaml"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["run", "agent.py", "--policy", "policy.yaml"])

    assert result.exit_code == 2
    assert "confidence" in result.stderr


@pytest.mark.parametrize("policy_text", ["assert: {}\n", "version: 1\nassert: {}\n"])
def test_run_rejects_unsupported_policy_before_executing_agent(tmp_path, monkeypatch, policy_text):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "agent.py").write_text("raise RuntimeError('agent must not execute')\n")
    (tmp_path / "policy.yaml").write_text(policy_text)
    result = runner.invoke(app, ["run", "agent.py", "--policy", "policy.yaml"])
    assert result.exit_code == 2
    assert "version: 2" in result.stderr
    assert "deprecated" not in result.stderr.lower()
    assert "agent must not execute" not in result.output
