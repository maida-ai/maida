"""Baseline snapshot creation, persistence, and shared metric extraction.

A baseline captures the structural behavior of a completed run (tool path,
event sequence, token usage, etc.) for later comparison by ``assertions.py``.

Updated for OTel-based storage: reads event-like records through the shared
run analysis loader.
"""

import json
from collections import Counter
from pathlib import Path

from maida.config import MaidaConfig
from maida.baseline_sample import (
    BASELINE_SCHEMA_VERSION,
    create_baseline_from_report,
    validate_baseline_version,
)
from maida.events import EventType
from maida.storage import load_run_for_analysis

_BASELINE_SCHEMA_VERSION = BASELINE_SCHEMA_VERSION


def extract_run_metrics(meta: dict, events: list[dict]) -> dict:
    """Extract structural metrics from run metadata and event-like dicts.

    Shared by ``create_baseline`` and ``run_assertions`` so both operate on
    identical metric derivation logic.
    """
    counts = meta.get("counts") or {}
    tool_names_ordered: list[str] = []
    tool_call_sequence: list[str] = []
    tool_counter: Counter[str] = Counter()
    llm_models: set[str] = set()
    event_type_seq: list[str] = []
    total_tokens = 0
    guardrail_events: list[dict] = []

    _action_events = [
        e
        for e in events
        if e.get("event_type")
        not in (EventType.RUN_START.value, EventType.RUN_END.value)
    ]
    for ev in events:
        et = ev.get("event_type", "")
        event_type_seq.append(et)

        if et == EventType.TOOL_CALL.value:
            name = ev.get("name", "")
            tool_call_sequence.append(name)
            tool_counter[name] += 1
            if name not in tool_names_ordered:
                tool_names_ordered.append(name)

        elif et == EventType.LLM_CALL.value:
            model = ev.get("name", "")
            if model:
                llm_models.add(model)
            payload = ev.get("payload") or {}
            usage = payload.get("usage") or {}
            tok = usage.get("total_tokens")
            if isinstance(tok, (int, float)):
                total_tokens += int(tok)

        elif et == EventType.ERROR.value:
            payload = ev.get("payload") or {}
            if "guardrail" in payload:
                guardrail_events.append(ev)
            elif payload.get("error_type") in ("GuardrailExceeded", "LoopAbort"):
                guardrail_events.append(ev)

    return {
        "summary": {
            "status": meta.get("status", ""),
            "total_events": len(_action_events),
            "llm_calls": counts.get("llm_calls", 0),
            "tool_calls": counts.get("tool_calls", 0),
            "errors": counts.get("errors", 0),
            "loop_warnings": counts.get("loop_warnings", 0),
            "duration_ms": meta.get("duration_ms", 0),
            "total_tokens": total_tokens,
        },
        "tool_path": sorted(tool_names_ordered),
        "tool_call_sequence": tool_call_sequence,
        "_tool_call_sequence_exact": True,
        "tool_call_counts": dict(tool_counter),
        "llm_models_used": sorted(llm_models),
        "event_type_sequence": event_type_seq,
        "guardrail_events": guardrail_events,
        "final_status": meta.get("status", ""),
    }


def create_baseline(trace_id: str, config: MaidaConfig) -> dict:
    """Load a completed run and return a baseline snapshot dict.

    Args:
        trace_id: The OTel trace ID (or prefix) for the run.
        config: MaidaConfig instance.
    """
    full_id, meta, events = load_run_for_analysis(trace_id, config)
    metrics = extract_run_metrics(meta, events)
    summary = metrics["summary"]
    report = {
        "report_version": "2.0.0",
        "metadata": {
            "trials_used": 1,
            "trials_budgeted": 1,
            "environment_fingerprint": None,
        },
        "trials": [
            {
                "trace_id": full_id,
                "run_name": meta.get("run_name"),
                "metric_values": {
                    "step_count": summary["total_events"],
                    "tool_call_count": summary["tool_calls"],
                    "cost_tokens": summary["total_tokens"],
                    "latency_ms": summary["duration_ms"],
                    "llm_call_count": summary["llm_calls"],
                    "error_count": summary["errors"],
                    "loop_warning_count": summary["loop_warnings"],
                },
                "invariant_outcomes": {},
                "structural_signature": {
                    "tool_path": metrics["tool_path"],
                    "tool_call_sequence": metrics["tool_call_sequence"],
                    "tool_call_counts": metrics["tool_call_counts"],
                    "llm_models_used": metrics["llm_models_used"],
                    "event_type_sequence": metrics["event_type_sequence"],
                    "final_status": metrics["final_status"],
                },
            }
        ],
    }
    baseline = create_baseline_from_report(report)
    baseline["summary"] = summary
    baseline["guardrail_events"] = metrics["guardrail_events"]
    return baseline


def save_baseline(baseline: dict, path: Path, force: bool = True) -> None:
    """Write a baseline dict to *path* as pretty-printed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not force:
        raise FileExistsError(
            f"Baseline file already exists: {path}. "
            "Cowardly refusing to overwrite without --force."
        )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(baseline, f, ensure_ascii=False, indent=2)


def load_baseline(path: Path) -> dict:
    """Read a baseline JSON file and return its contents.

    Raises ``FileNotFoundError`` if *path* does not exist or
    ``json.JSONDecodeError`` if the file is malformed.
    """
    with open(path, "r", encoding="utf-8") as f:
        baseline = json.load(f)
    if not isinstance(baseline, dict):
        raise ValueError("baseline root must be an object")
    validate_baseline_version(baseline)
    return baseline
