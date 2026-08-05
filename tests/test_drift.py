"""Windowed scheduled behavioral regression checks."""

from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.assertions import AssertionPolicy
from maida.baseline import create_baseline, save_baseline
from maida.cli import app
from maida.config import load_config
from maida.drift import DriftWindowError, run_drift
from maida.policy_types import MetricDirection, MetricKind, MetricMode, MetricPolicy
from maida.statistics import GateVerdict


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "traces" / "current"
runner = CliRunner()


def _copy_trace(
    fixture: str,
    runs_dir: Path,
    *,
    trace_id: str,
    run_name: str,
    started_at: str = "2026-08-01T00:00:00.000Z",
) -> Path:
    source = FIXTURES / fixture
    destination = runs_dir / trace_id
    shutil.copytree(source, destination)

    meta_path = destination / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_trace_id = meta["trace_id"]
    meta["trace_id"] = trace_id
    meta["run_name"] = run_name
    meta["started_at"] = started_at
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    spans_path = destination / "spans.jsonl"
    spans = []
    for line in spans_path.read_text(encoding="utf-8").splitlines():
        span = json.loads(line)
        assert span["trace_id"] == old_trace_id
        span["trace_id"] = trace_id
        spans.append(span)
    spans_path.write_text(
        "".join(json.dumps(span) + "\n" for span in spans), encoding="utf-8"
    )
    return destination


def _baseline(tmp_path: Path, *, agent: str = "orders-agent") -> dict:
    data_dir = tmp_path / "baseline-data"
    runs_dir = data_dir / "runs"
    runs_dir.mkdir(parents=True)
    trace_id = "a" * 32
    _copy_trace("normal", runs_dir, trace_id=trace_id, run_name=agent)
    config = load_config()
    config.data_dir = data_dir
    return create_baseline(trace_id, config)


