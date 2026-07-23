"""Statistical aggregation for repeated binary gate outcomes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from statistics import NormalDist
from typing import Any, Iterable


class GateVerdict(str, Enum):
    """A merge-gate result that distinguishes uncertainty from failure."""

    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True)
class StatisticalResult:
    """Wilson-bound verdict and the complete rationale needed to reproduce it."""

    check_name: str
    verdict: GateVerdict
    trials: int
    successes: int
    pass_rate: float
    confidence_interval: tuple[float, float]
    confidence_level: float
    pass_rate_threshold: float
    decision_rule: str
    trial_outcomes: tuple[bool, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_name": self.check_name,
            "verdict": self.verdict.value,
            "trials": self.trials,
            "successes": self.successes,
            "pass_rate": self.pass_rate,
            "confidence_interval": list(self.confidence_interval),
            "confidence_level": self.confidence_level,
            "pass_rate_threshold": self.pass_rate_threshold,
            "decision_rule": self.decision_rule,
            "trial_outcomes": list(self.trial_outcomes),
        }


def wilson_interval(
    successes: int, trials: int, *, confidence_level: float = 0.95
) -> tuple[float, float]:
    """Return a two-sided Wilson score interval for a binomial proportion."""
    if trials < 1:
        raise ValueError("trials must be at least 1")
    if successes < 0 or successes > trials:
        raise ValueError("successes must be between 0 and trials")
    if not 0.0 < confidence_level < 1.0:
        raise ValueError("confidence_level must be between 0 and 1")

    alpha = 1.0 - confidence_level
    z = NormalDist().inv_cdf(1.0 - alpha / 2.0)
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


def aggregate_outcomes(
    check_name: str,
    outcomes: Iterable[bool],
    *,
    confidence_level: float = 0.95,
    pass_rate_threshold: float = 0.90,
) -> StatisticalResult:
    """Aggregate binary outcomes into PASS, FAIL, or INCONCLUSIVE."""
    values = tuple(bool(value) for value in outcomes)
    if not values:
        raise ValueError("at least one trial outcome is required")
    if not 0.0 <= pass_rate_threshold <= 1.0:
        raise ValueError("pass_rate_threshold must be between 0 and 1")

    trials = len(values)
    successes = sum(values)
    interval = wilson_interval(successes, trials, confidence_level=confidence_level)
    decision_rule = "wilson_two_sided"

    if trials == 1:
        verdict = GateVerdict.PASS if successes else GateVerdict.FAIL
        decision_rule = "single_trial_binary"
    elif trials == 3 and successes in {0, 3}:
        verdict = GateVerdict.PASS if successes == 3 else GateVerdict.FAIL
        decision_rule = "small_n_unanimous"
    elif interval[0] >= pass_rate_threshold:
        verdict = GateVerdict.PASS
    elif interval[1] < pass_rate_threshold:
        verdict = GateVerdict.FAIL
    else:
        verdict = GateVerdict.INCONCLUSIVE

    return StatisticalResult(
        check_name=check_name,
        verdict=verdict,
        trials=trials,
        successes=successes,
        pass_rate=successes / trials,
        confidence_interval=interval,
        confidence_level=confidence_level,
        pass_rate_threshold=pass_rate_threshold,
        decision_rule=decision_rule,
        trial_outcomes=values,
    )


def aggregate_verdict(results: Iterable[StatisticalResult]) -> GateVerdict:
    """Return FAIL over INCONCLUSIVE over PASS for a collection of checks."""
    verdicts = {result.verdict for result in results}
    if GateVerdict.FAIL in verdicts:
        return GateVerdict.FAIL
    if GateVerdict.INCONCLUSIVE in verdicts:
        return GateVerdict.INCONCLUSIVE
    return GateVerdict.PASS
