"""Directional aggregation for tier-aware fixed-budget gate outcomes."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from statistics import NormalDist
from typing import Any, Iterable

from maida.policy_types import MetricDirection, MetricMode


class GateVerdict(str, Enum):
    """A merge-gate result that distinguishes uncertainty from failure."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class StatisticalResult:
    """One normalized tier result and the evidence needed to reproduce it."""

    check_name: str
    kind: str
    verdict: GateVerdict | None
    decision_rule: str
    trials_used: int
    trials_budgeted: int
    direction: str | None = None
    mode: str = MetricMode.GATING.value
    stopping_rule: str = "fixed_n"
    trial_outcomes: tuple[bool, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def trials(self) -> int:
        return self.trials_used

    @property
    def successes(self) -> int:
        return sum(self.trial_outcomes)

    @property
    def pass_rate(self) -> float:
        return self.successes / self.trials_used if self.trials_used else 0.0

    @property
    def confidence_interval(self) -> tuple[float, float]:
        bounds = self.evidence.get("confidence_bounds") or {}
        return float(bounds.get("lower", 0.0)), float(bounds.get("upper", 1.0))

    @property
    def confidence_level(self) -> float:
        return float(self.evidence.get("confidence", 0.0))

    @property
    def pass_rate_threshold(self) -> float:
        threshold = self.evidence.get("threshold", 0.0)
        return float(threshold) if not isinstance(threshold, dict) else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "kind": self.kind,
            "direction": self.direction,
            "mode": self.mode,
            "verdict": self.verdict.value if self.verdict is not None else None,
            "decision_rule": self.decision_rule,
            "stopping_rule": self.stopping_rule,
            "trials_used": self.trials_used,
            "trials_budgeted": self.trials_budgeted,
            "trial_outcomes": list(self.trial_outcomes),
            "evidence": self.evidence,
        }


def _validate_counts(successes: int, trials: int, confidence: float) -> None:
    if trials < 1:
        raise ValueError("trials must be at least 1")
    if successes < 0 or successes > trials:
        raise ValueError("successes must be between 0 and trials")
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be greater than 0 and less than 1")


def wilson_one_sided_bounds(
    successes: int, trials: int, *, confidence: float = 0.95
) -> tuple[float, float]:
    """Return separate lower/upper Wilson bounds, each with one-sided coverage."""
    _validate_counts(successes, trials, confidence)
    z = NormalDist().inv_cdf(confidence)
    proportion = successes / trials
    z_squared = z * z
    denominator = 1.0 + z_squared / trials
    center = (proportion + z_squared / (2.0 * trials)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / trials
            + z_squared / (4.0 * trials * trials)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def wilson_interval(
    successes: int, trials: int, *, confidence_level: float = 0.95
) -> tuple[float, float]:
    """Compatibility alias for callers migrating to one-sided Wilson bounds."""
    return wilson_one_sided_bounds(successes, trials, confidence=confidence_level)


def _directional_verdict(
    observed_rate: float,
    lower_bound: float,
    upper_bound: float,
    threshold: float | tuple[float, float],
    direction: MetricDirection,
) -> GateVerdict:
    del observed_rate  # Bounds, not points, earn both PASS and FAIL.
    if direction is MetricDirection.LOWER:
        theta = float(threshold)
        if lower_bound >= theta:
            return GateVerdict.PASS
        if upper_bound < theta:
            return GateVerdict.FAIL
        return GateVerdict.INCONCLUSIVE
    if direction is MetricDirection.UPPER:
        theta = float(threshold)
        if upper_bound <= theta:
            return GateVerdict.PASS
        if lower_bound > theta:
            return GateVerdict.FAIL
        return GateVerdict.INCONCLUSIVE

    lower_threshold, upper_threshold = threshold
    if lower_bound >= lower_threshold and upper_bound <= upper_threshold:
        return GateVerdict.PASS
    if upper_bound < lower_threshold or lower_bound > upper_threshold:
        return GateVerdict.FAIL
    return GateVerdict.INCONCLUSIVE


def aggregate_outcomes(
    check_name: str,
    outcomes: Iterable[bool],
    *,
    confidence_level: float = 0.95,
    pass_rate_threshold: float | tuple[float, float] = 0.90,
    direction: MetricDirection | str = MetricDirection.LOWER,
    mode: MetricMode | str = MetricMode.GATING,
    trials_budgeted: int | None = None,
    stopping_rule: str = "fixed_n",
    kind: str = "statistical",
) -> StatisticalResult:
    """Aggregate Bernoulli outcomes with confidence earned in both directions."""
    values = tuple(bool(value) for value in outcomes)
    if not values:
        raise ValueError("at least one trial outcome is required")
    parsed_direction = MetricDirection(direction)
    parsed_mode = MetricMode(mode)
    successes = sum(values)
    trials = len(values)
    lower, upper = wilson_one_sided_bounds(
        successes, trials, confidence=confidence_level
    )
    verdict = None
    decision_rule = "report_only"
    if parsed_mode is MetricMode.GATING:
        verdict = _directional_verdict(
            successes / trials,
            lower,
            upper,
            pass_rate_threshold,
            parsed_direction,
        )
        decision_rule = "wilson_one_sided"
    threshold_json: float | dict[str, float]
    if isinstance(pass_rate_threshold, tuple):
        threshold_json = {
            "lower": pass_rate_threshold[0],
            "upper": pass_rate_threshold[1],
        }
    else:
        threshold_json = float(pass_rate_threshold)
    return StatisticalResult(
        check_name=check_name,
        kind=kind,
        verdict=verdict,
        direction=parsed_direction.value,
        mode=parsed_mode.value,
        decision_rule=decision_rule,
        trials_used=trials,
        trials_budgeted=trials_budgeted or trials,
        stopping_rule=stopping_rule,
        trial_outcomes=values,
        evidence={
            "successes": successes,
            "failures": trials - successes,
            "observed_rate": successes / trials,
            "threshold": threshold_json,
            "confidence": confidence_level,
            "confidence_bounds": {"lower": lower, "upper": upper},
        },
    )


def aggregate_verdict(results: Iterable[StatisticalResult]) -> GateVerdict:
    """Return FAIL over INCONCLUSIVE over PASS, ignoring report-only rows."""
    verdicts = {result.verdict for result in results if result.verdict is not None}
    if GateVerdict.FAIL in verdicts:
        return GateVerdict.FAIL
    if GateVerdict.INCONCLUSIVE in verdicts:
        return GateVerdict.INCONCLUSIVE
    return GateVerdict.PASS


def distributional_minimum_baseline_trials(coverage: float) -> int:
    """Minimum one-sided order-statistic sample for requested coverage."""
    if not 0.0 < coverage < 1.0:
        raise ValueError("coverage must be greater than 0 and less than 1")
    return max(1, math.ceil(1.0 / (1.0 - coverage)) - 1)
