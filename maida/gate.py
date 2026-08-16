"""Tier-aware metric extraction and fixed-budget gate aggregation."""

from __future__ import annotations

import math
from dataclasses import replace
from statistics import median
from typing import Any, Iterable

from maida.assertions import AssertionPolicy
from maida.policy import minimum_trials_for_pass
from maida.policy_types import MetricDirection, MetricKind, MetricMode, MetricPolicy
from maida.policy_types import PLAN_METRIC_NAMES
from maida.statistics import (
    GateVerdict,
    StatisticalResult,
    aggregate_outcomes,
    distributional_minimum_baseline_trials,
)


_SUMMARY_KEYS = {
    "step_count": "total_events",
    "tool_call_count": "tool_calls",
    "cost_tokens": "total_tokens",
    "latency_ms": "duration_ms",
    "llm_call_count": "llm_calls",
    "error_count": "errors",
    "loop_warning_count": "loop_warnings",
}


def numeric_metrics(metrics: dict[str, Any]) -> dict[str, float]:
    """Return the canonical numeric vector for one completed trial."""
    summary = metrics["summary"]
    return {
        name: float(summary.get(source, 0) or 0)
        for name, source in _SUMMARY_KEYS.items()
    }


def structural_signature(metrics: dict[str, Any]) -> dict[str, Any]:
    """Return the structural fields deduplicated in a baseline sample."""
    return {
        "tool_path": metrics.get("tool_path") or [],
        "tool_call_sequence": metrics.get("tool_call_sequence") or [],
        "tool_call_counts": metrics.get("tool_call_counts") or {},
        "llm_models_used": metrics.get("llm_models_used") or [],
        "event_type_sequence": metrics.get("event_type_sequence") or [],
        "final_status": metrics.get("final_status") or "",
    }


def invariant_outcomes(
    metrics: dict[str, Any],
    policy: AssertionPolicy,
    baseline: dict[str, Any] | None,
) -> dict[str, bool]:
    """Evaluate semantic contracts for one trial without statistical inference."""
    result: dict[str, bool] = {}
    summary = metrics["summary"]
    tool_path = set(metrics.get("tool_path") or [])
    for name, metric in policy.metrics.items():
        if metric.kind is not MetricKind.INVARIANT:
            continue
        if name == "stop_condition_reached":
            reached = summary.get("status") == "ok"
            result[name] = reached is bool(metric.require)
        elif name == "forbidden_tools":
            forbidden = set(metric.none_of)
            if policy.source_format == "v1" and not forbidden:
                baseline_tools = set((baseline or {}).get("tool_path") or [])
                result[name] = not (tool_path - baseline_tools)
            else:
                result[name] = not bool(tool_path & forbidden)
        elif name == "required_tools":
            result[name] = set(metric.all_of) <= tool_path
        elif name == "no_loops":
            result[name] = int(summary.get("loop_warnings", 0) or 0) == 0
        elif name == "no_guardrails":
            result[name] = not bool(metrics.get("guardrail_events"))
    return result


def aggregate_value(values: Iterable[float], aggregate: str) -> float:
    """Aggregate a measured sample according to the declared typical/tail rule."""
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("at least one measured value is required")
    if aggregate == "median":
        return float(median(ordered))
    if aggregate == "max":
        return ordered[-1]
    if aggregate == "p90":
        return ordered[max(0, math.ceil(0.90 * len(ordered)) - 1)]
    raise ValueError(f"unsupported aggregate: {aggregate}")


def baseline_values(baseline: dict[str, Any], name: str) -> list[float]:
    """Read a baseline vector, falling back to one legacy aggregate value."""
    sample = baseline.get("trial_sample") or {}
    vector = (sample.get("metrics") or {}).get(name)
    if isinstance(vector, list) and vector:
        return [float(value) for value in vector]
    summary = baseline.get("summary") or {}
    source = _SUMMARY_KEYS.get(name)
    if source is not None and isinstance(summary.get(source), (int, float)):
        return [float(summary[source])]
    return []


def _resolve_mode(
    metric: MetricPolicy,
    *,
    policy_trials: int,
    baseline_trials: int | None = None,
) -> MetricMode:
    if metric.mode is not None:
        return metric.mode
    if metric.kind is MetricKind.STATISTICAL:
        n_min = minimum_trials_for_pass(
            metric.threshold, metric.confidence, metric.direction
        )
        return MetricMode.GATING if policy_trials >= n_min else MetricMode.REPORT_ONLY
    if metric.kind is MetricKind.DISTRIBUTIONAL:
        required = distributional_minimum_baseline_trials(metric.coverage)
        return (
            MetricMode.GATING
            if (baseline_trials or 0) >= required
            else MetricMode.REPORT_ONLY
        )
    return MetricMode.GATING


