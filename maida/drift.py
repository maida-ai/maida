"""Read-only scheduled checks over a persisted window of Maida traces."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

import maida.storage as storage
from maida.assertions import AssertionPolicy, run_assertions
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
from maida.runner_v2 import TrialRecord, TrialRunReport, _v2_assertion_report


class DriftWindowError(ValueError):
    """The requested persisted trace window cannot be evaluated safely."""


@dataclass(frozen=True)
class LoadedWindowTrace:
    """One fully validated native trace selected for a drift sample."""

    trace_id: str
    meta: dict
    events: list[dict]
    started_at: datetime


class TraceWindowSource(Protocol):
    """Source boundary for native runs and future exported-window formats."""

    analysis_config: MaidaConfig

    def load(self, agent_name: str) -> list[LoadedWindowTrace]: ...


# Conforming external emitters already produce native ``runs/<trace_id>/``
# directories. A future source can add ``maida export`` JSON window inputs.


def _parse_started_at(trace_id: str, value: object) -> datetime:
    if not isinstance(value, str):
        raise DriftWindowError(f"Trace {trace_id[:8]} has no valid started_at")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise DriftWindowError(
            f"Trace {trace_id[:8]} has invalid started_at {value!r}"
        ) from error
    if parsed.tzinfo is None:
        raise DriftWindowError(
            f"Trace {trace_id[:8]} started_at must include a timezone"
        )
    return parsed


class NativeTraceWindowSource:
    """Load current native ``runs/<trace_id>/`` directories without mutation."""

    def __init__(self, runs_dir: Path, config: MaidaConfig) -> None:
        self.runs_dir = runs_dir.expanduser().resolve()
        if not self.runs_dir.is_dir():
            raise DriftWindowError(f"Trace window directory not found: {runs_dir}")
        if self.runs_dir.name != "runs":
            raise DriftWindowError(
                "Trace window must be a native Maida runs directory ending in /runs"
            )
        self.analysis_config = replace(config, data_dir=self.runs_dir.parent)

    def load(self, agent_name: str) -> list[LoadedWindowTrace]:
        candidates = sorted(
            entry
            for entry in self.runs_dir.iterdir()
            if entry.is_dir() and not entry.name.startswith(".")
        )
        if not candidates:
            raise DriftWindowError(f"Trace window {self.runs_dir} contains no traces")

        loaded: list[LoadedWindowTrace] = []
        for entry in candidates:
            try:
                trace_id, meta, events = storage.load_run_for_analysis(
                    entry.name, self.analysis_config
                )
            except (FileNotFoundError, ValueError, storage.RunValidationError) as error:
                raise DriftWindowError(
                    f"Invalid trace window entry {entry.name}: {error}"
                ) from error
            except storage.UnsupportedTraceFormatError as error:
                raise DriftWindowError(str(error)) from error
            if meta.get("status") == "running" or meta.get("ended_at") is None:
                raise DriftWindowError(
                    f"Trace {trace_id[:8]} is incomplete; only completed traces "
                    "can enter a drift window"
                )
            started_at = _parse_started_at(trace_id, meta.get("started_at"))
            if meta.get("run_name") == agent_name:
                loaded.append(
                    LoadedWindowTrace(
                        trace_id=trace_id,
                        meta=meta,
                        events=events,
                        started_at=started_at,
                    )
                )

        if not loaded:
            raise DriftWindowError(
                f"Trace window contains no completed traces for agent {agent_name!r}"
            )
        loaded.sort(key=lambda item: (item.started_at, item.trace_id))
        return loaded


@dataclass(frozen=True)
class DriftTarget:
    """One agent/baseline pair; future directory fanout yields multiple targets."""

    agent_name: str
    baseline: dict


def resolve_drift_target(baseline: dict, requested: str | None) -> DriftTarget:
    """Resolve the single target supported by the initial CLI contract."""
    baseline_agent = baseline.get("source_run_name")
    if baseline_agent is not None and not isinstance(baseline_agent, str):
        raise DriftWindowError("Baseline source_run_name must be a string or null")
    if requested is not None:
        requested = requested.strip()
        if not requested:
            raise DriftWindowError("--agent must not be empty")
        if baseline_agent and requested != baseline_agent:
            raise DriftWindowError(
                f"Selected agent {requested!r} does not match baseline agent "
                f"{baseline_agent!r}"
            )
        return DriftTarget(agent_name=requested, baseline=baseline)
    if baseline_agent:
        return DriftTarget(agent_name=baseline_agent, baseline=baseline)
    raise DriftWindowError(
        "Baseline does not identify an agent; pass --agent to select one"
    )


def run_drift(
    runs_dir: Path,
    *,
    baseline: dict,
    policy: AssertionPolicy,
    config: MaidaConfig,
    agent_name: str | None = None,
    source: TraceWindowSource | None = None,
) -> TrialRunReport:
    """Evaluate a complete persisted trace window against one agent baseline."""
    target = resolve_drift_target(baseline, agent_name)
    if not policy.metrics:
        policy = merge_policy(policy, {})
    validate_policy_against_baseline(policy, baseline)

    window_source = source or NativeTraceWindowSource(runs_dir, config)
    traces = window_source.load(target.agent_name)
    window_config = window_source.analysis_config

    records: list[TrialRecord] = []
    for index, item in enumerate(traces, start=1):
        extracted = extract_run_metrics(item.meta, item.events)
        invariants = invariant_outcomes(extracted, policy, baseline)
        assertion_report = (
            run_assertions(
                item.trace_id,
                policy,
                baseline=baseline,
                config=window_config,
            )
            if policy.source_format != "v2"
            else _v2_assertion_report(item.trace_id, invariants)
        )
        records.append(
            TrialRecord(
                trial=index,
                trace_id=item.trace_id,
                run_name=item.meta.get("run_name"),
                process_exit_code=None,
                stdout="",
                stderr="",
                assertion_report=assertion_report,
                run_status=item.meta.get("status"),
                baseline_diff=asdict(
                    compute_diff(item.trace_id, baseline=baseline, config=window_config)
                ),
                metric_values=numeric_metrics(extracted),
                invariant_outcomes=invariants,
                structural_signature=structural_signature(extracted),
            )
        )

    aggregate_results = aggregate_metrics(
        policy=policy,
        trial_values=[record.metric_values for record in records],
        trial_invariants=[record.invariant_outcomes for record in records],
        process_outcomes=[record.run_status == "ok" for record in records],
        baseline=baseline,
        trials_budgeted=len(records),
        stopping_rule="fixed_n",
    )
    return TrialRunReport(
        trials_requested=len(records),
        trials=records,
        aggregate_results=aggregate_results,
        confidence_level=policy.confidence_level,
        pass_rate_threshold=policy.pass_rate_threshold,
        stopping_rule="fixed_n",
        environment_fingerprint={},
        report_kind="drift",
        agent_name=target.agent_name,
        window_input_format="maida_runs",
        baseline_source_run_id=baseline.get("source_run_id"),
        baseline_source_run_name=baseline.get("source_run_name"),
    )
