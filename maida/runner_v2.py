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
    _markdown_baseline_provenance,
    _markdown_table_cell,
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
from maida.plan_contract import PlanEvidence
from maida.statistics import GateVerdict, StatisticalResult, aggregate_verdict
from maida.schema_versions import REPORT_SCHEMA_VERSION
from maida.storage import load_run_for_analysis


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
            self.process_exit_code == 0
            if self.process_exit_code is not None
            else self.run_status == "ok"
        )
        return (
            process_succeeded
            and self.assertion_report.passed
            and all(self.invariant_outcomes.values())
        )

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
            "aggregate_results": [
                result.to_dict() for result in self.aggregate_results
            ],
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
            lines.append(
                f"Stopped after {len(self.trials)}/{self.trials_requested}: "
                f"{self.abort_reason}"
            )
        lines.extend(["", f"RESULT: {self.verdict.value.upper()}"])
        return "\n".join(lines)

    def to_markdown(self, baseline_path: str | None = None) -> str:
        """Render the compact, GitHub-facing statistical gate report."""
        return _render_trial_report_markdown(self, baseline_path=baseline_path)


_CHECK_LABELS = {
    "agent_process": "Agent execution",
    "step_count": "Steps",
    "tool_call_count": "Tool calls",
    "cost_tokens": "Tokens",
    "latency_ms": "Latency",
    "llm_call_count": "Model calls",
    "error_count": "Errors",
    "loop_warning_count": "Loops",
    "no_loops": "Loops",
    "no_guardrails": "Guardrails",
    "stop_condition_reached": "Terminal state",
    "forbidden_tools": "Allowed tools",
    "required_tools": "Required tools",
    "task_pass_rate": "Successful behavior",
}

_SUMMARY_CHANGES = {
    "total_events": ("Steps", ""),
    "tool_calls": ("Tool calls", ""),
    "total_tokens": ("Tokens", ""),
    "duration_ms": ("Latency", " ms"),
    "llm_calls": ("Model calls", ""),
    "errors": ("Errors", ""),
    "loop_warnings": ("Loops", ""),
}


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def _number(value: object) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        numeric = float(value)
        return str(int(numeric)) if numeric.is_integer() else f"{numeric:g}"
    return _markdown_table_cell(value)


def _signed_number(value: object) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        numeric = float(value)
        sign = "+" if numeric > 0 else ""
        return f"{sign}{_number(numeric)}"
    return _number(value)


def _inline(value: object) -> str:
    escaped = _markdown_table_cell(value).replace("`", "&#96;")
    return f"`{escaped}`"


def _sequence(values: object) -> str:
    if not isinstance(values, list) or not values:
        return "(none)"
    return " → ".join(str(value).replace("\n", " ") for value in values)


def _pair(value: object) -> tuple[object, object] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    return None


def _change_sentence(label: str, suffix: str, current: object, baseline: object) -> str:
    current_text = f"{_number(current)}{suffix}"
    baseline_text = f"{_number(baseline)}{suffix}"
    trend = "changed"
    delta: object | None = None
    if isinstance(current, (int, float)) and isinstance(baseline, (int, float)):
        delta = current - baseline
        if delta > 0:
            trend = "increased"
        elif delta < 0:
            trend = "decreased"
    sentence = f"{label} {trend} from {baseline_text} to {current_text}"
    if delta:
        sentence += f" ({_signed_number(delta)}{suffix})"
    return f"{sentence}."


def _behavior_lines(diff: dict[str, Any] | None) -> list[str]:
    if not isinstance(diff, dict):
        return []
    lines: list[str] = []
    current_sequence = diff.get("current_tool_sequence")
    baseline_sequence = diff.get("baseline_tool_sequence")
    if diff.get("reordered_tools"):
        lines.append(
            "Tool order changed from "
            f"{_inline(_sequence(baseline_sequence))} to "
            f"{_inline(_sequence(current_sequence))}."
        )
    for tool in diff.get("new_tools") or []:
        lines.append(f"New tool used: {_inline(tool)}.")
    for tool in diff.get("removed_tools") or []:
        lines.append(f"Tool removed: {_inline(tool)}.")
    repeated = diff.get("repeated_tools") or {}
    if isinstance(repeated, dict):
        for tool, counts in repeated.items():
            pair = _pair(counts)
            if pair is None:
                continue
            baseline_count, current_count = pair
            lines.append(
                f"Tool {_inline(tool)} repeated {_number(current_count)} times "
                f"(baseline: {_number(baseline_count)})."
            )

    summary = diff.get("summary_diff") or {}
    if isinstance(summary, dict):
        for key, (label, suffix) in _SUMMARY_CHANGES.items():
            pair = _pair(summary.get(key))
            if pair is None:
                continue
            current, baseline = pair
            lines.append(_change_sentence(label, suffix, current, baseline))

    guardrails = _pair(diff.get("guardrail_event_diff"))
    if guardrails is not None:
        current, baseline = guardrails
        count = int(current) if isinstance(current, (int, float)) else 0
        lines.append(
            f"Guardrails triggered {_number(current)} {_plural(count, 'time')} "
            f"(baseline: {_number(baseline)})."
        )
    terminal = _pair(diff.get("terminal_status_diff"))
    if terminal is not None:
        current, baseline = terminal
        lines.append(
            f"Terminal state changed from {_inline(baseline)} to {_inline(current)}."
        )
    return lines


