"""Repeated, isolated execution of a traced agent script."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from maida.assertions import AssertionPolicy, AssertionReport, run_assertions
from maida.config import MaidaConfig
from maida.storage import load_run_meta


class RunExecutionError(RuntimeError):
    """The agent process could not produce an unambiguous completed trace."""


@dataclass(frozen=True)
class TrialRecord:
    """One isolated agent invocation and its assertion outcome."""

    trial: int
    trace_id: str
    run_name: str | None
    process_exit_code: int
    stdout: str
    stderr: str
    assertion_report: AssertionReport

    @property
    def passed(self) -> bool:
        return self.process_exit_code == 0 and self.assertion_report.passed

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
        }


@dataclass(frozen=True)
class TrialRunReport:
    """Collected results for a fixed number of isolated trials."""

    trials_requested: int
    trials: list[TrialRecord] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(trial.passed for trial in self.trials)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trials_requested": self.trials_requested,
            "passed": self.passed,
            "trials": [trial.to_dict() for trial in self.trials],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_text(self) -> str:
        lines = []
        for trial in self.trials:
            verdict = "PASS" if trial.passed else "FAIL"
            lines.append(
                f"Trial {trial.trial}/{self.trials_requested}: {verdict} "
                f"(trace {trial.trace_id[:8]})"
            )
        lines.extend(["", f"RESULT: {'PASSED' if self.passed else 'FAILED'}"])
        return "\n".join(lines)


def _workspace_files(project_root: Path) -> list[Path]:
    """Return tracked and nonignored untracked files from *project_root*."""
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


def _preserve_trace(trace_id: str, trial_data_dir: Path, config: MaidaConfig) -> None:
    source = trial_data_dir / "runs" / trace_id
    destination = config.data_dir.expanduser() / "runs" / trace_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RunExecutionError(f"Trace destination already exists: {trace_id}")
    shutil.copytree(source, destination)


def run_trials(
    agent_script: Path,
    *,
    trials: int,
    policy: AssertionPolicy,
    config: MaidaConfig,
    project_root: Path | None = None,
    baseline: dict | None = None,
) -> TrialRunReport:
    """Execute *agent_script* in a fresh copied workspace for every trial."""
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

    records: list[TrialRecord] = []
    for trial_number in range(1, trials + 1):
        with tempfile.TemporaryDirectory(prefix="maida-trial-") as temp:
            trial_root = Path(temp) / "workspace"
            trial_data_dir = Path(temp) / "data"
            trial_root.mkdir()
            _copy_workspace(root, trial_root)

            env = os.environ.copy()
            env["MAIDA_DATA_DIR"] = str(trial_data_dir)
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
            meta = load_run_meta(trace_id, config)
            assertion_report = run_assertions(
                trace_id, policy, baseline=baseline, config=config
            )
            records.append(
                TrialRecord(
                    trial=trial_number,
                    trace_id=trace_id,
                    run_name=meta.get("run_name"),
                    process_exit_code=completed.returncode,
                    stdout=completed.stdout,
                    stderr=completed.stderr,
                    assertion_report=assertion_report,
                )
            )

    return TrialRunReport(trials_requested=trials, trials=records)
