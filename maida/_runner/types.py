"""Trial records and aggregate run reports."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from maida.assertions import AssertionReport
from maida.plan_contract import PlanEvidence
from maida.schema_versions import REPORT_SCHEMA_VERSION
from maida.statistics import GateVerdict, StatisticalResult, aggregate_verdict

REPORT_VERSION = REPORT_SCHEMA_VERSION


class RunExecutionError(RuntimeError):
    """The agent process could not produce an unambiguous completed trace."""


@dataclass(frozen=True)
class TrialRecord:
    """One sampled agent execution and its raw tier evidence."""

    trial: int
    trace_id: str
    run_name: str | None
    process_exit_code: int | None
    stdout: str
    stderr: str
    assertion_report: AssertionReport
    run_status: str | None = None
    baseline_diff: dict[str, Any] | None = None
    metric_values: dict[str, float] = field(default_factory=dict)
    invariant_outcomes: dict[str, bool] = field(default_factory=dict)
    structural_signature: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        process_succeeded = (
            self.process_exit_code == 0 if self.process_exit_code is not None else self.run_status == "ok"
        )
        return process_succeeded and self.assertion_report.passed and all(self.invariant_outcomes.values())

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "trial": self.trial,
            "trace_id": self.trace_id,
            "run_name": self.run_name,
            "process_exit_code": self.process_exit_code,
            "passed": self.passed,
            "checks": [
                {
                    "check_name": result.check_name,
                    "passed": result.passed,
                    "reason_code": str(getattr(result.reason_code, "value", result.reason_code)),
                    "message": result.message,
                    "expected": result.expected,
                    "actual": result.actual,
                    "ignored": result.ignored,
                }
                for result in self.assertion_report.results
            ],
            "metric_values": self.metric_values,
            "invariant_outcomes": self.invariant_outcomes,
            "structural_signature": self.structural_signature,
            "baseline_diff": self.baseline_diff,
        }
        if self.run_status is not None:
            payload["run_status"] = self.run_status
        return payload


@dataclass(frozen=True)
class TrialRunReport:
    """Collected evidence and verdicts for a fixed execution sample."""

    trials_requested: int
    trials: list[TrialRecord] = field(default_factory=list)
    aggregate_results: list[StatisticalResult] = field(default_factory=list)
    confidence_level: float = 0.95
    pass_rate_threshold: float = 0.90
    stopping_rule: str = "fixed_n"
    abort_reason: str | None = None
    environment_fingerprint: dict[str, Any] = field(default_factory=dict)
    report_kind: str = "gate"
    agent_name: str | None = None
    window_input_format: str | None = None
    baseline_source_run_id: str | None = None
    baseline_source_run_name: str | None = None
    baseline_acceptance: dict[str, Any] | None = None
    plan_evidence: list[PlanEvidence] = field(default_factory=list)

    @property
    def verdict(self) -> GateVerdict:
        return aggregate_verdict(self.aggregate_results)

    @property
    def passed(self) -> bool | None:
        if self.verdict is GateVerdict.PASS:
            return True
        if self.verdict is GateVerdict.FAIL:
            return False
        return None

    def to_dict(self) -> dict[str, Any]:
        metadata = {
            "trials_used": len(self.trials),
            "trials_budgeted": self.trials_requested,
            "stopping_rule": self.stopping_rule,
            "abort_reason": self.abort_reason,
            "environment_fingerprint": self.environment_fingerprint,
        }
        payload = {
            "report_version": REPORT_VERSION,
            "trials_requested": self.trials_requested,
            "verdict": self.verdict.value,
            "passed": self.passed,
            "metadata": metadata,
            "trials": [trial.to_dict() for trial in self.trials],
            "aggregate_results": [result.to_dict() for result in self.aggregate_results],
        }
        if self.report_kind != "gate":
            payload["report_kind"] = self.report_kind
            metadata.update(
                {
                    "agent_name": self.agent_name,
                    "window_input_format": self.window_input_format,
                    "baseline_source_run_id": self.baseline_source_run_id,
                    "baseline_source_run_name": self.baseline_source_run_name,
                }
            )
        if self.baseline_acceptance is not None:
            payload["baseline_acceptance"] = self.baseline_acceptance
        if self.plan_evidence:
            payload["plan_evidence"] = [item.to_dict() for item in self.plan_evidence]
        return payload

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_text(self) -> str:
        label = "Window trace" if self.report_kind == "drift" else "Trial"
        lines = [
            (
                f"{label} {trial.trial}/{self.trials_requested}: "
                f"{'PASS' if trial.passed else 'FAIL'} "
                f"(trace {trial.trace_id[:8]})"
            )
            for trial in self.trials
        ]
        if self.abort_reason:
            lines.append(f"Stopped after {len(self.trials)}/{self.trials_requested}: {self.abort_reason}")
        lines.extend(["", f"RESULT: {self.verdict.value.upper()}"])
        return "\n".join(lines)

    def to_markdown(self, baseline_path: str | None = None) -> str:
        """Render the compact, GitHub-facing statistical gate report."""
        from maida._runner.markdown import render_trial_report_markdown

        return render_trial_report_markdown(self, baseline_path=baseline_path)