def _measured_bounds(
    metric: MetricPolicy,
    baseline_value: float | None,
) -> tuple[float | None, float | None]:
    lower: float | None = None
    upper: float | None = None
    if baseline_value is not None:
        allowance = abs(baseline_value) * float(
            metric.tolerance_relative or 0.0
        ) + float(metric.tolerance_absolute or 0.0)
        if metric.direction in {MetricDirection.LOWER, MetricDirection.BOTH}:
            lower = baseline_value - allowance
        if metric.direction in {MetricDirection.UPPER, MetricDirection.BOTH}:
            upper = baseline_value + allowance
    if metric.limit is not None:
        if metric.direction is MetricDirection.UPPER:
            limit_upper = float(metric.limit)
            upper = limit_upper if upper is None else min(upper, limit_upper)
        elif metric.direction is MetricDirection.LOWER:
            limit_lower = float(metric.limit)
            lower = limit_lower if lower is None else max(lower, limit_lower)
        else:
            limit_lower, limit_upper = metric.limit
            lower = limit_lower if lower is None else max(lower, limit_lower)
            upper = limit_upper if upper is None else min(upper, limit_upper)
    return lower, upper


def _direct_result(
    *,
    name: str,
    kind: MetricKind,
    verdict: GateVerdict | None,
    rule: str,
    trials_used: int,
    trials_budgeted: int,
    direction: MetricDirection | None,
    mode: MetricMode,
    stopping_rule: str,
    outcomes: Iterable[bool] = (),
    evidence: dict[str, Any] | None = None,
) -> StatisticalResult:
    return StatisticalResult(
        check_name=name,
        kind=kind.value,
        verdict=verdict,
        decision_rule=rule if mode is MetricMode.GATING else "report_only",
        trials_used=trials_used,
        trials_budgeted=trials_budgeted,
        direction=direction.value if direction is not None else None,
        mode=mode.value,
        stopping_rule=stopping_rule,
        trial_outcomes=tuple(outcomes),
        evidence=evidence or {},
    )


