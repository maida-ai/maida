"""Extract reviewable gate drafts from completed native trace windows."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import yaml

from maida.baseline import extract_run_metrics, load_baseline
from maida.baseline_sample import create_baseline_from_report
from maida.config import MaidaConfig
from maida.drift import (
    DriftWindowError,
    LoadedWindowTrace,
    NativeTraceWindowSource,
    run_drift,
)
from maida.gate import invariant_outcomes, numeric_metrics, structural_signature
from maida.policy import load_policy
from maida.schema_versions import REPORT_SCHEMA_VERSION
from maida.statistics import GateVerdict


DRAFT_VERSION = "1.0.0"


class ExtractionInputError(ValueError):
    """The requested source, selection, or output location is unsafe."""


def _signature_id(signature: dict[str, Any]) -> str:
    encoded = json.dumps(
        signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _artifact_name(run_name: str) -> str:
    normalized = unicodedata.normalize("NFKD", run_name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")
    slug = (slug or "workflow")[:48].rstrip("-")
    digest = hashlib.sha256(run_name.encode("utf-8")).hexdigest()[:12]
    return f"{slug}-{digest}"


def _as_number(value: float) -> int | float:
    return int(value) if value.is_integer() else value


def _select_workflows(
    traces: list[LoadedWindowTrace], requested: Iterable[str] | None
) -> list[tuple[str, list[LoadedWindowTrace]]]:
    groups: dict[str, list[LoadedWindowTrace]] = {}
    for trace in traces:
        run_name = trace.meta.get("run_name")
        if isinstance(run_name, str) and run_name.strip():
            groups.setdefault(run_name, []).append(trace)

    selectors = list(requested or ())
    if selectors:
        if any(not isinstance(item, str) or not item.strip() for item in selectors):
            raise ExtractionInputError("--workflow must not be empty")
        duplicates = sorted(
            name for name, count in Counter(selectors).items() if count > 1
        )
        if duplicates:
            raise ExtractionInputError(
                f"duplicate --workflow selection: {', '.join(repr(x) for x in duplicates)}"
            )
        missing = sorted(name for name in selectors if name not in groups)
        if missing:
            raise ExtractionInputError(
                "no traces for workflow selection: "
                + ", ".join(repr(name) for name in missing)
            )
        selected_names = sorted(selectors)
    else:
        selected_names = sorted(groups)

    if not selected_names:
        raise ExtractionInputError(
            "trace window contains no completed traces with a nonempty run_name"
        )
    return [(name, groups[name]) for name in selected_names]


def _trace_evidence(trace: LoadedWindowTrace) -> dict[str, Any]:
    extracted = extract_run_metrics(trace.meta, trace.events)
    return {
        "trace": trace,
        "extracted": extracted,
        "metrics": numeric_metrics(extracted),
        "signature": structural_signature(extracted),
    }


def _workflow_summary(
    run_name: str,
    evidence: list[dict[str, Any]],
    artifact_dir: str,
) -> dict[str, Any]:
    tools = [set(item["signature"].get("tool_path") or []) for item in evidence]
    union = set().union(*tools)
    intersection = set.intersection(*tools) if tools else set()
    steps = [item["metrics"]["step_count"] for item in evidence]
    tool_calls = [item["metrics"]["tool_call_count"] for item in evidence]
    tokens = [item["metrics"]["cost_tokens"] for item in evidence]

    clusters_by_id: dict[str, dict[str, Any]] = {}
    for item in evidence:
        identifier = _signature_id(item["signature"])
        trace_id = item["trace"].trace_id
        cluster = clusters_by_id.setdefault(
            identifier,
            {
                "signature_id": identifier,
                "signature": item["signature"],
                "representative_trace_id": trace_id,
                "trace_ids": [],
                "count": 0,
            },
        )
        cluster["trace_ids"].append(trace_id)
        cluster["count"] += 1

    clusters = list(clusters_by_id.values())
    return {
        "run_name": run_name,
        "artifact_dir": artifact_dir,
        "trace_ids": [item["trace"].trace_id for item in evidence],
        "representative_trace_ids": [
            cluster["representative_trace_id"] for cluster in clusters
        ],
        "clusters": clusters,
        "tools": {
            "intersection": sorted(intersection),
            "union": sorted(union),
        },
        "step_band": {
            "lower": _as_number(min(steps)),
            "upper": _as_number(max(steps)),
        },
        "ceilings": {
            "tool_calls": _as_number(max(tool_calls)),
            "tokens": _as_number(max(tokens)),
        },
        "terminal_states": sorted(
            {str(item["signature"].get("final_status") or "") for item in evidence}
        ),
    }


def _policy_candidates(
    summary: dict[str, Any], evidence: list[dict[str, Any]]
) -> list[tuple[str, str, dict[str, Any]]]:
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    required_tools = summary["tools"]["intersection"]
    if required_tools:
        candidates.append(
            (
                "required_tools",
                "Candidate: every selected trace used these tools; confirm they are required.",
                {"kind": "invariant", "all_of": required_tools},
            )
        )
    if summary["terminal_states"] == ["ok"]:
        candidates.append(
            (
                "stop_condition_reached",
                "Candidate: every selected trace ended successfully; confirm this invariant.",
                {"kind": "invariant", "require": True},
            )
        )
    if all(item["metrics"]["loop_warning_count"] == 0 for item in evidence):
        candidates.append(
            (
                "no_loops",
                "Candidate: no selected trace reported a loop; confirm this invariant.",
                {"kind": "invariant", "require": True},
            )
        )
    if all(not item["extracted"].get("guardrail_events") for item in evidence):
        candidates.append(
            (
                "no_guardrails",
                "Candidate: no selected trace triggered a guardrail; confirm this invariant.",
                {"kind": "invariant", "require": True},
            )
        )
    candidates.extend(
        [
            (
                "step_count",
                "Observed exact band; widen or narrow only after human review.",
                {
                    "kind": "measured",
                    "direction": "both",
                    "aggregate": "median",
                    "limit": summary["step_band"],
                },
            ),
            (
                "tool_call_count",
                "Observed upper ceiling; confirm the allowed tool-call budget.",
                {
                    "kind": "measured",
                    "direction": "upper",
                    "aggregate": "max",
                    "limit": summary["ceilings"]["tool_calls"],
                },
            ),
            (
                "cost_tokens",
                "Observed upper ceiling; confirm the allowed token budget.",
                {
                    "kind": "measured",
                    "direction": "upper",
                    "aggregate": "max",
                    "limit": summary["ceilings"]["tokens"],
                },
            ),
        ]
    )
    return candidates


def _render_policy(summary: dict[str, Any], evidence: list[dict[str, Any]]) -> str:
    lines = [
        "# DRAFT: This policy is not active and requires human review.",
        "# Review each candidate before copying it into .maida/policy.yaml.",
        "version: 2",
        f"trials: {len(evidence)}",
        "fail_fast: false",
        "metrics:",
    ]
    for name, comment, payload in _policy_candidates(summary, evidence):
        lines.append(f"  # {comment}")
        rendered = yaml.safe_dump(
            {name: payload},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        ).rstrip()
        lines.extend(f"  {line}" for line in rendered.splitlines())
    return "\n".join(lines) + "\n"


def _baseline_report(
    run_name: str,
    evidence: list[dict[str, Any]],
    policy_path: Path,
) -> dict[str, Any]:
    policy = load_policy(policy_path)
    trials = []
    for index, item in enumerate(evidence, start=1):
        trace = item["trace"]
        trials.append(
            {
                "trial": index,
                "trace_id": trace.trace_id,
                "run_name": run_name,
                "metric_values": item["metrics"],
                "invariant_outcomes": invariant_outcomes(
                    item["extracted"], policy, None
                ),
                "structural_signature": item["signature"],
            }
        )
    return {
        "report_version": REPORT_SCHEMA_VERSION,
        "metadata": {
            "trials_used": len(trials),
            "trials_budgeted": len(trials),
            "environment_fingerprint": {},
        },
        "trials": trials,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _verify_workflow(
    runs_dir: Path,
    *,
    artifact_dir: Path,
    run_name: str,
    config: MaidaConfig,
) -> None:
    baseline = load_baseline(artifact_dir / "baseline.json")
    policy = load_policy(artifact_dir / "policy.yaml")
    report = run_drift(
        runs_dir,
        baseline=baseline,
        policy=policy,
        config=config,
        agent_name=run_name,
    )
    if report.verdict is not GateVerdict.PASS:
        raise RuntimeError(
            f"generated draft for workflow {run_name!r} did not pass its source window"
        )


def extract_window(
    runs_dir: Path,
    *,
    out_dir: Path,
    config: MaidaConfig,
    workflows: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Create an atomic, inactive draft directory for selected workflow groups."""
    requested_out = out_dir.expanduser()
    if requested_out.exists() or requested_out.is_symlink():
        raise ExtractionInputError(f"output directory already exists: {out_dir}")
    final_dir = requested_out.resolve()
    source_dir = runs_dir.expanduser().resolve()
    if final_dir.exists() or final_dir.is_symlink():
        raise ExtractionInputError(f"output directory already exists: {out_dir}")
    if final_dir.is_relative_to(source_dir):
        raise ExtractionInputError(
            "output directory must not be inside the trace window"
        )

    try:
        source = NativeTraceWindowSource(runs_dir, config)
        traces = source.load_all()
    except DriftWindowError as error:
        raise ExtractionInputError(str(error)) from error
    selected = _select_workflows(traces, workflows)

    artifact_names = [_artifact_name(run_name) for run_name, _items in selected]
    if len(set(artifact_names)) != len(artifact_names):
        raise ExtractionInputError(
            "ambiguous workflow names produce the same artifact directory"
        )

    final_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = Path(
        tempfile.mkdtemp(
            prefix=f".{final_dir.name}.", suffix=".tmp", dir=final_dir.parent
        )
    )
    try:
        draft: dict[str, Any] = {
            "draft_version": DRAFT_VERSION,
            "review_required": True,
            "workflows": [],
        }
        for (run_name, workflow_traces), artifact_name in zip(selected, artifact_names):
            evidence = [_trace_evidence(trace) for trace in workflow_traces]
            artifact_rel = (Path("workflows") / artifact_name).as_posix()
            summary = _workflow_summary(run_name, evidence, artifact_rel)
            artifact_dir = staging_dir / artifact_rel
            artifact_dir.mkdir(parents=True)

            policy_path = artifact_dir / "policy.yaml"
            policy_path.write_text(_render_policy(summary, evidence), encoding="utf-8")
            baseline = create_baseline_from_report(
                _baseline_report(run_name, evidence, policy_path)
            )
            baseline["created_at"] = str(workflow_traces[-1].meta["ended_at"])
            _write_json(artifact_dir / "baseline.json", baseline)
            _verify_workflow(
                runs_dir,
                artifact_dir=artifact_dir,
                run_name=run_name,
                config=config,
            )
            draft["workflows"].append(summary)

        _write_json(staging_dir / "draft.json", draft)
        if final_dir.exists() or final_dir.is_symlink():
            raise ExtractionInputError(f"output directory already exists: {out_dir}")
        os.replace(staging_dir, final_dir)
        return draft
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
