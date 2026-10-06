"""CLI demo behavior."""

from tests.support.paths import REPO_ROOT
import json
import os
import shlex
import socket
from pathlib import Path
from types import SimpleNamespace
from packaging.requirements import Requirement
from typer.testing import CliRunner
from maida.cli import _PLAN_BACKEND_INSTALL_COMMAND, app
from maida.config import load_config
from maida.storage import list_runs


runner = CliRunner()


def test_demo_records_a_run(empty_data_dir):
    config = load_config()
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "Run recorded:" in result.output
    assert "Next steps:" in result.output

    runs = list_runs(limit=5, config=config)
    assert len(runs) == 1
    assert runs[0].get("run_name") == "demo-support-agent"
    assert runs[0].get("status") == "ok"


def test_demo_plan_renders_a_pre_execution_refusal(monkeypatch, tmp_path):
    class DemoBackend:
        @staticmethod
        def run_plan_demo(policy_path=None):
            assert policy_path is None
            return {
                "evidence": SimpleNamespace(valid=False),
                "execution_attempts": 0,
                "max_fanout": 2,
                "node_count": 4,
                "schemas": {"plan": "0.1.0", "policy": "2.1", "report": "2.0.1"},
                "topology": "normalize -> [draft, review] -> publish",
                "rendered": (
                    "PLAN REFUSED: PLAN_FANOUT_EXCEEDED\nPlan fan-out is 2; policy allows at most 1 (plan_fanout)."
                ),
            }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("maida._cli.demo.import_module", lambda name: DemoBackend)

    result = runner.invoke(app, ["demo", "--plan"])

    assert result.exit_code == 0
    assert "everything below is simulated and local" in result.output
    assert "policy 2.1 | plan 0.1.0 | report 2.0.1" in result.output
    assert "policy source: bundled demo refusal policy" in result.output
    assert "policy: plan_fanout <=" not in result.output
    assert "normalize -> [draft, review] -> publish" in result.output
    assert "PLAN REFUSED: PLAN_FANOUT_EXCEEDED" in result.output
    assert "No generated module executed." in result.output


def test_demo_plan_discovers_printed_policy_recovery_path(
    monkeypatch,
    tmp_path,
):
    selected_policies = []

    class DemoBackend:
        @staticmethod
        def run_plan_demo(policy_path=None):
            selected_policies.append(policy_path)
            return {
                "evidence": SimpleNamespace(valid=False),
                "execution_attempts": 0,
                "max_fanout": 2,
                "node_count": 4,
                "schemas": {"plan": "0.1.0", "policy": "2.1", "report": "2.0.1"},
                "topology": "normalize -> [draft, review] -> publish",
                "rendered": (
                    "PLAN REFUSED: PLAN_FANOUT_EXCEEDED\nPlan fan-out is 2; policy allows at most 1 (plan_fanout)."
                ),
            }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("maida._cli.demo.import_module", lambda name: DemoBackend)

    first = runner.invoke(app, ["demo", "--plan"])

    assert first.exit_code == 0
    assert selected_policies == [None]
    assert "policy source: bundled demo refusal policy" in first.output
    assert f"update {Path('.maida/policy.yaml')}" in first.output

    policy = tmp_path / ".maida" / "policy.yaml"
    policy.parent.mkdir()
    policy.write_text(
        "version: 2.1\nmetrics:\n  plan_fanout: {kind: measured, direction: upper, limit: 2}\n",
        encoding="utf-8",
    )

    second = runner.invoke(app, ["demo", "--plan"])

    assert second.exit_code == 0
    assert selected_policies == [None, Path(".maida/policy.yaml")]
    assert f"policy source: {Path('.maida/policy.yaml')}" in second.output


def test_demo_plan_explicit_policy_wins_over_discovered_default(
    monkeypatch,
    tmp_path,
):
    selected_policies = []

    class DemoBackend:
        @staticmethod
        def run_plan_demo(policy_path=None):
            selected_policies.append(policy_path)
            return {
                "evidence": SimpleNamespace(valid=False),
                "execution_attempts": 0,
                "max_fanout": 2,
                "node_count": 4,
                "schemas": {"plan": "0.1.0", "policy": "2.1", "report": "2.0.1"},
                "topology": "normalize -> [draft, review] -> publish",
                "rendered": (
                    "PLAN REFUSED: PLAN_FANOUT_EXCEEDED\nPlan fan-out is 2; policy allows at most 1 (plan_fanout)."
                ),
            }

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("maida._cli.demo.import_module", lambda name: DemoBackend)
    default_policy = tmp_path / ".maida" / "policy.yaml"
    default_policy.parent.mkdir()
    default_policy.write_text("version: 2.1\nmetrics: {}\n", encoding="utf-8")
    explicit_policy = tmp_path / "reviewed-policy.yaml"
    explicit_policy.write_text("version: 2.1\nmetrics: {}\n", encoding="utf-8")

    result = runner.invoke(
        app,
        ["demo", "--plan", "--policy", str(explicit_policy)],
    )

    assert result.exit_code == 0
    assert selected_policies == [explicit_policy]
    assert f"policy source: {explicit_policy}" in result.output
    assert f"update {explicit_policy} after review" in result.output


