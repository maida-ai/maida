"""Regression coverage for directional, tier-aware issue #187 behavior."""

from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from maida.assertions import AssertionPolicy
from maida.baseline_bind import validate_policy_against_baseline
from maida.gate import aggregate_metrics
from maida.policy import PolicyDeprecationWarning, load_policy, minimum_trials_for_pass
from maida.runner import TrialRunReport
from maida.policy_types import (
    MetricDirection,
    MetricKind,
    MetricMode,
    MetricPolicy,
)
from maida.statistics import GateVerdict, aggregate_verdict


def _write_policy(tmp_path: Path, text: str) -> AssertionPolicy:
    path = tmp_path / "policy.yaml"
    path.write_text(text, encoding="utf-8")
    return load_policy(path)


@pytest.mark.parametrize(
    ("threshold", "expected"),
    [(0.90, 25), (0.80, 11), (0.70, 7)],
)
def test_one_sided_minimum_trials_matches_documented_values(
    threshold: float, expected: int
) -> None:
    assert minimum_trials_for_pass(threshold, 0.95, MetricDirection.LOWER) == expected


def test_policy_rejects_unreachable_gating_statistical_config(
    tmp_path: Path,
) -> None:
    with pytest.raises(
        ValueError,
        match=(
            r"threshold=0.9.*confidence=0.95.*n_min=25.*"
            r"configured trials=3.*Raise trials.*mode: report_only"
        ),
    ):
        _write_policy(
            tmp_path,
            """
version: 2
trials: 3
metrics:
  task_pass_rate:
    kind: statistical
    direction: lower
    threshold: 0.90
    confidence: 0.95
    mode: gating
""",
        )


def test_policy_requires_direction_and_rejects_patch_version(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="direction"):
        _write_policy(
            tmp_path,
            """
version: 2
metrics:
  step_count:
    kind: measured
    limit: 10
""",
        )
    with pytest.raises(ValueError, match="patch versions are invalid"):
        _write_policy(
            tmp_path,
            "version: 2.0.0\nmetrics: {}\n",
        )


def test_v1_migration_is_visible_and_infers_known_direction(
    tmp_path: Path,
) -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        policy = _write_policy(
            tmp_path,
            "assert:\n  max_steps: 12\n  no_loops: true\n",
        )
    assert any(issubclass(item.category, PolicyDeprecationWarning) for item in caught)
    assert policy.metrics["step_count"].direction is MetricDirection.UPPER
    assert policy.metrics["no_loops"].kind is MetricKind.INVARIANT


def test_step_count_improvement_from_12_to_5_is_not_a_failure_and_is_reported() -> None:
    """Named #12 regression: an upper-direction improvement never blocks."""
    metric = MetricPolicy(
        name="step_count",
        kind=MetricKind.MEASURED,
        direction=MetricDirection.UPPER,
        tolerance_relative=0.0,
    )
    policy = AssertionPolicy(
        trials=1,
        source_format="v2",
        policy_version=(2, 0),
        metrics={"step_count": metric},
    )
    results = aggregate_metrics(
        policy=policy,
        trial_values=[{"step_count": 5.0}],
        trial_invariants=[{}],
        process_outcomes=[True],
        baseline={"trial_sample": {"metrics": {"step_count": [12.0]}}},
        trials_budgeted=1,
        stopping_rule="fixed_n",
    )
    step = next(result for result in results if result.check_name == "step_count")
    assert step.verdict is GateVerdict.PASS
    assert step.evidence["delta"] == -7.0
    markdown = TrialRunReport(
        trials_requested=1, aggregate_results=results
    ).to_markdown()
    assert "delta -7" in markdown


def test_invariant_violation_at_n1_is_a_failure() -> None:
    metric = MetricPolicy(
        name="stop_condition_reached",
        kind=MetricKind.INVARIANT,
        require=True,
        aggregate="",
    )
    policy = AssertionPolicy(
        trials=1,
        source_format="v2",
        policy_version=(2, 0),
        metrics={"stop_condition_reached": metric},
    )
    results = aggregate_metrics(
        policy=policy,
        trial_values=[{}],
        trial_invariants=[{"stop_condition_reached": False}],
        process_outcomes=[True],
        baseline=None,
        trials_budgeted=1,
        stopping_rule="fixed_n",
    )
    assert aggregate_verdict(results) is GateVerdict.FAIL


def test_statistical_report_only_promotes_at_minimum_trial_boundary() -> None:
    metric = MetricPolicy(
        name="task_pass_rate",
        kind=MetricKind.STATISTICAL,
        direction=MetricDirection.LOWER,
        threshold=0.90,
        confidence=0.95,
        mode=None,
        success_predicate="all_invariants_passed",
        aggregate="",
    )
    for trials, expected_mode, expected_verdict in [
        (24, MetricMode.REPORT_ONLY.value, None),
        (25, MetricMode.GATING.value, GateVerdict.PASS),
    ]:
        policy = AssertionPolicy(
            trials=trials,
            source_format="v2",
            policy_version=(2, 0),
            metrics={"task_pass_rate": metric},
        )
        results = aggregate_metrics(
            policy=policy,
            trial_values=[{} for _ in range(trials)],
            trial_invariants=[{} for _ in range(trials)],
            process_outcomes=[True] * trials,
            baseline=None,
            trials_budgeted=trials,
            stopping_rule="fixed_n",
        )
        task = next(
            result for result in results if result.check_name == "task_pass_rate"
        )
        assert task.mode == expected_mode
        assert task.verdict is expected_verdict


def test_distributional_gateability_is_earned_by_baseline_sample() -> None:
    metric = MetricPolicy(
        name="step_count",
        kind=MetricKind.DISTRIBUTIONAL,
        direction=MetricDirection.UPPER,
        coverage=0.95,
        confidence=0.95,
        mode=MetricMode.GATING,
        aggregate="",
    )
    policy = AssertionPolicy(
        trials=1,
        source_format="v2",
        policy_version=(2, 0),
        metrics={"step_count": metric},
    )
    with pytest.raises(ValueError, match=r"n_min=19 baseline trials.*baseline has 18"):
        validate_policy_against_baseline(
            policy,
            {"trial_sample": {"metrics": {"step_count": [10] * 18}}},
        )
    validate_policy_against_baseline(
        policy,
        {"trial_sample": {"metrics": {"step_count": [10] * 19}}},
    )
