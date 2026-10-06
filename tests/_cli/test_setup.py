"""CLI setup behavior."""

import yaml
from typer.testing import CliRunner
from maida.cli import app
from maida.config import load_config
from maida.policy import load_policy
from maida.scaffold import (
    CHECKOUT_ACTION_REF,
    MAIDA_ACCEPT_ACTION_REF,
    MAIDA_ASSERT_ACTION_REF,
    WORKFLOW_TEMPLATE,
)
from tests.support.cli import _make_run


runner = CliRunner()


def test_scaffold_grants_checks_write_permission():
    assert "checks: write" in WORKFLOW_TEMPLATE


def test_init_writes_valid_policy(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _make_run(load_config())
    result = runner.invoke(app, ["init", "--from-run", run_id])
    assert result.exit_code == 0
    policy_path = tmp_path / ".maida" / "starter" / "policy.yaml"
    assert policy_path.is_file()
    assert "candidate invariants" in result.output
    assert "Next:" in result.output

    # generated policy must load through the real policy loader
    policy_text = policy_path.read_text(encoding="utf-8")
    policy = load_policy(policy_path)
    assert policy.source_format == "v2"
    assert policy.policy_version == (2, 0)
    assert policy.trials == 1
    assert policy.fail_fast is False
    assert policy.metrics["stop_condition_reached"].kind.value == "invariant"
    assert set(policy.metrics) == {"stop_condition_reached", "no_loops", "no_guardrails"}
    assert "version: 2" in policy_text
    assert "trials: 1" in policy_text


def test_init_github_writes_valid_workflow(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _make_run(load_config())
    assert runner.invoke(app, ["init", "--from-run", run_id]).exit_code == 0
    (tmp_path / "agent.py").write_text("# Existing traced entrypoint\n")
    result = runner.invoke(
        app, ["init", "--reviewed", "--reason", "reviewed", "--github", "--agent-script", "agent.py"]
    )
    assert result.exit_code == 0
    policy_path = tmp_path / ".maida" / "policy.yaml"
    wf_path = tmp_path / ".github" / "workflows" / "maida.yml"
    assert policy_path.is_file()
    assert wf_path.is_file()

    workflow_text = wf_path.read_text(encoding="utf-8")
    policy = load_policy(policy_path)
    assert policy.policy_version == (2, 0)
    assert policy.metrics["stop_condition_reached"].kind.value == "invariant"

    wf = yaml.safe_load(workflow_text)
    triggers = wf.get("on", wf.get(True))
    assert set(triggers) == {"pull_request", "issue_comment", "repository_dispatch"}
    assert triggers["issue_comment"]["types"] == ["created"]
    assert triggers["repository_dispatch"]["types"] == ["maida_baseline_updated"]
    assert wf["permissions"] == {}
    assert wf["env"] == {
        "MAIDA_AGENT_SCRIPT": "agent.py",
        "MAIDA_POLICY": ".maida/policy.yaml",
        "MAIDA_BASELINE": ".maida/baselines/agent.json",
    }

    job = wf["jobs"]["agent-check"]
    assert set(wf["jobs"]) == {"agent-check", "authorize", "capture", "write"}
    assert job["permissions"] == {
        "contents": "read",
        "pull-requests": "write",
        "checks": "write",
        "statuses": "write",
    }
    assert "repository_dispatch" in job["if"]
    context, checkout, gate, status = job["steps"]
    assert context["uses"] == "maida-ai/maida-assert/pr-context@" + MAIDA_ASSERT_ACTION_REF.split("@")[1]
    assert checkout["uses"] == CHECKOUT_ACTION_REF
    assert checkout["with"] == {
        "ref": "${{ steps.pr.outputs.head-sha }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }
    assert gate["uses"] == MAIDA_ASSERT_ACTION_REF
    assert gate["with"]["configuration-acceptance"] == "${{ vars.MAIDA_CONFIGURATION_ACCEPTANCE }}"
    assert status["if"] == "always() && steps.pr.outcome == 'success'"
    assert status["uses"] == "maida-ai/maida-assert/publish-status@" + MAIDA_ASSERT_ACTION_REF.split("@")[1]
    assert status["with"] == {
        "head-sha": "${{ steps.pr.outputs.head-sha }}",
        "base-sha": "${{ steps.pr.outputs.base-sha }}",
        "verdict": "${{ steps.gate.outputs.verdict }}",
        "conclusion": "${{ steps.gate.outputs.conclusion }}",
        "publication": "${{ steps.gate.outputs.publication }}",
    }
    assert "steps.gate.outcome" not in workflow_text
    assert "github.sha" not in workflow_text
    authorize, capture, write = (wf["jobs"][key] for key in ("authorize", "capture", "write"))
    assert "issue_comment" in authorize["if"]
    assert authorize["steps"][0]["uses"] == MAIDA_ACCEPT_ACTION_REF
    assert authorize["steps"][0]["with"]["stage"] == "authorize"
    assert capture["permissions"] == {"contents": "read"}
    assert capture["steps"][0]["with"]["persist-credentials"] is False
    assert capture["steps"][0]["with"]["ref"] == "${{ needs.authorize.outputs.head-sha }}"
    assert write["needs"] == ["authorize", "capture"]
    assert write["steps"][-1]["with"]["context"] == "${{ needs.authorize.outputs.context }}"
    assert not any("checkout" in step.get("uses", "") or "run" in step for step in write["steps"])
    assert "existing traced entrypoint" in workflow_text
    assert "Reviewed baseline" in workflow_text
    assert "secrets." not in workflow_text.lower()


def test_init_skips_existing_without_force(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run_id = _make_run(load_config())
    runner.invoke(app, ["init", "--from-run", run_id])
    policy_path = tmp_path / ".maida" / "starter" / "policy.yaml"
    policy_path.write_text("assert: {}\n", encoding="utf-8")

    result = runner.invoke(app, ["init", "--from-run", run_id])
    assert result.exit_code == 2
    assert "already exists" in result.output
    assert policy_path.read_text() == "assert: {}\n"  # untouched

    result = runner.invoke(app, ["init", "--from-run", run_id, "--force"])
    assert result.exit_code == 0
    assert "Drafted" in result.output
    assert "version: 2" in policy_path.read_text()  # overwritten