def _all_behavior_lines(trials: list[TrialRecord]) -> list[str]:
    return list(
        dict.fromkeys(
            sentence
            for trial in trials
            for sentence in _behavior_lines(trial.baseline_diff)
        )
    )


def _check_label(check_name: str) -> str:
    return _CHECK_LABELS.get(check_name, check_name.replace("_", " ").capitalize())


def _result_title(result: StatisticalResult) -> str:
    label = _check_label(result.check_name)
    violations = int(result.evidence.get("violations", 0) or 0)
    trials = result.trials_used
    trial_word = _plural(trials, "trial")
    if result.verdict is GateVerdict.PASS:
        if result.check_name == "agent_process":
            return "Agent execution stayed healthy."
        return f"{label} stayed within the allowed range."
    if result.verdict is GateVerdict.INCONCLUSIVE:
        if result.check_name == "task_pass_rate":
            return "Successful behavior is still inconclusive."
        return f"{label} is still inconclusive."
    if result.check_name == "agent_process":
        return f"Agent execution failed in {violations} of {trials} {trial_word}."
    if result.check_name in {"no_loops", "loop_warning_count"}:
        return f"Loops appeared in {violations} of {trials} {trial_word}."
    if result.check_name == "no_guardrails":
        return f"Guardrails triggered in {violations} of {trials} {trial_word}."
    if result.check_name == "stop_condition_reached":
        return "The agent did not reach a successful terminal state."
    if result.check_name == "latency_ms":
        return "Latency increased beyond the allowed range."
    if result.check_name == "step_count":
        return "Steps increased beyond the allowed range."
    if result.check_name == "cost_tokens":
        return "Tokens increased beyond the allowed range."
    if result.check_name == "task_pass_rate":
        return "Successful behavior fell below the required rate."
    return f"{label} violated the policy."


def _measurement(value: object, check_name: str) -> str:
    suffix = " ms" if check_name == "latency_ms" else ""
    return f"{_number(value)}{suffix}"


def _result_evidence(result: StatisticalResult) -> str | None:
    evidence = result.evidence
    if result.kind == "measured":
        parts: list[str] = []
        baseline = evidence.get("baseline")
        if baseline is not None:
            parts.append(f"baseline {_measurement(baseline, result.check_name)}")
        delta = evidence.get("delta")
        if (
            result.check_name == "step_count"
            and isinstance(delta, (int, float))
            and delta
        ):
            parts.append(f"delta {_signed_number(delta)}")
        allowed = evidence.get("allowed") or {}
        lower = allowed.get("lower") if isinstance(allowed, dict) else None
        upper = allowed.get("upper") if isinstance(allowed, dict) else None
        if lower is not None and upper is not None:
            parts.append(
                "allowed between "
                f"{_measurement(lower, result.check_name)} and "
                f"{_measurement(upper, result.check_name)}"
            )
        elif upper is not None:
            parts.append(f"allowed at most {_measurement(upper, result.check_name)}")
        elif lower is not None:
            parts.append(f"allowed at least {_measurement(lower, result.check_name)}")
        observed = _measurement(evidence.get("observed", 0), result.check_name)
        return f"Observed {observed}" + (f" ({'; '.join(parts)})." if parts else ".")
    if result.kind == "invariant":
        if result.check_name == "agent_process" and result.verdict is GateVerdict.PASS:
            return (
                f"All {result.trials_used} {_plural(result.trials_used, 'trial')} "
                "completed successfully."
            )
        return None

    bounds = evidence.get("confidence_bounds") or {}
    lower = float(bounds.get("lower", 0.0))
    upper = float(bounds.get("upper", 1.0))
    confidence = float(evidence.get("confidence", 0.0)) * 100
    observed_rate = float(evidence.get("observed_rate", 0.0))
    if result.mode == "report_only":
        return (
            f"Observed pass rate {observed_rate:.3f}; {confidence:g}% confidence "
            f"range {lower:.3f}–{upper:.3f}."
        )
    successes = int(evidence.get("successes", result.successes))
    threshold = evidence.get("threshold", 0.0)
    if isinstance(threshold, dict):
        target = (
            f"target {_number(threshold.get('lower'))}–"
            f"{_number(threshold.get('upper'))}"
        )
    elif result.direction == "upper":
        target = f"target at most {float(threshold):.3f}"
    else:
        target = f"target at least {float(threshold):.3f}"
    return (
        f"{successes}/{result.trials_used} trials passed; {confidence:g}% "
        f"confidence range {lower:.3f}–{upper:.3f} ({target})."
    )