def _hash_tree(path: Path) -> dict[str, str]:
    return {
        item.relative_to(path).as_posix(): sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def test_run_drift_filters_one_agent_and_does_not_modify_window(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)
    selected = _copy_trace(
        "normal",
        runs_dir,
        trace_id="1" * 32,
        run_name="orders-agent",
        started_at="2026-08-01T00:00:01.000Z",
    )
    _copy_trace(
        "tool-call-spike",
        runs_dir,
        trace_id="2" * 32,
        run_name="billing-agent",
    )
    before = _hash_tree(runs_dir)

    report = run_drift(
        runs_dir,
        baseline=baseline,
        policy=AssertionPolicy(),
        config=load_config(),
    )

    assert report.report_kind == "drift"
    assert report.agent_name == "orders-agent"
    assert [trial.trace_id for trial in report.trials] == [selected.name]
    assert report.trials[0].process_exit_code is None
    assert report.trials[0].run_status == "ok"
    assert report.verdict is GateVerdict.PASS
    assert _hash_tree(runs_dir) == before


def test_run_drift_reports_confirmed_structural_variance(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace(
        "tool-call-spike",
        runs_dir,
        trace_id="3" * 32,
        run_name="orders-agent",
    )

    report = run_drift(
        runs_dir,
        baseline=baseline,
        policy=AssertionPolicy(max_tool_calls=1, no_new_tools=True),
        config=load_config(),
    )

    assert report.verdict is GateVerdict.FAIL
    assert report.trials[0].baseline_diff is not None
    assert report.trials[0].baseline_diff["new_tools"]
    markdown = report.to_markdown()
    assert markdown.startswith("## ❌ Maida drift check: fail")
    assert "`tool_call_count`" in markdown
    assert "### Window traces" in markdown


def test_run_drift_preserves_inconclusive_statistical_verdict(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)
    for index in range(3):
        _copy_trace(
            "normal",
            runs_dir,
            trace_id=str(index + 4) * 32,
            run_name="orders-agent",
            started_at=f"2026-08-01T00:00:0{index}.000Z",
        )
    task_pass_rate = MetricPolicy(
        name="task_pass_rate",
        kind=MetricKind.STATISTICAL,
        direction=MetricDirection.LOWER,
        threshold=0.90,
        confidence=0.95,
        mode=MetricMode.GATING,
        success_predicate="all_invariants_passed",
        aggregate="",
    )
    policy = AssertionPolicy(
        trials=3,
        source_format="v2",
        policy_version=(2, 0),
        metrics={"task_pass_rate": task_pass_rate},
    )

    report = run_drift(
        runs_dir,
        baseline=baseline,
        policy=policy,
        config=load_config(),
    )

    assert report.verdict is GateVerdict.INCONCLUSIVE
    assert "Neutral result" in report.to_markdown()


def test_run_drift_uses_stable_oldest_first_order(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="8" * 32,
        run_name="orders-agent",
        started_at="2026-08-02T00:00:00.000Z",
    )
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="9" * 32,
        run_name="orders-agent",
        started_at="2026-08-01T00:00:00.000Z",
    )

    report = run_drift(
        runs_dir,
        baseline=baseline,
        policy=AssertionPolicy(),
        config=load_config(),
    )

    assert [trial.trace_id for trial in report.trials] == ["9" * 32, "8" * 32]


@pytest.mark.parametrize(
    ("baseline_agent", "selected_agent", "message"),
    [
        (None, None, "does not identify an agent"),
        ("orders-agent", "billing-agent", "does not match baseline agent"),
    ],
)
def test_run_drift_requires_unambiguous_agent_selection(
    tmp_path: Path,
    baseline_agent: str | None,
    selected_agent: str | None,
    message: str,
) -> None:
    baseline = _baseline(tmp_path)
    baseline["source_run_name"] = baseline_agent
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace("normal", runs_dir, trace_id="b" * 32, run_name="orders-agent")

    with pytest.raises(DriftWindowError, match=message):
        run_drift(
            runs_dir,
            baseline=baseline,
            policy=AssertionPolicy(),
            config=load_config(),
            agent_name=selected_agent,
        )


def test_run_drift_rejects_empty_and_corrupt_windows(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)

    with pytest.raises(DriftWindowError, match="contains no traces"):
        run_drift(
            runs_dir,
            baseline=baseline,
            policy=AssertionPolicy(),
            config=load_config(),
        )

    trace_dir = _copy_trace(
        "normal", runs_dir, trace_id="c" * 32, run_name="orders-agent"
    )
    (trace_dir / "spans.jsonl").write_text("not json\n", encoding="utf-8")
    with pytest.raises(DriftWindowError, match="malformed JSON"):
        run_drift(
            runs_dir,
            baseline=baseline,
            policy=AssertionPolicy(),
            config=load_config(),
        )


def test_run_drift_rejects_wrong_layout_incomplete_and_unsupported_traces(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path)
    wrong_layout = tmp_path / "window"
    wrong_layout.mkdir()
    with pytest.raises(DriftWindowError, match="ending in /runs"):
        run_drift(
            wrong_layout,
            baseline=baseline,
            policy=AssertionPolicy(),
            config=load_config(),
        )

    incomplete_runs = tmp_path / "incomplete" / "runs"
    incomplete_runs.mkdir(parents=True)
    _copy_trace(
        "missing-terminal-state",
        incomplete_runs,
        trace_id="f" * 32,
        run_name="orders-agent",
    )
    with pytest.raises(DriftWindowError, match="is incomplete"):
        run_drift(
            incomplete_runs,
            baseline=baseline,
            policy=AssertionPolicy(),
            config=load_config(),
        )

    unsupported_runs = tmp_path / "unsupported" / "runs"
    unsupported_runs.mkdir(parents=True)
    trace_dir = _copy_trace(
        "normal",
        unsupported_runs,
        trace_id="0" * 31 + "1",
        run_name="orders-agent",
    )
    meta_path = trace_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["spec_version"] = "9.0.0"
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(DriftWindowError, match="unsupported spec_version"):
        run_drift(
            unsupported_runs,
            baseline=baseline,
            policy=AssertionPolicy(),
            config=load_config(),
        )


def test_run_drift_supports_explicit_legacy_agent_and_rejects_no_match(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path)
    baseline["source_run_name"] = None
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace("normal", runs_dir, trace_id="0" * 31 + "2", run_name="orders-agent")

    report = run_drift(
        runs_dir,
        baseline=baseline,
        policy=AssertionPolicy(),
        config=load_config(),
        agent_name="orders-agent",
    )
    assert report.agent_name == "orders-agent"

    with pytest.raises(DriftWindowError, match="contains no completed traces"):
        run_drift(
            runs_dir,
            baseline=baseline,
            policy=AssertionPolicy(),
            config=load_config(),
            agent_name="billing-agent",
        )


def test_drift_cli_writes_machine_report_and_uses_scheduler_exit_codes(
    tmp_path: Path,
) -> None:
    baseline = _baseline(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    save_baseline(baseline, baseline_path)
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace(
        "tool-call-spike",
        runs_dir,
        trace_id="d" * 32,
        run_name="orders-agent",
    )
    report_path = tmp_path / "drift.json"

    result = runner.invoke(
        app,
        [
            "drift",
            "--window",
            str(runs_dir),
            "--baseline",
            str(baseline_path),
            "--format",
            "markdown",
            "--json-out",
            str(report_path),
        ],
    )

    assert result.exit_code == 1, result.output
    assert result.stdout.startswith("## ❌ Maida drift check: fail")
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["report_version"] == "2.0.0"
    assert payload["report_kind"] == "drift"
    assert payload["metadata"]["agent_name"] == "orders-agent"
    assert payload["metadata"]["window_input_format"] == "maida_runs"
    assert payload["trials"][0]["process_exit_code"] is None
    assert payload["trials"][0]["run_status"] == "ok"


def test_drift_cli_rejects_baseline_directory_and_missing_agent(tmp_path: Path) -> None:
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)

    directory_result = runner.invoke(
        app,
        ["drift", "--window", str(runs_dir), "--baseline", str(tmp_path)],
    )
    assert directory_result.exit_code == 2
    assert "Baseline must be a JSON file" in directory_result.stderr

    baseline = _baseline(tmp_path)
    baseline["source_run_name"] = None
    baseline_path = tmp_path / "legacy-baseline.json"
    save_baseline(baseline, baseline_path)
    _copy_trace("normal", runs_dir, trace_id="e" * 32, run_name="orders-agent")
    agent_result = runner.invoke(
        app,
        [
            "drift",
            "--window",
            str(runs_dir),
            "--baseline",
            str(baseline_path),
        ],
    )
    assert agent_result.exit_code == 2
    assert "pass --agent" in agent_result.stderr


def test_drift_cli_returns_zero_for_pass_and_inconclusive(tmp_path: Path) -> None:
    baseline = _baseline(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    save_baseline(baseline, baseline_path)
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)
    for index in range(3):
        _copy_trace(
            "normal",
            runs_dir,
            trace_id=f"{index + 10:032x}",
            run_name="orders-agent",
            started_at=f"2026-08-01T00:00:0{index}.000Z",
        )

    passed = runner.invoke(
        app,
        [
            "drift",
            "--window",
            str(runs_dir),
            "--baseline",
            str(baseline_path),
        ],
    )
    assert passed.exit_code == 0, passed.output
    assert "RESULT: PASS" in passed.stdout

    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """
version: 2
trials: 25
fail_fast: false
metrics:
  task_pass_rate:
    kind: statistical
    direction: lower
    threshold: 0.90
    confidence: 0.95
    success_predicate: all_invariants_passed
    mode: gating
""".lstrip(),
        encoding="utf-8",
    )
    inconclusive = runner.invoke(
        app,
        [
            "drift",
            "--window",
            str(runs_dir),
            "--baseline",
            str(baseline_path),
            "--policy",
            str(policy_path),
            "--format",
            "json",
        ],
    )
    assert inconclusive.exit_code == 0, inconclusive.output
    assert json.loads(inconclusive.stdout)["verdict"] == "inconclusive"


def test_drift_cli_maps_unexpected_failures_to_internal_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = _baseline(tmp_path)
    baseline_path = tmp_path / "baseline.json"
    save_baseline(baseline, baseline_path)
    runs_dir = tmp_path / "window" / "runs"
    runs_dir.mkdir(parents=True)

    def fail(*args, **kwargs):
        del args, kwargs
        raise RuntimeError("unexpected evaluator failure")

    monkeypatch.setattr("maida.cli.run_drift", fail)
    result = runner.invoke(
        app,
        [
            "drift",
            "--window",
            str(runs_dir),
            "--baseline",
            str(baseline_path),
        ],
    )

    assert result.exit_code == 10
    assert "unexpected evaluator failure" in result.stderr


def test_drift_report_schema_allows_stored_trace_status() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "statistical-gate-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    trial_properties = schema["properties"]["trials"]["items"]["properties"]

    assert trial_properties["process_exit_code"]["type"] == ["integer", "null"]
    assert "run_status" in trial_properties
