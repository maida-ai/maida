"""Tests for Wilson-bound three-verdict aggregation."""

from __future__ import annotations

import math

import pytest

from maida.statistics import (
    GateVerdict,
    aggregate_outcomes,
    aggregate_verdict,
    wilson_interval,
)


@pytest.mark.parametrize(
    ("successes", "trials", "expected"),
    [
        (0, 1, GateVerdict.FAIL),
        (1, 1, GateVerdict.PASS),
        (0, 3, GateVerdict.FAIL),
        (3, 3, GateVerdict.PASS),
        (2, 3, GateVerdict.INCONCLUSIVE),
        (0, 5, GateVerdict.FAIL),
        (4, 5, GateVerdict.INCONCLUSIVE),
        (0, 10, GateVerdict.FAIL),
        (10, 10, GateVerdict.INCONCLUSIVE),
    ],
)
def test_default_verdicts_cover_supported_small_trial_counts(
    successes: int, trials: int, expected: GateVerdict
) -> None:
    result = aggregate_outcomes(
        "check", [True] * successes + [False] * (trials - successes)
    )

    assert result.verdict is expected
    assert result.trials == trials
    assert result.successes == successes


def test_wilson_pass_when_lower_bound_meets_threshold() -> None:
    result = aggregate_outcomes(
        "check", [True] * 10, pass_rate_threshold=0.70, confidence_level=0.95
    )

    assert result.verdict is GateVerdict.PASS
    assert result.confidence_interval[0] >= 0.70


def test_wilson_fail_when_upper_bound_is_below_threshold() -> None:
    result = aggregate_outcomes("check", [False] * 9 + [True], pass_rate_threshold=0.90)

    assert result.verdict is GateVerdict.FAIL
    assert result.confidence_interval[1] < 0.90


def test_wilson_inconclusive_when_interval_straddles_threshold() -> None:
    result = aggregate_outcomes("check", [True] * 4 + [False], pass_rate_threshold=0.75)

    lower, upper = result.confidence_interval
    assert result.verdict is GateVerdict.INCONCLUSIVE
    assert lower < 0.75 <= upper


def test_wilson_interval_matches_reference_value() -> None:
    lower, upper = wilson_interval(3, 3, confidence_level=0.95)

    assert lower == pytest.approx(0.4385, abs=0.0001)
    assert upper == pytest.approx(1.0)


def test_result_serializes_verdict_rationale() -> None:
    result = aggregate_outcomes("step_count", [True, True, False])
    payload = result.to_dict()

    assert payload == {
        "check_name": "step_count",
        "verdict": "inconclusive",
        "trials": 3,
        "successes": 2,
        "pass_rate": pytest.approx(2 / 3),
        "confidence_interval": pytest.approx([0.2076596008, 0.9385080553]),
        "confidence_level": 0.95,
        "pass_rate_threshold": 0.9,
        "decision_rule": "wilson_two_sided",
        "trial_outcomes": [True, True, False],
    }


@pytest.mark.parametrize(
    ("successes", "trials", "threshold", "confidence"),
    [(-1, 3, 0.9, 0.95), (4, 3, 0.9, 0.95), (1, 0, 0.9, 0.95)],
)
def test_wilson_interval_rejects_invalid_counts(
    successes: int, trials: int, threshold: float, confidence: float
) -> None:
    with pytest.raises(ValueError):
        wilson_interval(successes, trials, confidence_level=confidence)


def test_wilson_interval_is_finite() -> None:
    lower, upper = wilson_interval(5, 10, confidence_level=0.95)
    assert math.isfinite(lower)
    assert math.isfinite(upper)


def test_aggregate_verdict_prioritizes_fail_then_inconclusive() -> None:
    passed = aggregate_outcomes("passed", [True])
    failed = aggregate_outcomes("failed", [False])
    inconclusive = aggregate_outcomes("uncertain", [True, True, False])

    assert aggregate_verdict([passed, inconclusive]) is GateVerdict.INCONCLUSIVE
    assert aggregate_verdict([passed, inconclusive, failed]) is GateVerdict.FAIL
