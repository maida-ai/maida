"""Tests for the zero-token statistical gate burn-in harness."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from maida.burn_in import BurnInReport, run_burn_in, summarize_verdicts
from maida.statistics import GateVerdict


@pytest.mark.parametrize("probability, verdict", [(1.0, "pass"), (0.0, "fail")])
def test_script_completes_measurement_and_writes_report(tmp_path, probability, verdict):
    output = tmp_path / "reports" / "burn-in.json"
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/statistical_burn_in.py",
            "--gates",
            "1",
            "--trials",
            "1",
            "--pass-probability",
            str(probability),
            "--json-out",
            str(output),
        ],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert "Measurement complete (not an acceptance guarantee)" in completed.stdout
    payload = json.loads(output.read_text())
    assert payload["verdicts"] == [verdict]
    assert "acceptance_met" not in payload


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"pass_probability": -0.1}, "pass_probability"),
        ({"pass_probability": 1.1}, "pass_probability"),
        ({"pass_probability": float("nan")}, "pass_probability"),
        ({"trials_per_gate": 0}, "trials_per_gate"),
        ({"max_wall_time_seconds": float("nan")}, "max_wall_time_seconds"),
        ({"max_wall_time_seconds": float("inf")}, "max_wall_time_seconds"),
    ],
)
def test_invalid_configuration_rejected_before_execution(monkeypatch, kwargs, message):
    monkeypatch.setattr(
        "maida.burn_in.subprocess.run",
        lambda *args, **kw: pytest.fail("invalid configuration started a subprocess"),
    )
    with pytest.raises(ValueError, match=message):
        run_burn_in(gates=1, **kwargs)


def test_summarize_fifty_gates_reports_measurements() -> None:
    report = summarize_verdicts(
        [GateVerdict.PASS] * 47 + [GateVerdict.INCONCLUSIVE] * 3,
        trials_per_gate=3,
        seed=137,
        pass_probability=0.99,
    )

    assert report.gates == 50
    assert report.false_fail_rate == 0.0
    assert report.inconclusive_rate == 0.06
    assert report.model_calls == 0


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
    assert "acceptance_met" not in payload


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
    assert report.model_calls == 0


def test_full_harness_enforces_wall_time_cap(tmp_path) -> None:
    with pytest.raises(TimeoutError, match="wall-time cap"):
        run_burn_in(
            gates=2,
            trials_per_gate=3,
            max_wall_time_seconds=0.000001,
            workspace_parent=tmp_path,
        )


def test_wall_time_cap_interrupts_a_running_trial(tmp_path, monkeypatch):
    monkeypatch.setattr("maida.burn_in._SYNTHETIC_AGENT", "import time; time.sleep(2)")
    with pytest.raises(TimeoutError, match="wall-time cap"):
        run_burn_in(
            gates=1,
            trials_per_gate=1,
            max_wall_time_seconds=0.2,
            workspace_parent=tmp_path,
        )


def test_wall_time_cap_checks_the_final_gate(tmp_path, monkeypatch):
    from types import SimpleNamespace

    clock = [0.0]
    monkeypatch.setattr("maida.burn_in.time.monotonic", lambda: clock[0])

    def slow_gate(*args, **kwargs):
        clock[0] = 2.0
        return SimpleNamespace(verdict=GateVerdict.PASS)

    monkeypatch.setattr("maida.burn_in.run_trials", slow_gate)
    with pytest.raises(TimeoutError, match="wall-time cap"):
        run_burn_in(gates=1, max_wall_time_seconds=1, workspace_parent=tmp_path)


def test_nightly_workflow_is_opt_in_but_manual_dispatch_is_available() -> None:
    workflow = (Path(__file__).parents[1] / ".github" / "workflows" / "statistical-burn-in.yml").read_text(
        encoding="utf-8"
    )

    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    assert "vars.MAIDA_BURN_IN_ENABLED == 'true'" in workflow
    assert "github.event_name == 'workflow_dispatch'" in workflow
    assert "timeout-minutes: 15" in workflow
