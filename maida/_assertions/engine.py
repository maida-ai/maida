"""Evaluate a completed run against policy and baseline."""

from __future__ import annotations

from typing import Any, Callable

from maida._assertions.types import (
    KNOWN_CHECK_NAMES,
    AssertionPolicy,
    AssertionReport,
    AssertionResult,
    RegressionReasonCode,
)
from maida.baseline_bind import validate_policy_against_baseline
from maida.gate import aggregate_metrics, invariant_outcomes, numeric_metrics
from maida.baseline import extract_run_metrics
from maida.config import MaidaConfig, load_config
from maida.policy_types import MetricKind, PLAN_METRIC_NAMES
from maida.statistics import GateVerdict
from maida.storage import load_run_for_analysis


def _reason_code_for(passed: bool, failure_code: RegressionReasonCode) -> RegressionReasonCode:
    return RegressionReasonCode.NO_REGRESSION if passed else failure_code


def _loop_signature_summary(events: list[dict], limit: int = 3) -> str:
    loop_events = [e for e in events if e.get("event_type") == "LOOP_WARNING"]
    summaries = []
    for event in loop_events[:limit]:
        payload = event.get("payload") or {}
        pattern = payload.get("pattern") or "unknown"
        pattern_type = payload.get("pattern_type") or "loop"
        repetitions = payload.get("repetitions")
        if repetitions is None:
            summaries.append(f"{pattern_type}: {pattern}")
        else:
            summaries.append(f"{pattern_type} x{repetitions}: {pattern}")
    if len(loop_events) > limit:
        summaries.append(f"+{len(loop_events) - limit} more")
    return "; ".join(summaries)


def _loop_reason_code(events: list[dict]) -> RegressionReasonCode:
    """Return the most specific reason code for recorded loop warnings."""
    for event in events:
        if event.get("event_type") != "LOOP_WARNING":
            continue
        payload = event.get("payload") or {}
        if payload.get("pattern_type") == "cycle":
            return RegressionReasonCode.CYCLE_DETECTED
    return RegressionReasonCode.LOOP_DETECTED


def _check_threshold(
    actual: int | float,
    baseline_value: int | float | None,
    tolerance: float,
    standalone_max: int | float | None,
    check_name: str,
    reason_code: RegressionReasonCode,
    unit: str,
) -> AssertionResult | None:
    """Shared threshold/tolerance comparison for numeric metrics.

    Returns an ``AssertionResult`` if any check was enabled, else ``None``.
    """
    if baseline_value is not None and standalone_max is not None:
        if baseline_value > 0:
            limit = min(
                baseline_value * (1 + tolerance),
                float(standalone_max),
            )
        else:
            limit = float(standalone_max)
        passed = actual <= limit
        return AssertionResult(
            check_name=check_name,
            passed=passed,
            message=(
                f"{int(actual)} {unit} (baseline: {int(baseline_value)}, "
                f"tolerance: {tolerance:.0%}, cap: {standalone_max})"
            ),
            reason_code=_reason_code_for(passed, reason_code),
            expected=str(int(limit)),
            actual=str(int(actual)),
        )

    if baseline_value is not None:
        if baseline_value > 0:
            limit = baseline_value * (1 + tolerance)
        else:
            limit = float(standalone_max) if standalone_max is not None else 0.0
        passed = actual <= limit
        return AssertionResult(
            check_name=check_name,
            passed=passed,
            message=(f"{int(actual)} {unit} (baseline: {int(baseline_value)}, tolerance: {tolerance:.0%})"),
            reason_code=_reason_code_for(passed, reason_code),
            expected=str(int(limit)),
            actual=str(int(actual)),
        )

    if standalone_max is not None:
        passed = actual <= standalone_max
        return AssertionResult(
            check_name=check_name,
            passed=passed,
            message=f"{int(actual)} {unit} (max: {standalone_max})",
            reason_code=_reason_code_for(passed, reason_code),
            expected=str(standalone_max),
            actual=str(int(actual)),
        )

    return None


