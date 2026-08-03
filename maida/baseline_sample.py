"""Immutable multi-trial baseline sample construction and compatibility."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from maida.events import utc_now_iso_ms_z
from maida.schema_versions import BASELINE_SCHEMA_VERSION

LEGACY_BASELINE_VERSIONS = frozenset({"0.2", "0.2.0"})


def _signature_id(signature: dict[str, Any]) -> str:
    encoded = json.dumps(
        signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_baseline_from_report(report: dict[str, Any]) -> dict[str, Any]:
    """Create one reviewed, immutable baseline trial sample from report v2."""
    if report.get("report_version") != "2.0.0":
        raise ValueError("baseline --from-report requires report_version 2.0.0")
    trials = report.get("trials")
    if not isinstance(trials, list) or not trials:
        raise ValueError("report must contain at least one completed trial")
    metadata = report.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError("report metadata must be an object")
    if metadata.get("trials_used") != metadata.get("trials_budgeted"):
        raise ValueError(
            "baseline reports must contain the full fixed-N sample; "
            "re-run with --no-fail-fast"
        )

    metric_vectors: dict[str, list[float]] = {}
    invariant_vectors: dict[str, list[bool]] = {}
    signatures: dict[str, dict[str, Any]] = {}
    signature_order: list[str] = []
    for trial in trials:
        values = trial.get("metric_values")
        signature = trial.get("structural_signature")
        invariants = trial.get("invariant_outcomes")
        if not isinstance(values, dict) or not isinstance(signature, dict):
            raise ValueError(
                "report trials must include metric_values and structural_signature"
            )
        if not isinstance(invariants, dict):
            raise ValueError("report trials must include invariant_outcomes")
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"trial metric {name} must be numeric")
            metric_vectors.setdefault(name, []).append(float(value))
        for name, value in invariants.items():
            if not isinstance(value, bool):
                raise ValueError(f"trial invariant {name} must be boolean")
            invariant_vectors.setdefault(name, []).append(value)
        identifier = _signature_id(signature)
        signatures.setdefault(identifier, signature)
        signature_order.append(identifier)

    counts = Counter(signature_order)
    first = trials[0]
    first_signature = first["structural_signature"]
    first_values = first["metric_values"]
    summary = {
        "status": first_signature.get("final_status", ""),
        "total_events": first_values.get("step_count", 0),
        "tool_calls": first_values.get("tool_call_count", 0),
        "duration_ms": first_values.get("latency_ms", 0),
        "total_tokens": first_values.get("cost_tokens", 0),
        "llm_calls": first_values.get("llm_call_count", 0),
        "errors": first_values.get("error_count", 0),
        "loop_warnings": first_values.get("loop_warning_count", 0),
    }
    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "created_at": utc_now_iso_ms_z(),
        "source_run_id": first.get("trace_id"),
        "source_run_ids": [trial.get("trace_id") for trial in trials],
        "source_run_name": first.get("run_name"),
        "summary": summary,
        "tool_path": first_signature.get("tool_path", []),
        "tool_call_sequence": first_signature.get("tool_call_sequence", []),
        "tool_call_counts": first_signature.get("tool_call_counts", {}),
        "llm_models_used": first_signature.get("llm_models_used", []),
        "event_type_sequence": first_signature.get("event_type_sequence", []),
        "final_status": first_signature.get("final_status", ""),
        "trial_sample": {
            "trials": len(trials),
            "environment_fingerprint": metadata.get("environment_fingerprint"),
            "metrics": metric_vectors,
            "invariants": invariant_vectors,
            "signature_ids": signature_order,
            "signature_counts": dict(sorted(counts.items())),
            "signatures": signatures,
        },
    }


def validate_baseline_version(baseline: dict[str, Any]) -> None:
    """Accept legacy 0.2 and current 0.3.x machine-written baselines."""
    version = baseline.get("schema_version")
    if version in LEGACY_BASELINE_VERSIONS:
        return
    if not isinstance(version, str):
        raise ValueError("baseline schema_version must be a semantic version")
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError("baseline schema_version must use major.minor.patch form")
    major, minor, _patch = (int(part) for part in parts)
    if (major, minor) != (0, 3):
        raise ValueError(
            f"unsupported baseline schema_version {version}; "
            f"this Maida supports {BASELINE_SCHEMA_VERSION}"
        )
