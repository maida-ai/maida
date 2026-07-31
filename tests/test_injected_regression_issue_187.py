"""The real synthetic harness exposes an injected structural regression."""

from maida.burn_in import run_burn_in
from maida.statistics import GateVerdict


def test_injected_tool_regression_is_detected_at_fixed_n(tmp_path) -> None:
    report = run_burn_in(
        gates=1,
        trials_per_gate=3,
        seed=187,
        pass_probability=0.0,
        max_wall_time_seconds=30,
        workspace_parent=tmp_path,
    )
    assert report.verdicts == (GateVerdict.FAIL,)
    assert report.model_calls == 0