def _run_metric_assertions(
    metrics: dict[str, Any],
    policy: AssertionPolicy,
    baseline: dict | None,
    report: AssertionReport,
) -> AssertionReport:
    """Evaluate the single-observation policy tiers with the shared gate engine."""

    unsupported = [
        name
        for name, metric in policy.metrics.items()
        if metric.kind not in {MetricKind.INVARIANT, MetricKind.MEASURED} or name in PLAN_METRIC_NAMES
    ]
    if unsupported:
        raise ValueError(
            f"Single-run assertions cannot evaluate {', '.join(unsupported)}; "
            "use `maida run` or `maida drift` for tier-aware policy evaluation"
        )
    validate_policy_against_baseline(policy, baseline)
    results = aggregate_metrics(
        policy=policy,
        trial_values=[numeric_metrics(metrics)],
        trial_invariants=[invariant_outcomes(metrics, policy, baseline)],
        process_outcomes=[True],
        baseline=baseline,
        trials_budgeted=1,
        stopping_rule="fixed_n",
    )
    codes = {
        "step_count": RegressionReasonCode.STEP_COUNT_EXCEEDED,
        "tool_call_count": RegressionReasonCode.TOOL_CALL_COUNT_EXCEEDED,
        "cost_tokens": RegressionReasonCode.COST_ENVELOPE_EXCEEDED,
        "latency_ms": RegressionReasonCode.LATENCY_ENVELOPE_EXCEEDED,
        "forbidden_tools": RegressionReasonCode.NEW_TOOL_PATH,
        "required_tools": RegressionReasonCode.NEW_TOOL_PATH,
        "no_new_tools": RegressionReasonCode.NEW_TOOL_PATH,
        "no_loops": RegressionReasonCode.LOOP_DETECTED,
        "no_guardrails": RegressionReasonCode.GUARDRAIL_EVENT_CHANGED,
        "stop_condition_reached": RegressionReasonCode.TERMINAL_STATE_MISSING,
    }
    for result in results:
        if result.check_name == "agent_process":
            continue  # A stored trace has no separately observed process exit code.
        flag_name = {
            "tool_call_count": "tool_calls",
            "no_new_tools": "new_tools",
            "latency_ms": "duration",
            "stop_condition_reached": "expect_status",
        }.get(result.check_name, result.check_name)
        if flag_name in policy.ignored_checks:
            report.add(
                AssertionResult(
                    check_name=result.check_name,
                    passed=True,
                    message="check ignored",
                    ignored=True,
                )
            )
            continue
        passed = result.verdict is GateVerdict.PASS
        evidence = result.evidence
        expected = evidence.get("allowed", "invariant holds")
        actual = evidence.get("observed", evidence.get("description"))
        metric = policy.metrics[result.check_name]
        if result.check_name == "forbidden_tools":
            expected = f"none of {list(metric.none_of)}"
            actual = metrics.get("tool_path") or []
        elif result.check_name == "no_new_tools":
            expected = f"only tools from {baseline['tool_path']}"
            actual = metrics.get("tool_path") or []
        elif result.check_name == "required_tools":
            expected = f"all of {list(metric.all_of)}"
            actual = metrics.get("tool_path") or []
        report.add(
            AssertionResult(
                check_name=result.check_name,
                passed=passed,
                message=f"{result.check_name}: expected {expected}; observed {actual}",
                reason_code=_reason_code_for(passed, codes[result.check_name]),
                expected=str(expected),
                actual=str(actual),
            )
        )
    return report


