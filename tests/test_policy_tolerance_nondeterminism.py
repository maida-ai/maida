"""Deterministic tolerance fixtures for harmless run-to-run variance."""

import json
from collections.abc import Callable
from typing import Any

from maida import record_state, record_tool_call, traced_run
from maida.assertions import AssertionPolicy, RegressionReasonCode, run_assertions
from maida.baseline import create_baseline
from maida.config import load_config
from tests.conftest import get_latest_run_id


def _patch_run_meta(config, trace_id: str, **updates: Any) -> None:
    meta_path = config.data_dir / "runs" / trace_id / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(updates)
    meta_path.write_text(json.dumps(meta), encoding="utf-8")


def _make_state_run(config, *, name: str, state_steps: int, duration_ms: int) -> str:
    with traced_run(name=name):
        for step in range(state_steps):
            record_state(state={"step": step})

    trace_id = get_latest_run_id(config)
    _patch_run_meta(config, trace_id, duration_ms=duration_ms)
    return trace_id


def _make_tool_run(
    config,
    *,
    name: str,
    tools: tuple[str, ...],
    duration_ms: int,
    tool_meta: Callable[[int, str], dict[str, Any] | None] | None = None,
    **meta_updates: Any,
) -> str:
    with traced_run(name=name):
        for index, tool_name in enumerate(tools):
            meta = tool_meta(index, tool_name) if tool_meta else None
            record_tool_call(
                tool_name,
                args={"query": tool_name},
                result={"ok": True},
                meta=meta,
            )

    trace_id = get_latest_run_id(config)
    _patch_run_meta(config, trace_id, duration_ms=duration_ms, **meta_updates)
    return trace_id


def _exact_numeric_policy(**overrides: Any) -> AssertionPolicy:
    values = {
        "step_tolerance": 0.0,
        "tool_call_tolerance": 0.0,
        "cost_tolerance": 0.0,
        "duration_tolerance": 0.0,
    }
    values.update(overrides)
    return AssertionPolicy(**values)


def _assert_passed_check(report, check_name: str):
    result = next((r for r in report.results if r.check_name == check_name), None)
    assert result is not None, f"{check_name} check did not run"
    assert result.passed is True
    return result


def test_small_step_count_variance_passes_under_policy_tolerance(temp_data_dir):
    config = load_config()
    baseline_run = _make_state_run(
        config, name="baseline-steps", state_steps=4, duration_ms=1000
    )
    baseline = create_baseline(baseline_run, config)
    current_run = _make_state_run(
        config, name="current-steps", state_steps=5, duration_ms=1000
    )

    report = run_assertions(
        current_run,
        _exact_numeric_policy(step_tolerance=0.25),
        baseline=baseline,
        config=config,
    )

    assert report.passed is True
    step_result = next(r for r in report.results if r.check_name == "step_count")
    assert step_result.passed is True
    assert step_result.expected == "5"
    assert step_result.actual == "5"


def test_small_latency_variance_passes_under_policy_tolerance(temp_data_dir):
    config = load_config()
    baseline_run = _make_tool_run(
        config, name="baseline-latency", tools=("search",), duration_ms=1000
    )
    baseline = create_baseline(baseline_run, config)
    current_run = _make_tool_run(
        config, name="current-latency", tools=("search",), duration_ms=1100
    )

    report = run_assertions(
        current_run,
        _exact_numeric_policy(duration_tolerance=0.2),
        baseline=baseline,
        config=config,
    )

    assert report.passed is True
    duration_result = next(r for r in report.results if r.check_name == "duration")
    assert duration_result.passed is True
    assert duration_result.expected == "1200"
    assert duration_result.actual == "1100"


