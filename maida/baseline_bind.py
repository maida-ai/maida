"""Validation that becomes decidable only when policy and baseline are bound."""

from __future__ import annotations

from typing import Any

from maida.assertions import AssertionPolicy
from maida.gate import baseline_values
from maida.policy_types import MetricKind, MetricMode
from maida.statistics import distributional_minimum_baseline_trials


def validate_policy_against_baseline(
    policy: AssertionPolicy, baseline: dict[str, Any] | None
) -> None:
    """Reject baseline-dependent configurations before agent execution."""
    for name, metric in policy.metrics.items():
        if metric.kind is MetricKind.MEASURED:
            if (
                policy.source_format == "v2"
                and metric.limit is None
                and not baseline_values(baseline or {}, name)
            ):
                raise ValueError(
                    f"metrics.{name} declares a tolerance but no baseline sample is bound"
                )
        elif metric.kind is MetricKind.DISTRIBUTIONAL:
            sample = baseline_values(baseline or {}, name)
            if not sample:
                raise ValueError(
                    f"metrics.{name} requires a baseline trial sample; "
                    "run `maida baseline --from-report REPORT.json`"
                )
            required = distributional_minimum_baseline_trials(metric.coverage)
            if metric.mode is MetricMode.GATING and len(sample) < required:
                raise ValueError(
                    f"metrics.{name} cannot gate at coverage={metric.coverage:g}: "
                    f"n_min={required} baseline trials, configured baseline has "
                    f"{len(sample)}. Re-capture with `maida run AGENT.py --trials "
                    f"{required} --no-fail-fast` followed by `maida baseline "
                    "--from-report REPORT.json`, or set mode: report_only."
                )