def run_assertions(
    trace_id: str,
    policy: AssertionPolicy,
    baseline: dict | None = None,
    config: MaidaConfig | None = None,
) -> AssertionReport:
    """Run all enabled assertion checks against a completed run.

    Args:
        trace_id: The OTel trace ID (or prefix) for the run.
        policy: The assertion policy with thresholds.
        baseline: Optional baseline dict to compare against.
        config: MaidaConfig (loaded via ``load_config`` if ``None``).

    Returns:
        ``AssertionReport`` with all check results.
    """
    if config is None:
        config = load_config()

    full_id, meta, events = load_run_for_analysis(trace_id, config)
    metrics = extract_run_metrics(meta, events)
    summary = metrics["summary"]
    b_summary = (baseline or {}).get("summary")

    report = AssertionReport(
        run_id=full_id,
        baseline_run_id=(baseline or {}).get("source_run_id"),
        baseline_acceptance=(
            (baseline or {}).get("acceptance") if isinstance((baseline or {}).get("acceptance"), dict) else None
        ),
    )

    if policy.source_format == "v2":
        report = _run_metric_assertions(metrics, policy, baseline, report)

    _ignored = set(policy.ignored_checks)
    if unknown := _ignored - KNOWN_CHECK_NAMES:
        raise ValueError(
            f"Unknown check name(s) in ignored_checks: {', '.join(sorted(unknown))}. "
            f"Known checks: {', '.join(sorted(KNOWN_CHECK_NAMES))}"
        )

    # --- per-check runner helpers (return None when check is not enabled) ---
    def _threshold(
        name: str,
        actual: int | float,
        baseline_val: int | float | None,
        tolerance: float,
        max_val: int | float | None,
        reason: RegressionReasonCode,
        unit: str,
    ) -> Callable[[], AssertionResult | None]:
        return lambda: _check_threshold(actual, baseline_val, tolerance, max_val, name, reason, unit)

    def _check_new_tools() -> AssertionResult | None:
        if not (policy.no_new_tools and baseline is not None):
            return None
        bl_tools = set(baseline.get("tool_path") or [])
        run_tools = set(metrics["tool_path"])
        new_tools = sorted(run_tools - bl_tools)
        passed = len(new_tools) == 0
        return AssertionResult(
            check_name="new_tools",
            passed=passed,
            message=("no new tools" if passed else f"unexpected tools used: {new_tools}"),
            reason_code=_reason_code_for(passed, RegressionReasonCode.NEW_TOOL_PATH),
            expected="none",
            actual=str(new_tools) if new_tools else "none",
        )

    def _check_no_loops() -> AssertionResult | None:
        if not policy.no_loops:
            return None
        loop_count = summary["loop_warnings"]
        passed = loop_count == 0
        sig = _loop_signature_summary(events) if not passed else ""
        message = "no loop warnings detected"
        if not passed:
            message = f"{loop_count} loop warning(s) detected"
            if sig:
                message += f": {sig}"
        return AssertionResult(
            check_name="no_loops",
            passed=passed,
            message=message,
            reason_code=_reason_code_for(passed, _loop_reason_code(events)),
            actual=str(loop_count),
        )

    def _check_no_guardrails() -> AssertionResult | None:
        if not policy.no_guardrails:
            return None
        gr_count = len(metrics["guardrail_events"])
        passed = gr_count == 0
        return AssertionResult(
            check_name="no_guardrails",
            passed=passed,
            message=("no guardrail events" if passed else f"{gr_count} guardrail event(s) detected"),
            reason_code=_reason_code_for(passed, RegressionReasonCode.GUARDRAIL_EVENT_CHANGED),
            actual=str(gr_count),
        )

    def _check_expect_status() -> AssertionResult | None:
        if policy.expect_status is None:
            return None
        actual_status = meta.get("status", "")
        passed = actual_status == policy.expect_status
        return AssertionResult(
            check_name="expect_status",
            passed=passed,
            message=(
                f"status is '{actual_status}'"
                if passed
                else f"expected '{policy.expect_status}', got '{actual_status}'"
            ),
            reason_code=_reason_code_for(passed, RegressionReasonCode.TERMINAL_STATE_MISSING),
            expected=policy.expect_status,
            actual=actual_status,
        )

    # --- unified runner dispatch ---
    runners: dict[str, Callable[[], AssertionResult | None]] = {
        "step_count": _threshold(
            "step_count",
            summary["total_events"],
            b_summary["total_events"] if b_summary else None,
            policy.step_tolerance,
            policy.max_steps,
            RegressionReasonCode.STEP_COUNT_EXCEEDED,
            "steps",
        ),
        "tool_calls": _threshold(
            "tool_calls",
            summary["tool_calls"],
            b_summary["tool_calls"] if b_summary else None,
            policy.tool_call_tolerance,
            policy.max_tool_calls,
            RegressionReasonCode.TOOL_CALL_COUNT_EXCEEDED,
            "tool calls",
        ),
        "new_tools": _check_new_tools,
        "no_loops": _check_no_loops,
        "no_guardrails": _check_no_guardrails,
        "cost_tokens": _threshold(
            "cost_tokens",
            summary["total_tokens"],
            b_summary["total_tokens"] if b_summary else None,
            policy.cost_tolerance,
            policy.max_cost_tokens,
            RegressionReasonCode.COST_ENVELOPE_EXCEEDED,
            "tokens",
        ),
        "duration": _threshold(
            "duration",
            summary["duration_ms"],
            b_summary["duration_ms"] if b_summary else None,
            policy.duration_tolerance,
            policy.max_duration_ms,
            RegressionReasonCode.LATENCY_ENVELOPE_EXCEEDED,
            "ms",
        ),
        "expect_status": _check_expect_status,
    }

    for name, runner in runners.items():
        if policy.source_format == "v2" and name in {
            "step_count",
            "tool_calls",
            "cost_tokens",
            "duration",
        }:
            continue  # Numeric CLI overrides are already in the metric policy.
        r = runner()
        if r and name in _ignored:
            report.add(
                AssertionResult(
                    check_name=name,
                    passed=True,
                    message="check ignored",
                    ignored=True,
                )
            )
        elif r:
            report.add(r)

    return report