def aggregate_metrics(
    *,
    policy: AssertionPolicy,
    trial_values: list[dict[str, float]],
    trial_invariants: list[dict[str, bool]],
    process_outcomes: list[bool],
    baseline: dict[str, Any] | None,
    trials_budgeted: int,
    stopping_rule: str,
) -> list[StatisticalResult]:
    """Aggregate every configured metric according to its criterion source."""
    used = len(trial_values)
    results: list[StatisticalResult] = []

    process_verdict = GateVerdict.PASS if all(process_outcomes) else GateVerdict.FAIL
    results.append(
        _direct_result(
            name="agent_process",
            kind=MetricKind.INVARIANT,
            verdict=process_verdict,
            rule="invariant",
            trials_used=used,
            trials_budgeted=trials_budgeted,
            direction=None,
            mode=MetricMode.GATING,
            stopping_rule=stopping_rule,
            outcomes=process_outcomes,
            evidence={
                "violations": len(process_outcomes) - sum(process_outcomes),
                "description": "agent process exited successfully",
            },
        )
    )

    for name, metric in policy.metrics.items():
        values = [trial[name] for trial in trial_values if name in trial]
        if (
            name in PLAN_METRIC_NAMES
            and not values
            and metric.kind is not MetricKind.INVARIANT
        ):
            raise ValueError(
                f"metrics.{name} requires pre-execution plan evidence from a plan backend"
            )
        if metric.kind is MetricKind.INVARIANT:
            outcomes = [trial.get(name, False) for trial in trial_invariants]
            passed = all(outcomes)
            results.append(
                _direct_result(
                    name=name,
                    kind=metric.kind,
                    verdict=GateVerdict.PASS if passed else GateVerdict.FAIL,
                    rule="invariant",
                    trials_used=used,
                    trials_budgeted=trials_budgeted,
                    direction=None,
                    mode=MetricMode.GATING,
                    stopping_rule=stopping_rule,
                    outcomes=outcomes,
                    evidence={
                        "violations": len(outcomes) - sum(outcomes),
                        "description": (
                            f"violated in {len(outcomes) - sum(outcomes)}/{used} trials"
                        ),
                    },
                )
            )
            continue

        if metric.kind is MetricKind.MEASURED:
            baseline_sample = baseline_values(baseline or {}, name)
            if not baseline_sample and metric.limit is None:
                if policy.source_format != "v2":
                    continue
                raise ValueError(
                    f"metrics.{name} declares a tolerance but no baseline sample is bound"
                )
            observed = aggregate_value(values, metric.aggregate)
            reference = (
                aggregate_value(baseline_sample, metric.aggregate)
                if baseline_sample
                else None
            )
            lower, upper = _measured_bounds(metric, reference)
            passed = (lower is None or observed >= lower) and (
                upper is None or observed <= upper
            )
            results.append(
                _direct_result(
                    name=name,
                    kind=metric.kind,
                    verdict=GateVerdict.PASS if passed else GateVerdict.FAIL,
                    rule="tolerance",
                    trials_used=used,
                    trials_budgeted=trials_budgeted,
                    direction=metric.direction,
                    mode=MetricMode.GATING,
                    stopping_rule=stopping_rule,
                    evidence={
                        "aggregate": metric.aggregate,
                        "observed": observed,
                        "baseline": reference,
                        "allowed": {"lower": lower, "upper": upper},
                        "sample": {
                            "min": min(values),
                            "median": float(median(values)),
                            "max": max(values),
                        },
                        "delta": None if reference is None else observed - reference,
                    },
                )
            )
            continue

        if metric.kind is MetricKind.DISTRIBUTIONAL:
            baseline_sample = baseline_values(baseline or {}, name)
            if not baseline_sample:
                raise ValueError(
                    f"metrics.{name} requires a baseline trial sample; "
                    "run `maida baseline --from-report REPORT.json`"
                )
            required = distributional_minimum_baseline_trials(metric.coverage)
            mode = _resolve_mode(
                metric,
                policy_trials=trials_budgeted,
                baseline_trials=len(baseline_sample),
            )
            if metric.mode is MetricMode.GATING and len(baseline_sample) < required:
                raise ValueError(
                    f"metrics.{name} cannot gate at coverage={metric.coverage:g}: "
                    f"n_min={required} baseline trials, configured baseline has "
                    f"{len(baseline_sample)}. Re-capture with `maida run "
                    f"AGENT.py --trials {required} --no-fail-fast` followed by "
                    "`maida baseline --from-report REPORT.json`, or set "
                    "mode: report_only."
                )
            bound = (
                max(baseline_sample)
                if metric.direction is MetricDirection.UPPER
                else min(baseline_sample)
            )
            harmful = [
                value > bound
                if metric.direction is MetricDirection.UPPER
                else value < bound
                for value in values
            ]
            if used == 1:
                verdict = None
                if mode is MetricMode.GATING:
                    verdict = GateVerdict.FAIL if harmful[0] else GateVerdict.PASS
                results.append(
                    _direct_result(
                        name=name,
                        kind=metric.kind,
                        verdict=verdict,
                        rule="tolerance",
                        trials_used=used,
                        trials_budgeted=trials_budgeted,
                        direction=metric.direction,
                        mode=mode,
                        stopping_rule=stopping_rule,
                        outcomes=[not harmful[0]],
                        evidence={
                            "coverage": metric.coverage,
                            "baseline_trials": len(baseline_sample),
                            "baseline_n_min": required,
                            "prediction_bound": bound,
                            "observed": values[0],
                            "delta": values[0] - bound,
                        },
                    )
                )
            else:
                rate_result = aggregate_outcomes(
                    name,
                    harmful,
                    confidence_level=metric.confidence,
                    pass_rate_threshold=1.0 - metric.coverage,
                    direction=MetricDirection.UPPER,
                    mode=mode,
                    trials_budgeted=trials_budgeted,
                    stopping_rule=stopping_rule,
                    kind=metric.kind.value,
                )
                evidence = dict(rate_result.evidence)
                evidence.update(
                    {
                        "coverage": metric.coverage,
                        "baseline_trials": len(baseline_sample),
                        "baseline_n_min": required,
                        "prediction_bound": bound,
                        "values": values,
                        "harmful_exceedances": sum(harmful),
                    }
                )
                results.append(
                    replace(
                        rate_result,
                        direction=metric.direction.value,
                        evidence=evidence,
                    )
                )
            continue

        invariant_successes = [
            process_ok and all(outcomes.values())
            for process_ok, outcomes in zip(process_outcomes, trial_invariants)
        ]
        mode = _resolve_mode(metric, policy_trials=trials_budgeted)
        results.append(
            aggregate_outcomes(
                name,
                invariant_successes,
                confidence_level=metric.confidence,
                pass_rate_threshold=metric.threshold,
                direction=metric.direction,
                mode=mode,
                trials_budgeted=trials_budgeted,
                stopping_rule=stopping_rule,
                kind=metric.kind.value,
            )
        )
    return results