def _result_line(result: StatisticalResult, *, report_only: bool = False) -> str:
    if report_only:
        icon = "ℹ️"
        title = f"{_check_label(result.check_name)} were observed without blocking the gate."
    else:
        icon = {
            GateVerdict.PASS: "✅",
            GateVerdict.FAIL: "❌",
            GateVerdict.INCONCLUSIVE: "⚪",
        }[result.verdict]
        title = _result_title(result)
    line = f"- {icon} **{title}** {_inline(result.check_name)}"
    if evidence := _result_evidence(result):
        line += f" — {evidence}"
    return line


def _count_summary(report: TrialRunReport) -> str:
    failures = sum(
        result.verdict is GateVerdict.FAIL for result in report.aggregate_results
    )
    inconclusive = sum(
        result.verdict is GateVerdict.INCONCLUSIVE
        for result in report.aggregate_results
    )
    blocking = sum(result.verdict is not None for result in report.aggregate_results)
    if failures:
        check_summary = f"{failures} blocking {_plural(failures, 'check')} failed"
        if inconclusive:
            check_summary += (
                f", {inconclusive} {_plural(inconclusive, 'check')} inconclusive"
            )
    elif inconclusive:
        check_summary = (
            f"{inconclusive} blocking {_plural(inconclusive, 'check')} inconclusive"
        )
    else:
        check_summary = f"{blocking} blocking {_plural(blocking, 'check')} passed"

    completed = len(report.trials)
    if completed < report.trials_requested:
        trial_summary = f"{completed}/{report.trials_requested} trials completed"
    else:
        passed = sum(trial.passed for trial in report.trials)
        trial_summary = f"{passed}/{report.trials_requested} trials passed"
    return f"**{check_summary}** · **{trial_summary}**"


def _next_steps(report: TrialRunReport, baseline_path: str | None) -> list[str]:
    short_trace = report.trials[0].trace_id[:8] if report.trials else "TRACE_ID"
    safe_baseline = _markdown_table_cell(baseline_path) if baseline_path else None
    if report.verdict is GateVerdict.PASS:
        if report.trials:
            return [
                f"- No gate action needed. Inspect the trace: `maida view {short_trace}`"
            ]
        return ["- No gate action needed."]
    if report.verdict is GateVerdict.INCONCLUSIVE:
        rerun = (
            f"maida drift --window RUNS_DIR --baseline {safe_baseline}"
            if report.report_kind == "drift" and safe_baseline
            else f"maida run AGENT.py --trials {report.trials_requested}"
        )
        steps = [f"- Collect the remaining evidence and rerun: `{rerun}`"]
        if report.trials:
            steps.append(f"- Open the trace locally: `maida view {short_trace}`")
        return steps

    steps = ["- Review the behavioral changes and blocking checks above."]
    if safe_baseline:
        steps.append(
            f"- Inspect the full diff: `maida diff {short_trace} --baseline {safe_baseline}`"
        )
    if report.trials:
        steps.append(f"- Open the trace locally: `maida view {short_trace}`")
    if safe_baseline:
        steps.extend(
            [
                "- If this change is intentional, comment `/maida accept` on the PR "
                "or accept locally: "
                f"`maida accept {short_trace} --baseline {safe_baseline} "
                '--reason "..."`',
                "- Otherwise fix the agent behavior and rerun: "
                f"`maida run AGENT.py --baseline {safe_baseline}`",
            ]
        )
    else:
        steps.append(
            "- Otherwise fix the agent behavior or policy, then rerun the gate."
        )
    return steps