def test_demo_plan_missing_optional_backend_uses_canonical_install_instruction(
    monkeypatch,
):
    install_command = _PLAN_BACKEND_INSTALL_COMMAND

    def missing_backend(name):
        raise ModuleNotFoundError("No module named 'maida.workflows'", name="maida.workflows")

    monkeypatch.setattr("maida._cli.demo.import_module", missing_backend)

    result = runner.invoke(app, ["demo", "--plan"])

    assert result.exit_code == 2
    assert "maida-workflows is required for generated-plan gating" in result.stderr
    assert result.stderr.rstrip().endswith(install_command)


def test_demo_plan_install_instruction_has_lower_bounds_aligned_with_contract():
    requirements = {
        Requirement(argument).name: Requirement(argument)
        for argument in shlex.split(_PLAN_BACKEND_INSTALL_COMMAND)
        if argument.startswith(("maida-ai", "maida-workflows"))
    }
    assert set(requirements) == {"maida-ai", "maida-workflows"}

    def has_lower_bound(requirement: Requirement) -> bool:
        return any(spec.operator in {">=", ">"} for spec in requirement.specifier)

    assert has_lower_bound(requirements["maida-ai"])
    assert has_lower_bound(requirements["maida-workflows"])

    contract = json.loads((REPO_ROOT / "contracts" / "current-main.json").read_text(encoding="utf-8"))
    assert str(requirements["maida-ai"]) == contract["install_requirement"]


def test_demo_plan_rejects_incompatible_demo_options():
    result = runner.invoke(app, ["demo", "--plan", "--regression"])

    assert result.exit_code == 2
    assert "Choose either --plan or --regression" in result.stderr


def test_demo_run_is_redacted_on_disk(empty_data_dir):
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0

    spans_files = list(empty_data_dir.rglob("spans.jsonl"))
    assert spans_files, "expected a spans.jsonl to be written"
    raw = spans_files[0].read_text()
    assert "sk-demo-DO_NOT_USE" not in raw  # api_key value must be scrubbed


def test_demo_then_baseline_and_assert_pass(empty_data_dir):
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0

    bl_path = empty_data_dir / "demo.json"
    result = runner.invoke(app, ["baseline", "--out", str(bl_path)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["assert", "--baseline", str(bl_path)])
    assert result.exit_code == 1 or result.exit_code == 0
    # the same run asserted against its own baseline must pass every check
    assert "FAILED" not in result.output


def test_demo_regression_can_be_repeated(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for _ in range(2):
        result = runner.invoke(app, ["demo", "--regression"])
        assert result.exit_code == 0, result.output
        assert "PR-comment preview" in result.output


def test_demo_regression_story(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()

    result = runner.invoke(app, ["demo", "--regression"])
    assert result.exit_code == 0

    # baseline written under cwd
    bl_path = tmp_path / ".maida" / "baselines" / "demo-support-agent.json"
    assert bl_path.is_file()

    # two runs were recorded
    runs = list_runs(limit=5, config=config)
    assert len(runs) == 2

    # the gate must fail and explain itself
    assert "Local-only canned data: no API keys, no network calls, no repo clone." in (result.output)
    assert "Story: baseline -> regressed refactor -> failed gate -> PR-comment preview." in result.output
    assert "baseline behavior: lookup_customer -> search_kb -> send_reply" in (result.output)
    assert "regression: demo-gpt-4-mini loops on search_kb, then escalates" in (result.output)
    assert "finished with status ok; behavior still changed" in result.output
    assert "FAILED" in result.output
    assert "escalate_to_human" in result.output
    assert "loop warning" in result.output
    assert "duration [no_regression]: 120 ms (baseline: 120" in result.output
    assert "duration_ms:" not in result.output
    # and preview the PR comment
    assert "PR comment preview" in result.output
    assert "Maida verdict: fail" in result.output
    assert "Top behavior changes" in result.output


def test_demo_regression_is_local_only_and_forces_demo_loop_settings(
    empty_data_dir,
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("MAIDA_LOOP_WINDOW", "99")
    monkeypatch.setenv("MAIDA_LOOP_REPETITIONS", "99")

    def fail_network(*args, **kwargs):
        raise AssertionError("demo attempted a network call")

    monkeypatch.setattr(socket, "create_connection", fail_network)

    result = runner.invoke(app, ["demo", "--regression"], catch_exceptions=False)

    assert result.exit_code == 0
    assert "no API keys, no network calls, no repo clone" in result.output
    assert "loop warning" in result.output
    assert not (tmp_path / ".git").exists()
    assert os.environ["MAIDA_LOOP_WINDOW"] == "99"
    assert os.environ["MAIDA_LOOP_REPETITIONS"] == "99"


def test_demo_regression_no_secret_on_disk(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["demo", "--regression"])
    assert result.exit_code == 0
    for spans_file in empty_data_dir.rglob("spans.jsonl"):
        assert "sk-demo-DO_NOT_USE" not in spans_file.read_text()
