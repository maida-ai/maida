"""Exercise onboarding as a user: observe, review, activate, then catch a change."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from maida import record_tool_call, traced_run
from maida.cli import app
from maida.config import load_config
from maida.policy import load_policy
from tests.conftest import get_latest_run_id


runner = CliRunner()


def observe(*, name="repo-agent", error=False, tools=("read_file",)):
    try:
        with traced_run(name=name):
            for tool in tools:
                record_tool_call(tool, args={"secret": "never-copy-this"}, result="private result")
            if error:
                raise RuntimeError("fixture failure")
    except RuntimeError:
        pass
    return get_latest_run_id(load_config())


def test_starter_workflow_requires_review_and_catches_regression(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first = observe()
    result = runner.invoke(app, ["init", "--from-run", first])
    assert result.exit_code == 0, result.output
    assert not Path(".maida/policy.yaml").exists()
    policy = load_policy(Path(".maida/starter/policy.yaml"))
    assert set(policy.metrics) == {"stop_condition_reached", "no_loops", "no_guardrails"}
    assert all(item.kind.value == "invariant" for item in policy.metrics.values())
    assert "review" in result.output.lower()
    assert "never-copy-this" not in "".join(p.read_text() for p in Path(".maida/starter").iterdir())
    assert "private result" not in Path(".maida/starter/baseline.json").read_text()

    result = runner.invoke(app, ["init", "--reviewed"])
    assert result.exit_code == 2
    assert "--reason" in result.output
    assert not Path(".maida/policy.yaml").exists()

    result = runner.invoke(app, ["init", "--reviewed", "--reason", "Task must finish without loops"])
    assert result.exit_code == 0, result.output
    assert "maida assert" in result.output
    review = json.loads(Path(".maida/starter/review.json").read_text())
    assert review["review_required"] is False
    assert review["source_trace_ids"] == [first]
    assert review["accepted_policy_sha256"]
    baseline = Path(".maida/baselines/agent.json")

    # A different tool/path is harmless unless the owner chooses a path contract.
    second = observe(tools=("read_file", "run_tests"))
    result = runner.invoke(app, ["assert", second, "--baseline", str(baseline)])
    assert result.exit_code == 0, result.output
    bad = observe(error=True)
    result = runner.invoke(app, ["assert", bad, "--baseline", str(baseline)])
    assert result.exit_code == 1, result.output


def test_starter_requires_explicit_observation_and_rejects_mixed_workflows(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 2
    assert "--from-run" in result.output
    assert not Path(".maida").exists()
    result = runner.invoke(app, ["init", "--from-run", "latest"])
    assert result.exit_code == 2
    assert "maida demo" in result.output
    one = observe(name="one")
    two = observe(name="two")
    result = runner.invoke(app, ["init", "--from-run", one, "--from-run", two])
    assert result.exit_code == 2
    assert "same workflow" in result.output
    assert not Path(".maida/starter").exists()


def test_starter_failed_observation_is_not_a_known_good_baseline(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = observe(error=True)
    result = runner.invoke(app, ["init", "--from-run", run])
    assert result.exit_code == 2
    assert "successful" in result.output
    assert not Path(".maida/starter").exists()


def test_starter_samples_same_workflow_and_preserves_existing_files(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    first, second = observe(), observe(tools=("different_tool",))
    args = ["init", "--from-run", first, "--from-run", second]
    assert runner.invoke(app, args).exit_code == 0
    baseline = json.loads(Path(".maida/starter/baseline.json").read_text())
    assert baseline["trial_sample"]["trials"] == 2
    before = Path(".maida/starter/baseline.json").read_bytes()
    assert runner.invoke(app, args).exit_code == 2
    assert Path(".maida/starter/baseline.json").read_bytes() == before
    Path(".maida/policy.yaml").write_text("owned by user\n")
    result = runner.invoke(app, ["init", "--reviewed", "--reason", "reviewed"])
    assert result.exit_code == 2
    assert Path(".maida/policy.yaml").read_text() == "owned by user\n"
    assert not Path(".maida/baselines/agent.json").exists()


def test_starter_review_revalidates_edited_policy_and_baseline(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--from-run", observe()]).exit_code == 0
    policy = Path(".maida/starter/policy.yaml")
    policy.write_text("version: 2\nmetrics: {}\n")
    result = runner.invoke(app, ["init", "--reviewed", "--reason", "reviewed"])
    assert result.exit_code == 2
    assert not Path(".maida/policy.yaml").exists()


def test_starter_does_not_propose_no_loops_when_observed_runs_loop(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    trace_id = observe(tools=("read_file",) * 5)
    assert runner.invoke(app, ["init", "--from-run", trace_id]).exit_code == 0
    policy = load_policy(Path(".maida/starter/policy.yaml"))
    assert "no_loops" not in policy.metrics
    assert "stop_condition_reached" in policy.metrics


def test_starter_refuses_symlinked_output(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    outside = tmp_path / "other"
    outside.mkdir()
    Path(".maida").symlink_to(outside, target_is_directory=True)
    result = runner.invoke(app, ["init", "--from-run", observe()])
    assert result.exit_code == 2
    assert "symlink" in result.output
    assert list(outside.iterdir()) == []


def test_starter_activation_rolls_back_on_persistence_error(temp_data_dir, tmp_path, monkeypatch):
    import maida.starter as starter

    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--from-run", observe()]).exit_code == 0
    review_before = Path(".maida/starter/review.json").read_bytes()
    replace = starter.os.replace

    def fail_baseline(source, destination):
        if destination == starter.ACTIVE_BASELINE:
            raise OSError("simulated disk failure")
        return replace(source, destination)

    monkeypatch.setattr(starter.os, "replace", fail_baseline)
    result = runner.invoke(app, ["init", "--reviewed", "--reason", "reviewed"])
    assert result.exit_code == 10
    assert not Path(".maida/policy.yaml").exists()
    assert not Path(".maida/baselines/agent.json").exists()
    assert Path(".maida/starter/review.json").read_bytes() == review_before


def test_github_scaffold_uses_real_entrypoint_and_reviewed_baseline(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], check=True)
    Path("agent.py").write_text("from maida import traced_run\nwith traced_run(name='repo-agent'): pass\n")
    assert runner.invoke(app, ["init", "--from-run", observe()]).exit_code == 0
    result = runner.invoke(
        app, ["init", "--reviewed", "--reason", "reviewed", "--github", "--agent-script", "agent.py"]
    )
    assert result.exit_code == 0, result.output
    workflow = yaml.safe_load(Path(".github/workflows/maida.yml").read_text())
    assert workflow["env"]["MAIDA_AGENT_SCRIPT"] == "agent.py"
    assert workflow["env"]["MAIDA_BASELINE"] == ".maida/baselines/agent.json"
    assert "my_agent.py" not in Path(".github/workflows/maida.yml").read_text()
    assert "maida run agent.py" in result.output
    result = runner.invoke(app, ["run", "agent.py", "--baseline", ".maida/baselines/agent.json"])
    assert result.exit_code == 0, result.output


def test_github_installs_declared_dependencies_in_both_execution_jobs(tmp_path, monkeypatch):
    from maida.scaffold import render_workflow

    monkeypatch.chdir(tmp_path)
    Path("pyproject.toml").write_text('[project]\nname="example"\nversion="0.0.1"\ndependencies=[]\n')
    Path("uv.lock").write_text("version = 1\n")
    workflow = yaml.safe_load(render_workflow("agent.py", ".maida/baselines/agent.json"))
    for job in ("agent-check", "capture"):
        steps = workflow["jobs"][job]["steps"]
        install = next(step for step in steps if step.get("name") == "Install project dependencies")
        assert "uv export --locked --no-dev" in install["run"]
    assert not any("run" in step for step in workflow["jobs"]["write"]["steps"])


@pytest.mark.parametrize("script", [None, "missing.py", "../elsewhere.py", "${{ secrets.TOKEN }}.py"])
def test_github_preflight_does_not_activate_when_script_invalid(temp_data_dir, tmp_path, monkeypatch, script):
    monkeypatch.chdir(tmp_path)
    assert runner.invoke(app, ["init", "--from-run", observe()]).exit_code == 0
    args = ["init", "--reviewed", "--reason", "reviewed", "--github"]
    if script is not None:
        args += ["--agent-script", script]
    result = runner.invoke(app, args)
    assert result.exit_code == 2
    assert not Path(".maida/policy.yaml").exists()
    assert not Path(".github/workflows/maida.yml").exists()


def test_captured_task_first_check_then_review_and_fresh_session(temp_data_dir, tmp_path, monkeypatch):
    """Run the guide across real hook import and distinct captured session IDs."""
    monkeypatch.chdir(tmp_path)

    def capture(session, tool):
        common = {"session_id": session, "cwd": str(tmp_path)}
        for event in (
            {"hook_event_name": "SessionStart", "source": "startup"},
            {
                "hook_event_name": "PreToolUse",
                "tool_use_id": "one",
                "tool_name": tool,
                "tool_input": {"file_path": "pyproject.toml"},
            },
            {
                "hook_event_name": "PostToolUse",
                "tool_use_id": "one",
                "tool_name": tool,
                "tool_input": {"file_path": "pyproject.toml"},
                "tool_response": {"content": "pytest"},
            },
            {"hook_event_name": "SessionEnd", "reason": "other"},
        ):
            result = runner.invoke(app, ["capture", "claude-hook"], input=json.dumps({**common, **event}))
            assert result.exit_code == 0, result.output
        return get_latest_run_id(load_config())

    first = capture("known-task-session", "Read")
    result = runner.invoke(app, ["assert", "--expect-status", "ok", "--no-loops", "--no-guardrails"])
    assert result.exit_code == 0, result.output
    assert not Path(".maida/policy.yaml").exists()
    result = runner.invoke(app, ["init", "--from-run", "latest"])
    assert result.exit_code == 0, result.output
    assert first in result.output
    policy = Path(".maida/starter/policy.yaml")
    candidate = yaml.safe_load(policy.read_text())
    # The fixture owner also adopts a concrete requirement observed in this task.
    candidate["metrics"]["required_tools"] = {"kind": "invariant", "all_of": ["Read"]}
    policy.write_text(yaml.safe_dump(candidate))
    result = runner.invoke(app, ["init", "--reviewed", "--reason", "Lookup must read test configuration"])
    assert result.exit_code == 0, result.output
    baseline = Path(".maida/baselines/agent.json")
    original = baseline.read_bytes()
    for session, tool, expected in (("candidate", "Read", 0), ("regression", "Write", 1), ("repair", "Read", 0)):
        selected = capture(session, tool)
        assert selected != first
        result = runner.invoke(app, ["assert", "--baseline", str(baseline), "--policy", ".maida/policy.yaml"])
        assert result.exit_code == expected, result.output
        assert baseline.read_bytes() == original
