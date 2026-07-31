"""Deterministic, zero-token calibration for the one-sided decision rule."""

from __future__ import annotations

import random
from dataclasses import dataclass

from maida.policy import minimum_trials_for_pass
from maida.policy_types import MetricDirection
from maida.statistics import GateVerdict, aggregate_outcomes


TRIAL_BUDGETS = (1, 3, 5, 7, 11, 25)
THRESHOLDS = (0.70, 0.80, 0.90)
TRUE_RATES = (0.99, 0.95, 0.90, 0.85, 0.70)


@dataclass(frozen=True)
class CalibrationCell:
    trials: int
    threshold: float
    true_rate: float
    replications: int
    status: str
    false_fail_rate: float | None
    inconclusive_rate: float | None
    missed_regression_rate: float | None

    def to_dict(self) -> dict[str, int | float | str | None]:
        return {
            "trials": self.trials,
            "threshold": self.threshold,
            "true_rate": self.true_rate,
            "replications": self.replications,
            "status": self.status,
            "false_fail_rate": self.false_fail_rate,
            "inconclusive_rate": self.inconclusive_rate,
            "missed_regression_rate": self.missed_regression_rate,
        }


def calibrate_decision_rule(
    *,
    replications: int = 10_000,
    seed: int = 187,
) -> list[CalibrationCell]:
    """Run the published seeded Bernoulli grid without model calls."""
    if replications < 1:
        raise ValueError("replications must be at least 1")
    cells: list[CalibrationCell] = []
    for trials in TRIAL_BUDGETS:
        for threshold in THRESHOLDS:
            n_min = minimum_trials_for_pass(threshold, 0.95, MetricDirection.LOWER)
            for true_rate in TRUE_RATES:
                if trials < n_min:
                    cells.append(
                        CalibrationCell(
                            trials=trials,
                            threshold=threshold,
                            true_rate=true_rate,
                            replications=replications,
                            status=f"load rejected (n_min={n_min})",
                            false_fail_rate=None,
                            inconclusive_rate=None,
                            missed_regression_rate=None,
                        )
                    )
                    continue

                rng = random.Random(f"{seed}:{trials}:{threshold:.2f}:{true_rate:.2f}")
                verdict_by_successes = {}
                for successes in range(trials + 1):
                    verdict_by_successes[successes] = aggregate_outcomes(
                        "task_pass_rate",
                        [True] * successes + [False] * (trials - successes),
                        confidence_level=0.95,
                        pass_rate_threshold=threshold,
                        direction=MetricDirection.LOWER,
                    ).verdict
                verdicts = []
                for _ in range(replications):
                    successes = sum(rng.random() < true_rate for _ in range(trials))
                    verdicts.append(verdict_by_successes[successes])
                inconclusive = verdicts.count(GateVerdict.INCONCLUSIVE)
                false_fail = (
                    verdicts.count(GateVerdict.FAIL) if true_rate >= threshold else None
                )
                missed = (
                    sum(verdict is not GateVerdict.FAIL for verdict in verdicts)
                    if true_rate < threshold
                    else None
                )
                cells.append(
                    CalibrationCell(
                        trials=trials,
                        threshold=threshold,
                        true_rate=true_rate,
                        replications=replications,
                        status="measured",
                        false_fail_rate=(
                            false_fail / replications
                            if false_fail is not None
                            else None
                        ),
                        inconclusive_rate=inconclusive / replications,
                        missed_regression_rate=(
                            missed / replications if missed is not None else None
                        ),
                    )
                )
    return cells


def render_calibration_markdown(cells: list[CalibrationCell]) -> str:
    """Render the full reproducible grid as a documentation table."""

    def rate(value: float | None) -> str:
        return "—" if value is None else f"{value:.2%}"

    lines = [
        "| N | θ | True pass rate | Status | False-fail | Inconclusive | Missed regression |",
        "| ---: | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for cell in cells:
        lines.append(
            f"| {cell.trials} | {cell.threshold:.2f} | {cell.true_rate:.2f} | "
            f"{cell.status} | {rate(cell.false_fail_rate)} | "
            f"{rate(cell.inconclusive_rate)} | "
            f"{rate(cell.missed_regression_rate)} |"
        )
    return "\n".join(lines)