def test_benign_run_metadata_differences_do_not_fail_structural_policy(
    temp_data_dir,
):
    config = load_config()
    baseline_run = _make_tool_run(
        config,
        name="run-metadata-baseline",
        tools=("search",),
        duration_ms=1000,
        run_name="renamed-baseline",
        started_at="2026-01-01T00:00:00.000Z",
        ended_at="2026-01-01T00:00:01.000Z",
    )
    baseline = create_baseline(baseline_run, config)
    current_run = _make_tool_run(
        config,
        name="run-metadata-current",
        tools=("search",),
        duration_ms=1000,
        run_name="renamed-current",
        started_at="2026-01-02T00:00:00.000Z",
        ended_at="2026-01-02T00:00:01.000Z",
    )

    report = run_assertions(
        current_run,
        _exact_numeric_policy(no_new_tools=True),
        baseline=baseline,
        config=config,
    )

    assert report.passed is True
    new_tools_result = _assert_passed_check(report, "new_tools")
    assert new_tools_result.expected == "none"
    assert new_tools_result.actual == "none"


def test_benign_tool_metadata_differences_do_not_fail_structural_policy(
    temp_data_dir,
):
    config = load_config()
    baseline_run = _make_tool_run(
        config,
        name="tool-metadata-baseline",
        tools=("search",),
        duration_ms=1000,
        tool_meta=lambda *_: {"request_id": "baseline-request"},
    )
    baseline = create_baseline(baseline_run, config)
    current_run = _make_tool_run(
        config,
        name="tool-metadata-current",
        tools=("search",),
        duration_ms=1000,
        tool_meta=lambda *_: {"request_id": "current-request"},
    )

    report = run_assertions(
        current_run,
        _exact_numeric_policy(no_new_tools=True),
        baseline=baseline,
        config=config,
    )

    assert report.passed is True
    new_tools_result = _assert_passed_check(report, "new_tools")
    assert new_tools_result.expected == "none"
    assert new_tools_result.actual == "none"


def test_benign_tool_ordering_passes_without_exact_sequence_policy(temp_data_dir):
    config = load_config()
    baseline_run = _make_tool_run(
        config,
        name="ordering-baseline",
        tools=("search", "summarize"),
        duration_ms=1000,
    )
    baseline = create_baseline(baseline_run, config)
    current_run = _make_tool_run(
        config,
        name="ordering-current",
        tools=("summarize", "search"),
        duration_ms=1000,
    )

    report = run_assertions(
        current_run,
        _exact_numeric_policy(no_new_tools=True),
        baseline=baseline,
        config=config,
    )

    assert report.passed is True
    new_tools_result = _assert_passed_check(report, "new_tools")
    assert new_tools_result.expected == "none"
    assert new_tools_result.actual == "none"


def test_exact_step_policy_fails_on_small_step_variance(temp_data_dir):
    config = load_config()
    baseline_run = _make_state_run(
        config, name="exact-baseline-steps", state_steps=4, duration_ms=1000
    )
    baseline = create_baseline(baseline_run, config)
    current_run = _make_state_run(
        config, name="exact-current-steps", state_steps=5, duration_ms=1000
    )

    report = run_assertions(
        current_run,
        _exact_numeric_policy(),
        baseline=baseline,
        config=config,
    )

    assert report.passed is False
    step_result = next(r for r in report.results if r.check_name == "step_count")
    assert step_result.passed is False
    assert step_result.reason_code == RegressionReasonCode.STEP_COUNT_EXCEEDED.value
    assert step_result.expected == "4"
    assert step_result.actual == "5"


def test_exact_latency_policy_fails_on_small_latency_variance(temp_data_dir):
    config = load_config()
    baseline_run = _make_tool_run(
        config, name="exact-baseline-latency", tools=("search",), duration_ms=1000
    )
    baseline = create_baseline(baseline_run, config)
    current_run = _make_tool_run(
        config, name="exact-current-latency", tools=("search",), duration_ms=1001
    )

    report = run_assertions(
        current_run,
        _exact_numeric_policy(),
        baseline=baseline,
        config=config,
    )

    assert report.passed is False
    duration_result = next(r for r in report.results if r.check_name == "duration")
    assert duration_result.passed is False
    assert (
        duration_result.reason_code
        == RegressionReasonCode.LATENCY_ENVELOPE_EXCEEDED.value
    )
    assert duration_result.expected == "1000"
    assert duration_result.actual == "1001"
