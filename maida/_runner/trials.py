"""Execute isolated agent trials and aggregate gate evidence."""

from __future__ import annotations

import math
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from maida.assertions import AssertionPolicy, AssertionReport, AssertionResult, run_assertions
from maida.baseline import extract_run_metrics
from maida.baseline_bind import validate_policy_against_baseline
from maida.config import MaidaConfig
from maida.diff import compute_diff
from maida.gate import aggregate_metrics, invariant_outcomes, numeric_metrics, structural_signature
from maida.policy import merge_policy
from maida.storage import load_run_for_analysis
from maida._runner.types import RunExecutionError, TrialRecord, TrialRunReport
from maida._runner.workspace import _copy_workspace, _environment_fingerprint, _preserve_trace


def invariant_assertion_report(
    trace_id: str,
    outcomes: dict[str, bool],
) -> AssertionReport:
    report = AssertionReport(run_id=trace_id, baseline_run_id=None)
    for name, passed in outcomes.items():
        report.add(
            AssertionResult(
                check_name=name,
                passed=passed,
                message=("invariant satisfied" if passed else "invariant violated in this trial"),
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
    max_wall_time_seconds: float | None = None,
) -> TrialRunReport:
    """Execute isolated trials, optionally sharing a wall-time budget across them."""
    deadline = None
    if max_wall_time_seconds is not None:
        if not math.isfinite(max_wall_time_seconds) or max_wall_time_seconds <= 0:
            raise ValueError("max_wall_time_seconds must be finite and positive")
        deadline = time.monotonic() + max_wall_time_seconds
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
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise TimeoutError("trial execution exceeded wall-time cap")
            try:
                completed = subprocess.run(
                    [sys.executable, str(relative_script)],
                    cwd=trial_root,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=remaining,
                )
            except subprocess.TimeoutExpired as error:
                raise TimeoutError("trial execution exceeded wall-time cap") from error

            runs_dir = trial_data_dir / "runs"
            trace_dirs = sorted(path for path in runs_dir.iterdir() if path.is_dir()) if runs_dir.is_dir() else []
            if len(trace_dirs) != 1:
                raise RunExecutionError(f"Trial {trial_number} must produce exactly one trace; found {len(trace_dirs)}")
            trace_id = trace_dirs[0].name
            _preserve_trace(trace_id, trial_data_dir, config)
            full_id, meta, events = load_run_for_analysis(trace_id, config)
            extracted = extract_run_metrics(meta, events)
            invariants = invariant_outcomes(extracted, policy, baseline)
            assertion_report = (
                run_assertions(full_id, policy, baseline=baseline, config=config)
                if policy.source_format == "cli"
                else invariant_assertion_report(full_id, invariants)
            )
            baseline_diff = (
                asdict(compute_diff(full_id, baseline=baseline, config=config)) if baseline is not None else None
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
            if policy.fail_fast and (completed.returncode != 0 or not all(invariants.values())):
                abort_reason = "agent_process_failure" if completed.returncode != 0 else "invariant_violation"
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
    report = TrialRunReport(
        trials_requested=trials,
        trials=records,
        aggregate_results=aggregate_results,
        confidence_level=policy.confidence_level,
        pass_rate_threshold=policy.pass_rate_threshold,
        stopping_rule=stopping_rule,
        abort_reason=abort_reason,
        environment_fingerprint=_environment_fingerprint(root),
        baseline_acceptance=(
            baseline.get("acceptance") if isinstance((baseline or {}).get("acceptance"), dict) else None
        ),
    )
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("trial execution exceeded wall-time cap")
    return report