def _render_trial_report_markdown(
    report: TrialRunReport, *, baseline_path: str | None
) -> str:
    icons = {
        GateVerdict.PASS: "✅",
        GateVerdict.FAIL: "❌",
        GateVerdict.INCONCLUSIVE: "⚪",
    }
    heading = "Maida drift check" if report.report_kind == "drift" else "Maida verdict"
    lines = [
        f"## {icons[report.verdict]} {heading}: {report.verdict.value}",
        "",
        _count_summary(report),
    ]
    if report.abort_reason:
        lines.extend(
            [
                "",
                f"> Sample stopped early: {_inline(report.abort_reason)}.",
            ]
        )
    lines.extend(["", "### Behavior vs baseline", ""])
    behavior_lines = _all_behavior_lines(report.trials)
    baseline_configured = any(
        trial.baseline_diff is not None for trial in report.trials
    )
    if behavior_lines:
        lines.extend(f"- {sentence}" for sentence in behavior_lines)
    elif baseline_configured:
        lines.append(
            "No behavior changed from the accepted baseline across the sampled trials."
        )
    else:
        lines.append("No baseline comparison was configured for this run.")

    blocking_results = [
        result
        for result in report.aggregate_results
        if result.verdict in {GateVerdict.FAIL, GateVerdict.INCONCLUSIVE}
    ]
    if blocking_results:
        lines.extend(["", "### Blocking checks", ""])
        lines.extend(_result_line(result) for result in blocking_results)
    if report.verdict is GateVerdict.INCONCLUSIVE:
        if report.report_kind == "drift":
            lines.extend(
                [
                    "",
                    "> Neutral result: no blocking failure was established. "
                    "The CLI exits 0; collect more evidence before promotion.",
                ]
            )
        else:
            lines.extend(
                [
                    "",
                    "> Maida did not establish a blocking regression, but this sample "
                    "is too small to approve confidently. Collect more trials and "
                    "rerun before promotion.",
                ]
            )

    lines.extend(_markdown_baseline_provenance(report.baseline_acceptance))
    if lines[-1]:
        lines.append("")
    lines.extend(["### Next steps", "", *_next_steps(report, baseline_path)])

    passing = [
        result
        for result in report.aggregate_results
        if result.verdict is GateVerdict.PASS
    ]
    report_only = [
        result for result in report.aggregate_results if result.verdict is None
    ]
    lines.extend(
        [
            "",
            "<details>",
            "<summary>Passing checks, report-only metrics, and trial evidence</summary>",
        ]
    )
    if passing:
        lines.extend(["", "#### Passing checks", ""])
        lines.extend(_result_line(result) for result in passing)
    if report_only:
        lines.extend(["", "#### Report-only metrics", ""])
        lines.extend(_result_line(result, report_only=True) for result in report_only)

    evidence_heading = (
        "### Window traces" if report.report_kind == "drift" else "#### Trial evidence"
    )
    lines.extend(["", evidence_heading, ""])
    if report.trials:
        execution_heading = (
            "Run status" if report.report_kind == "drift" else "Process exit"
        )
        item_heading = "Trace" if report.report_kind == "drift" else "Trial"
        lines.extend(
            [
                f"| {item_heading} | Outcome | Trace | {execution_heading} | Behavior changes |",
                "| ---: | --- | --- | ---: | --- |",
            ]
        )
        for trial in report.trials:
            if trial.baseline_diff is None:
                changes = "not configured"
            else:
                count = len(_behavior_lines(trial.baseline_diff))
                changes = (
                    "none" if count == 0 else f"{count} {_plural(count, 'change')}"
                )
            execution = (
                trial.run_status or "unknown"
                if report.report_kind == "drift"
                else str(trial.process_exit_code)
            )
            lines.append(
                f"| {trial.trial} | {'PASS' if trial.passed else 'FAIL'} | "
                f"`{trial.trace_id[:8]}` | {execution} | {changes} |"
            )
    else:
        lines.append("No trial evidence was recorded.")
    lines.extend(
        [
            "",
            "</details>",
            "",
            "---",
            "*Gated by [Maida](https://maida.ai) — the local-first behavioral"
            " regression gate for AI agents.*",
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
        baseline_acceptance=(
            baseline.get("acceptance")
            if isinstance((baseline or {}).get("acceptance"), dict)
            else None
        ),
    )
