"""Tests for the zero-token statistical gate burn-in harness."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from maida.burn_in import BurnInReport, run_burn_in, summarize_verdicts
from maida.statistics import GateVerdict


def test_summarize_fifty_gates_reports_rates_and_acceptance() -> None:
    report = summarize_verdicts(
        [GateVerdict.PASS] * 47 + [GateVerdict.INCONCLUSIVE] * 3,
        trials_per_gate=3,
        seed=137,
        pass_probability=0.99,
    )

    assert report.gates == 50
    assert report.false_fail_rate == 0.0
    assert report.inconclusive_rate == 0.06
    assert report.acceptance_met is True
    assert report.model_calls == 0


def test_false_fail_threshold_is_strictly_less_than_two_percent() -> None:
    report = summarize_verdicts(
        [GateVerdict.PASS] * 49 + [GateVerdict.FAIL],
        trials_per_gate=3,
        seed=137,
        pass_probability=0.99,
    )

    assert report.false_fail_rate == 0.02
    assert report.acceptance_met is False


def test_inconclusive_threshold_is_strictly_less_than_fifteen_percent() -> None:
    report = summarize_verdicts(
        [GateVerdict.PASS] * 17 + [GateVerdict.INCONCLUSIVE] * 3,
        trials_per_gate=3,
        seed=137,
        pass_probability=0.99,
    )

    assert report.inconclusive_rate == 0.15
    assert report.acceptance_met is False


def test_report_json_is_machine_readable() -> None:
    report = BurnInReport(
        gates=2,
        trials_per_gate=3,
        seed=7,
        pass_probability=0.99,
        verdicts=(GateVerdict.PASS, GateVerdict.INCONCLUSIVE),
        elapsed_seconds=0.5,
    )

    payload = json.loads(report.to_json())
    assert payload["false_fail_rate"] == 0.0
    assert payload["inconclusive_rate"] == 0.5
    assert payload["model_calls"] == 0
    assert payload["acceptance_met"] is False


def test_full_harness_runs_fixed_agent_without_changing_repo(tmp_path) -> None:
    report = run_burn_in(
        gates=2,
        trials_per_gate=3,
        seed=137,
        pass_probability=1.0,
        max_wall_time_seconds=30,
        workspace_parent=tmp_path,
    )

    assert report.verdicts == (GateVerdict.PASS, GateVerdict.PASS)
    assert report.acceptance_met is True
    assert report.model_calls == 0


def test_full_harness_enforces_wall_time_cap(tmp_path) -> None:
    with pytest.raises(TimeoutError, match="wall-time cap"):
        run_burn_in(
            gates=2,
            trials_per_gate=3,
            max_wall_time_seconds=0.000001,
            workspace_parent=tmp_path,
        )


def test_nightly_workflow_is_opt_in_but_manual_dispatch_is_available() -> None:
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "statistical-burn-in.yml"
    ).read_text(encoding="utf-8")

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "vars.MAIDA_BURN_IN_ENABLED == 'true'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "timeout-minutes: 15" in workflow
