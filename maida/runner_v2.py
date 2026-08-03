"""Repeated isolated execution with tier-aware fixed-budget aggregation."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from maida import __version__
from maida.assertions import (
    AssertionPolicy,
    AssertionReport,
    AssertionResult,
    run_assertions,
)
from maida.baseline import extract_run_metrics
from maida.baseline_bind import validate_policy_against_baseline
from maida.config import MaidaConfig
from maida.diff import compute_diff
from maida.gate import (
    aggregate_metrics,
    invariant_outcomes,
    numeric_metrics,
    structural_signature,
)
from maida.policy import merge_policy
from maida.statistics import GateVerdict, StatisticalResult, aggregate_verdict
from maida.schema_versions import REPORT_SCHEMA_VERSION
from maida.storage import load_run_for_analysis


REPORT_VERSION = REPORT_SCHEMA_VERSION


class RunExecutionError(RuntimeError):
    """The agent process could not produce an unambiguous completed trace."""


@dataclass(frozen=True)
class TrialRecord:
    """One isolated agent invocation and its raw tier evidence."""

    trial: int
    trace_id: str
    run_name: str | None
    process_exit_code: int
    stdout: str
    stderr: str
    assertion_report: AssertionReport
    baseline_diff: dict[str, Any] | None = None
    metric_values: dict[str, float] = field(default_factory=dict)
    invariant_outcomes: dict[str, bool] = field(default_factory=dict)
    structural_signature: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return (
            self.process_exit_code == 0
            and self.assertion_report.passed
            and all(self.invariant_outcomes.values())
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial": self.trial,
            "trace_id": self.trace_id,
            "run_name": self.run_name,
            "process_exit_code": self.process_exit_code,
            "passed": self.passed,
            "checks": [
                {
                    "check_name": result.check_name,
                    "passed": result.passed,
                    "reason_code": str(
                        getattr(result.reason_code, "value", result.reason_code)
                    ),
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


@dataclass(frozen=True)
class TrialRunReport:
    """Collected evidence and verdicts for a fixed trial budget."""

    trials_requested: int
    trials: list[TrialRecord] = field(default_factory=list)
    aggregate_results: list[StatisticalResult] = field(default_factory=list)
    confidence_level: float = 0.95
    pass_rate_threshold: float = 0.90
    stopping_rule: str = "fixed_n"
    abort_reason: str | None = None
    environment_fingerprint: dict[str, Any] = field(default_factory=dict)

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
        return {
            "report_version": REPORT_VERSION,
            "trials_requested": self.trials_requested,
            "verdict": self.verdict.value,
            "passed": self.passed,
            "metadata": {
                "trials_used": len(self.trials),
                "trials_budgeted": self.trials_requested,
                "stopping_rule": self.stopping_rule,
                "abort_reason": self.abort_reason,
                "environment_fingerprint": self.environment_fingerprint,
            },
            "trials": [trial.to_dict() for trial in self.trials],
            "aggregate_results": [
                result.to_dict() for result in self.aggregate_results
            ],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_text(self) -> str:
        lines = [
            (
                f"Trial {trial.trial}/{self.trials_requested}: "
                f"{'PASS' if trial.passed else 'FAIL'} "
                f"(trace {trial.trace_id[:8]})"
            )
            for trial in self.trials
        ]
        if self.abort_reason:
            lines.append(
                f"Stopped after {len(self.trials)}/{self.trials_requested}: "
                f"{self.abort_reason}"
            )
        lines.extend(["", f"RESULT: {self.verdict.value.upper()}"])
        return "\n".join(lines)

    def to_markdown(self) -> str:
        icons = {
            GateVerdict.PASS: "✅",
            GateVerdict.FAIL: "❌",
            GateVerdict.INCONCLUSIVE: "⚪",
        }
        lines = [
            f"## {icons[self.verdict]} Maida gate: {self.verdict.value}",
            "",
            f"**{len(self.trials)}/{self.trials_requested} trials used** · "
            f"stopping rule `{self.stopping_rule}`",
        ]
        if self.abort_reason:
            lines.append(f" · aborted: `{self.abort_reason}`")
        lines.extend(
            [
                "",
                "| Metric | Kind | Mode | Direction | Verdict | Evidence |",
                "| --- | --- | --- | --- | --- | --- |",
            ]
        )
        for result in self.aggregate_results:
            verdict = (
                result.verdict.value.upper()
                if result.verdict is not None
                else "REPORT ONLY"
            )
            evidence = result.evidence
            if result.kind == "measured":
                delta = evidence.get("delta")
                delta_text = "n/a" if delta is None else f"{delta:+.3g}"
                sample = evidence.get("sample") or {}
                detail = (
                    f"delta {delta_text}; min/median/max "
                    f"{sample.get('min')}/{sample.get('median')}/{sample.get('max')}"
                )
            elif result.kind == "invariant":
                detail = (
                    f"violated in {evidence.get('violations', 0)}/"
                    f"{result.trials_used} trials"
                )
            elif result.mode == "report_only":
                detail = (
                    f"observed rate {evidence.get('observed_rate', 0):.3f}; "
                    "no confidence verdict"
                )
            else:
                bounds = evidence.get("confidence_bounds") or {}
                detail = (
                    f"one-sided bounds "
                    f"{bounds.get('lower', 0):.3f}–{bounds.get('upper', 1):.3f}"
                )
            lines.append(
                f"| `{result.check_name}` | {result.kind} | {result.mode} | "
                f"{result.direction or 'n/a'} | **{verdict}** | {detail} |"
            )
        lines.extend(
            [
                "",
                "### Trial traces",
                "",
                "| Trial | Outcome | Trace | Process exit | Baseline changes |",
                "| ---: | --- | --- | ---: | --- |",
            ]
        )
        for trial in self.trials:
            diff = trial.baseline_diff
            changes = "not configured"
            if diff is not None:
                changed = list((diff.get("summary_diff") or {}).keys())
                changed.extend(
                    f"new tool: {tool}" for tool in diff.get("new_tools", [])
                )
                changes = ", ".join(changed) if changed else "none"
            lines.append(
                f"| {trial.trial} | {'PASS' if trial.passed else 'FAIL'} | "
                f"`{trial.trace_id[:8]}` | {trial.process_exit_code} | {changes} |"
            )
        if self.verdict is GateVerdict.INCONCLUSIVE:
            lines.extend(
                [
                    "",
                    "> Neutral result: no blocking failure was established. "
                    "The CLI exits 0.",
                ]
            )
        return "\n".join(lines)


def _workspace_files(project_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return [Path(item.decode()) for item in result.stdout.split(b"\0") if item]


def _copy_workspace(project_root: Path, destination: Path) -> None:
    for relative_path in _workspace_files(project_root):
        source = project_root / relative_path
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        elif source.is_file():
            shutil.copy2(source, target)


def _environment_fingerprint(project_root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    for relative in sorted(
        _workspace_files(project_root), key=lambda item: item.as_posix()
    ):
        path = project_root / relative
        if not path.is_file():
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "algorithm": "sha256",
        "workspace": digest.hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "maida": __version__,
    }


def _preserve_trace(trace_id: str, trial_data_dir: Path, config: MaidaConfig) -> None:
    source = trial_data_dir / "runs" / trace_id
    destination = config.data_dir.expanduser() / "runs" / trace_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RunExecutionError(f"Trace destination already exists: {trace_id}")
    shutil.copytree(source, destination)


def _v2_assertion_report(
    trace_id: str,
    outcomes: dict[str, bool],
) -> AssertionReport:
    report = AssertionReport(run_id=trace_id, baseline_run_id=None)
    for name, passed in outcomes.items():
        report.add(
            AssertionResult(
                check_name=name,
                passed=passed,
                message=(
                    "invariant satisfied"
                    if passed
                    else "invariant violated in this trial"
                ),
            )
        )
    return report


def run_trials(
    agent_script: Path,
    *,
    trials: int,
    policy: AssertionPolicy,
    config: MaidaConfig,
    project_root: Path | None = None,
    baseline: dict | None = None,
    confidence_level: float = 0.95,
    pass_rate_threshold: float = 0.90,
) -> TrialRunReport:
    """Execute the agent in isolated workspaces and aggregate policy tiers."""
    del confidence_level, pass_rate_threshold
    if not policy.metrics:
        policy = merge_policy(policy, {})
    if trials < 1:
        raise ValueError("trials must be at least 1")

    root = (project_root or Path.cwd()).resolve()
    script = agent_script if agent_script.is_absolute() else root / agent_script
    script = script.resolve()
    try:
        relative_script = script.relative_to(root)
    except ValueError as error:
        raise ValueError("agent script must be inside the project workspace") from error
    if not script.is_file():
        raise FileNotFoundError(f"Agent script not found: {agent_script}")
    validate_policy_against_baseline(policy, baseline)

    records: list[TrialRecord] = []
    abort_reason: str | None = None
    for trial_number in range(1, trials + 1):
        with tempfile.TemporaryDirectory(prefix="maida-trial-") as temp:
            trial_root = Path(temp) / "workspace"
            trial_data_dir = Path(temp) / "data"
            trial_root.mkdir()
            _copy_workspace(root, trial_root)
            env = os.environ.copy()
            env["MAIDA_DATA_DIR"] = str(trial_data_dir)
            env["MAIDA_TRIAL_INDEX"] = str(trial_number)
            completed = subprocess.run(
                [sys.executable, str(relative_script)],
                cwd=trial_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            runs_dir = trial_data_dir / "runs"
            trace_dirs = (
                sorted(path for path in runs_dir.iterdir() if path.is_dir())
                if runs_dir.is_dir()
                else []
            )
            if len(trace_dirs) != 1:
                raise RunExecutionError(
                    f"Trial {trial_number} must produce exactly one trace; "
                    f"found {len(trace_dirs)}"
                )
            trace_id = trace_dirs[0].name
            _preserve_trace(trace_id, trial_data_dir, config)
            full_id, meta, events = load_run_for_analysis(trace_id, config)
            extracted = extract_run_metrics(meta, events)
            invariants = invariant_outcomes(extracted, policy, baseline)
            assertion_report = (
                run_assertions(full_id, policy, baseline=baseline, config=config)
                if policy.source_format != "v2"
                else _v2_assertion_report(full_id, invariants)
            )
            baseline_diff = (
                asdict(compute_diff(full_id, baseline=baseline, config=config))
                if baseline is not None
                else None
            )
            records.append(
                TrialRecord(
                    trial=trial_number,
                    trace_id=full_id,
                    run_name=meta.get("run_name"),
                    process_exit_code=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    assertion_report=assertion_report,
                    baseline_diff=baseline_diff,
                    metric_values=numeric_metrics(extracted),
                    invariant_outcomes=invariants,
                    structural_signature=structural_signature(extracted),
                )
            )
            if policy.fail_fast and (
                completed.returncode != 0 or not all(invariants.values())
            ):
                abort_reason = (
                    "agent_process_failure"
                    if completed.returncode != 0
                    else "invariant_violation"
                )
                break

    stopping_rule = "fixed_n_fail_fast" if policy.fail_fast else "fixed_n"
    aggregate_results = aggregate_metrics(
        policy=policy,
        trial_values=[record.metric_values for record in records],
        trial_invariants=[record.invariant_outcomes for record in records],
        process_outcomes=[record.process_exit_code == 0 for record in records],
        baseline=baseline,
        trials_budgeted=trials,
        stopping_rule=stopping_rule,
    )
    return TrialRunReport(
        trials_requested=trials,
        trials=records,
        aggregate_results=aggregate_results,
        confidence_level=policy.confidence_level,
        pass_rate_threshold=policy.pass_rate_threshold,
        stopping_rule=stopping_rule,
        abort_reason=abort_reason,
        environment_fingerprint=_environment_fingerprint(root),
    )
