"""Calibration is deterministic, offline, and documents rejected cells."""

from maida.calibration import calibrate_decision_rule


def test_calibration_grid_is_seeded_and_marks_unreachable_budgets() -> None:
    first = calibrate_decision_rule(replications=100, seed=187)
    second = calibrate_decision_rule(replications=100, seed=187)
    assert first == second
    assert len(first) == 6 * 3 * 5
    rejected = [cell for cell in first if cell.trials == 3 and cell.threshold == 0.90]
    assert rejected
    assert {cell.status for cell in rejected} == {"load rejected (n_min=25)"}
    assert all(cell.false_fail_rate is None for cell in rejected)
