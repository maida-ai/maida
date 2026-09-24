"""Behavioral acceptance cases, independent of parser and evaluator helpers.

Every case goes through YAML and the public CLI with valid persisted traces.
Expected checks and outcomes are stated here, not inferred from loaded policy.
A missing check is a failure even if some other check still blocks the run.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.cli import app


FIXTURES = Path(__file__).parent / "fixtures/traces/current"
RUN_NAME = "policy-contract-agent"
# Each row names a contract, the policy spelling, and a violating observation.
INVARIANT_RULES = [
    ("no_new_tools", "{kind: invariant, require: true}", "new-tool"),
    ("forbidden_tools", "{kind: invariant, none_of: [unlisted_tool]}", "new-tool"),
    ("required_tools", "{kind: invariant, all_of: [search]}", "new-tool"),
    ("no_loops", "{kind: invariant, require: true}", "loop"),
    ("no_guardrails", "{kind: invariant, require: true}", "guardrail"),
    ("stop_condition_reached", "{kind: invariant, require: true}", "guardrail"),
]
MEASURED_RULES = [
    ("step_count", "{kind: measured, direction: upper, limit: 2}", "tool-call-spike"),
    (
        "tool_call_count",
        "{kind: measured, direction: upper, limit: 1}",
        "tool-call-spike",
    ),
    (
        "cost_tokens",
        "{kind: measured, direction: upper, limit: 25}",
        "latency-cost-envelope",
    ),
    (
        "latency_ms",
        "{kind: measured, direction: upper, limit: 150}",
        "latency-cost-envelope",
    ),
]

RULES = INVARIANT_RULES + MEASURED_RULES


def _trace(destination: Path, observation: str, trace_id=None) -> str:
    fixture = "normal" if observation.startswith(("new-tool", "known-tool", "tokens-", "duration-")) else observation
    shutil.copytree(FIXTURES / fixture, destination)
    meta_path = destination / "meta.json"
    meta = json.loads(meta_path.read_text())
    meta["run_name"] = RUN_NAME
    if trace_id is not None:
        meta["trace_id"] = trace_id
    spans_path = destination / "spans.jsonl"
    spans = [json.loads(line) for line in spans_path.read_text().splitlines()]
    if observation == "new-tool":
        # Same count, timing, and usage; only tool identity changes.
        tool = next(span for span in spans if span["name"] == "search")
        tool["name"] = "unlisted_tool"
        tool["attributes"]["tool.name"] = "unlisted_tool"
    elif observation in {"known-tool-repeat", "known-tool-triple"}:
        tool = next(span for span in spans if span["name"] == "search")
        for index in range(1 if observation == "known-tool-repeat" else 2):
            spans.append({**tool, "span_id": f"{index + 100:016x}"})
            meta["counts"]["tool_calls"] += 1
    elif observation.startswith("tokens-"):
        total = int(observation.split("-")[1])
        usage = next(span["attributes"] for span in spans if "gen_ai.usage.total_tokens" in span["attributes"])
        usage["gen_ai.usage.total_tokens"] = total
        usage["gen_ai.usage.output_tokens"] = total - 10
    elif observation.startswith("duration-"):
        duration = int(observation.split("-")[1])
        meta["duration_ms"] = duration
        # Fixture starts at exactly midnight; all cases stay within one second.
        meta["ended_at"] = f"2026-01-01T00:00:00.{duration * 1000:06d}Z"
        spans[0]["duration_ms"] = duration
        spans[0]["end_time"] = meta["ended_at"]
    for span in spans:
        span["trace_id"] = meta["trace_id"]
    meta_path.write_text(json.dumps(meta))
    spans_path.write_text("".join(json.dumps(span) + "\n" for span in spans))
    return meta["trace_id"]


def _invoke(
    surface,
    root,
    monkeypatch,
    rules,
    observation,
    *,
    baseline=True,
    trials=1,
    fail_process=False,
    violation=None,
    version=2,
):
    """Use the same serialized policy and trace through each public surface."""
    runner = CliRunner()
    monkeypatch.chdir(root)
    monkeypatch.setenv("HOME", str(root / "home"))
    monkeypatch.setenv("MAIDA_DATA_DIR", str(root / "reference"))
    reference = root / "reference/runs/10000000000000000000000000000001"
    _trace(reference, "normal")
    baseline_path = root / "baseline.json"
    captured = runner.invoke(app, ["baseline", "--out", str(baseline_path)])
    assert captured.exit_code == 0, captured.output
    policy = root / "policy.yaml"
    policy.write_text(f"version: {version}\ntrials: {trials}\nfail_fast: false\nmetrics:\n" + rules)
    candidate = root / "candidate"
    _trace(candidate, observation)
    if violation is not None:
        _trace(root / "violation", violation)
    runs = root / "window/runs"
    runs.mkdir(parents=True)
    for index in range(trials):
        current = violation if violation is not None and index == 1 else observation
        _trace(runs / f"{index + 1000:032x}", current, f"{index + 1000:032x}")
    monkeypatch.setenv("MAIDA_DATA_DIR", str(runs.parent))
    if surface == "run":
        monkeypatch.setenv("MAIDA_DATA_DIR", str(root / "output"))
        # Simulate a native external emitter. The gate still validates, extracts,
        # and evaluates the trace from its real isolated subprocess workspace.
        (root / "agent.py").write_text(
            "import json, os, shutil, uuid\nfrom pathlib import Path\n"
            "source = Path('candidate')\n"
            "if Path('violation').exists() and os.environ['MAIDA_TRIAL_INDEX'] == '2': source = Path('violation')\n"
            "trace_id = uuid.uuid4().hex\n"
            "target = Path(os.environ['MAIDA_DATA_DIR']) / 'runs' / trace_id\n"
            "shutil.copytree(source, target)\n"
            "meta = json.loads((target / 'meta.json').read_text())\n"
            "meta['trace_id'] = trace_id\n"
            "(target / 'meta.json').write_text(json.dumps(meta))\n"
            "spans = [json.loads(line) for line in (target / 'spans.jsonl').read_text().splitlines()]\n"
            "for span in spans: span['trace_id'] = trace_id\n"
            "(target / 'spans.jsonl').write_text(''.join(json.dumps(span) + '\\n' for span in spans))\n"
            f"raise SystemExit({1 if fail_process else 0})\n"
        )
        for command in (
            ["git", "init", "--quiet"],
            ["git", "add", "candidate", "agent.py"],
        ):
            subprocess.run(command, cwd=root, check=True, capture_output=True)
        arguments = ["run", "agent.py"]
    elif surface == "drift":
        arguments = ["drift", "--window", str(runs)]
    else:
        arguments = ["assert", f"{1000:032x}"]
    arguments += ["--policy", str(policy), "--format", "json"]
    if baseline:
        arguments += ["--baseline", str(baseline_path)]
    return runner.invoke(app, arguments)


def _assert_contract(result, surface, expected_checks, expected_failed):
    assert result.exit_code == (1 if expected_failed else 0), result.output
    report = json.loads(result.stdout)
    assert report["passed"] is (not expected_failed)
    if surface != "assert":
        assert report["verdict"] == ("fail" if expected_failed else "pass")
    records = report["results"] if surface == "assert" else report["aggregate_results"]
    checks = {item["check_name"]: item for item in records}
    # Process status is separately observed by run/drift, not a policy metric.
    if surface == "assert":
        assert "agent_process" not in checks
    else:
        process = checks.pop("agent_process")
        assert process["mode"] == "gating"
        assert process["verdict"] in {"pass", "fail"}
    assert set(checks) == set(expected_checks), "a declared check disappeared or was substituted"
    failed = set()
    for name, item in checks.items():
        assert not item.get("ignored", False), f"{name} was silently ignored"
        if surface == "assert":
            passed = item["passed"]
            assert isinstance(passed, bool)
        else:
            assert item["mode"] == "gating", f"{name} stopped gating"
            assert item["verdict"] in {"pass", "fail"}
            passed = item["verdict"] == "pass"
        if not passed:
            failed.add(name)
    assert failed == set(expected_failed)


@pytest.mark.parametrize("surface", ["assert", "run", "drift"])
@pytest.mark.parametrize("metric, rule, violation", RULES, ids=[row[0] for row in RULES])
@pytest.mark.parametrize("regression", [False, True], ids=["passes", "fails"])
def test_each_declared_rule_enforces_its_behavior(tmp_path, monkeypatch, surface, metric, rule, violation, regression):
    result = _invoke(
        surface,
        tmp_path,
        monkeypatch,
        f"  {metric}: {rule}\n",
        violation if regression else "normal",
    )
    _assert_contract(result, surface, {metric}, {metric} if regression else set())


@pytest.mark.parametrize("surface", ["assert", "run", "drift"])
def test_composed_policy_reports_all_checks_and_all_violations(tmp_path, monkeypatch, surface):
    result = _invoke(
        surface,
        tmp_path,
        monkeypatch,
        "".join(f"  {name}: {rule}\n" for name, rule, _ in RULES),
        "tool-call-spike",
    )
    _assert_contract(
        result,
        surface,
        {row[0] for row in RULES},
        {"step_count", "tool_call_count", "latency_ms", "no_new_tools"},
    )


@pytest.mark.parametrize("surface", ["assert", "run", "drift"])
def test_known_tool_repetition_preserves_identity_contract(tmp_path, monkeypatch, surface):
    result = _invoke(
        surface,
        tmp_path,
        monkeypatch,
        "  no_new_tools: {kind: invariant, require: true}\n",
        "known-tool-repeat",
    )
    _assert_contract(result, surface, {"no_new_tools"}, set())


@pytest.mark.parametrize("surface", ["assert", "run"])
@pytest.mark.parametrize(
    "metric, rule",
    [
        ("no_new_tools", "{kind: invariant, require: true}"),
        (
            "cost_tokens",
            "{kind: measured, direction: upper, tolerance: {relative: 0.5}}",
        ),
    ],
)
def test_baseline_dependent_contract_cannot_be_silently_omitted(tmp_path, monkeypatch, surface, metric, rule):
    result = _invoke(
        surface,
        tmp_path,
        monkeypatch,
        f"  {metric}: {rule}\n",
        "normal",
        baseline=False,
    )
    assert result.exit_code == 2, result.output
    assert metric in result.stderr
    assert "baseline" in result.stderr


# A tolerance must not erase an absolute cap, and a cap must not erase tolerance.
# These observations sit on, or just beyond, independently stated boundaries.
BOUNDS = [
    ("step_count", 0.5, 4, 2, "known-tool-repeat", "known-tool-triple"),
    ("tool_call_count", 1, 3, 1, "known-tool-repeat", "known-tool-triple"),
    ("cost_tokens", 0.2, 40, 29, "tokens-30", "tokens-31"),
    ("latency_ms", 0.2, 200, 179, "duration-180", "duration-181"),
]


@pytest.mark.parametrize("surface", ["assert", "run", "drift"])
@pytest.mark.parametrize(
    "metric, tolerance, loose_cap, tight_cap, boundary, outside",
    BOUNDS,
    ids=[row[0] for row in BOUNDS],
)
@pytest.mark.parametrize("scenario", ["boundary", "outside-tolerance", "over-cap"])
def test_measured_contract_preserves_both_tolerance_and_cap(
    tmp_path,
    monkeypatch,
    surface,
    metric,
    tolerance,
    loose_cap,
    tight_cap,
    boundary,
    outside,
    scenario,
):
    cap = tight_cap if scenario == "over-cap" else loose_cap
    observation = outside if scenario == "outside-tolerance" else boundary
    rules = f"  {metric}: {{kind: measured, direction: upper, limit: {cap}, tolerance: {{relative: {tolerance}}}}}\n"
    result = _invoke(surface, tmp_path, monkeypatch, rules, observation)
    _assert_contract(result, surface, {metric}, set() if scenario == "boundary" else {metric})


@pytest.mark.parametrize("surface", ["run", "drift"])
@pytest.mark.parametrize("regression", [False, True])
def test_statistical_policy_reaches_a_blocking_decision(tmp_path, monkeypatch, surface, regression):
    rules = "  task_pass_rate: {kind: statistical, direction: lower, threshold: 0.5, confidence: 0.95, mode: gating}\n"
    result = _invoke(
        surface,
        tmp_path,
        monkeypatch,
        rules,
        "guardrail" if regression else "normal",
        trials=3,
        fail_process=regression,
    )
    _assert_contract(result, surface, {"task_pass_rate"}, {"task_pass_rate"} if regression else set())
    report = json.loads(result.stdout)
    metric = next(item for item in report["aggregate_results"] if item["check_name"] == "task_pass_rate")
    assert metric["decision_rule"] == "wilson_one_sided"
    assert metric["trials_used"] == 3


@pytest.mark.parametrize(
    "rule",
    [
        "task_pass_rate: {kind: statistical, direction: lower, threshold: 0.5, mode: report_only}",
        "latency_ms: {kind: distributional, direction: upper, coverage: 0.95}",
    ],
)
def test_single_trace_cannot_claim_unsupported_tier_coverage(tmp_path, monkeypatch, rule):
    result = _invoke("assert", tmp_path, monkeypatch, "  " + rule + "\n", "normal")
    assert result.exit_code == 2, result.output
    assert "maida run" in result.stderr
    assert "maida drift" in result.stderr


@pytest.mark.parametrize("surface", ["run", "drift"])
@pytest.mark.parametrize("regression", [False, True])
def test_distributional_rule_is_enforced_when_baseline_is_sufficient(tmp_path, monkeypatch, surface, regression):
    # One reference observation certifies 50% one-sided coverage. The separate
    # 95% case below must reject that same baseline, not reduce the requirement.
    rule = "  latency_ms: {kind: distributional, direction: upper, coverage: 0.5, mode: gating}\n"
    result = _invoke(
        surface,
        tmp_path,
        monkeypatch,
        rule,
        "latency-cost-envelope" if regression else "normal",
    )
    _assert_contract(result, surface, {"latency_ms"}, {"latency_ms"} if regression else set())


@pytest.mark.parametrize("surface", ["run", "drift"])
def test_insufficient_distributional_evidence_is_rejected_not_downgraded(tmp_path, monkeypatch, surface):
    rule = "  latency_ms: {kind: distributional, direction: upper, coverage: 0.95, mode: gating}\n"
    result = _invoke(surface, tmp_path, monkeypatch, rule, "normal")
    assert result.exit_code == 2, result.output
    assert "latency_ms" in result.stderr
    assert "0.95" in result.stderr
    assert "baseline" in result.stderr


@pytest.mark.parametrize("surface", ["run", "drift"])
@pytest.mark.parametrize("metric, rule, violation", INVARIANT_RULES, ids=[row[0] for row in INVARIANT_RULES])
def test_one_counterexample_fails_an_invariant_among_passing_trials(
    tmp_path, monkeypatch, surface, metric, rule, violation
):
    result = _invoke(
        surface,
        tmp_path,
        monkeypatch,
        f"  {metric}: {rule}\n",
        "normal",
        trials=3,
        violation=violation,
    )
    _assert_contract(result, surface, {metric}, {metric})
    report = json.loads(result.stdout)
    check = next(item for item in report["aggregate_results"] if item["check_name"] == metric)
    # Drift sorts by recorded time; run sorts by execution order. The contract
    # requires exactly one counterexample and two passing observations either way.
    assert sorted(check["trial_outcomes"]) == [False, True, True]
    assert check["trials_used"] == 3


def test_single_trace_rejects_plan_metrics_instead_of_dropping_them(tmp_path, monkeypatch):
    result = _invoke(
        "assert",
        tmp_path,
        monkeypatch,
        "  plan_depth: {kind: measured, direction: upper, limit: 2}\n",
        "normal",
        version="2.1",
    )
    assert result.exit_code == 2, result.output
    assert "plan_depth" in result.stderr
    assert "maida run" in result.stderr


@pytest.mark.parametrize("surface", ["run", "drift"])
def test_inconclusive_is_not_serialized_as_pass_despite_exit_zero(tmp_path, monkeypatch, surface):
    rule = "  latency_ms: {kind: distributional, direction: upper, coverage: 0.5, mode: gating}\n"
    result = _invoke(
        surface,
        tmp_path,
        monkeypatch,
        rule,
        "normal",
        trials=3,
        violation="latency-cost-envelope",
    )
    assert result.exit_code == 0, result.output
    report = json.loads(result.stdout)
    assert report["verdict"] == "inconclusive"
    assert report["passed"] is None
    metric = next(item for item in report["aggregate_results"] if item["check_name"] == "latency_ms")
    assert metric["mode"] == "gating"
    assert metric["verdict"] == "inconclusive"
