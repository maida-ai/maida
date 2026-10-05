"""Load and project stored runs for analysis."""

from __future__ import annotations

import json
import logging

from maida._storage.errors import RunValidationError, UnsupportedTraceFormatError
from maida._storage.paths import (
    EVENTS_JSONL,
    META_JSON,
    RUN_JSON,
    SPANS_JSONL,
    _legacy_run_dir,
    _meta_path,
    _spans_path,
    _trace_dir,
    _validate_trace_id,
)
from maida._storage.resolve import resolve_run_id
from maida.config import MaidaConfig
from maida.constants import SPEC_VERSION
from maida.events import spans_to_events
from maida.schema_versions import machine_minor_compatible
from maida.trace_validation import (
    TraceInputError,
    TraceValidationError,
    format_diagnostic_problem,
    validate_trace_path,
)

logger = logging.getLogger(__name__)


def load_run_meta(trace_id: str, config: MaidaConfig) -> dict:
    """Load run metadata from meta.json."""
    try:
        path = _meta_path(trace_id, config)
    except ValueError:
        path = _legacy_run_dir(trace_id, config) / RUN_JSON
    if not path.is_file():
        legacy_path = _legacy_run_dir(trace_id, config) / RUN_JSON
        if legacy_path.is_file():
            path = legacy_path
    if not path.is_file():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_spans(trace_id: str, config: MaidaConfig) -> list[dict]:
    """Read spans.jsonl for the trace and return a list of span dicts."""
    path = _spans_path(trace_id, config)
    if not path.is_file():
        return []
    spans: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(
                    "load_spans: skipping corrupt JSONL line trace_id=%s line=%s: %s",
                    trace_id,
                    line_no,
                    e,
                )
                continue
    return spans


def load_events(run_id: str, config: MaidaConfig) -> list[dict]:
    """Compatibility wrapper: return projected OTel events or legacy events.jsonl."""
    try:
        spans = load_spans(run_id, config)
    except ValueError:
        spans = []
    if spans:
        return spans_to_events(spans)

    path = _legacy_run_dir(run_id, config) / EVENTS_JSONL
    if not path.is_file():
        return []
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(
                    "load_events: skipping corrupt JSONL line run_id=%s line=%s: %s",
                    run_id,
                    line_no,
                    e,
                )
    return events


def _load_legacy_events_jsonl(run_id: str, config: MaidaConfig) -> list[dict]:
    path = _legacy_run_dir(run_id, config) / EVENTS_JSONL
    if not path.is_file():
        return []
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                raise UnsupportedTraceFormatError(run_id, f"{EVENTS_JSONL} line {line_no} is malformed JSON")
            if not isinstance(event, dict):
                raise UnsupportedTraceFormatError(run_id, f"{EVENTS_JSONL} line {line_no} must be a JSON object")
            events.append(event)
    return events


def _normalize_legacy_events(run_id: str, events: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for idx, event in enumerate(events, start=1):
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or not event_type:
            raise UnsupportedTraceFormatError(run_id, f"{EVENTS_JSONL} event {idx} is missing event_type")

        payload = event.get("payload")
        if payload is None:
            payload = {}
        elif not isinstance(payload, dict):
            payload = {"value": payload}

        meta = event.get("meta")
        if meta is None:
            meta = {}
        elif not isinstance(meta, dict):
            meta = {"value": meta}

        normalized.append(
            {
                "spec_version": SPEC_VERSION,
                "event_id": str(event.get("event_id") or ""),
                "run_id": str(event.get("run_id") or run_id),
                "parent_id": event.get("parent_id"),
                "event_type": event_type,
                "ts": str(event.get("ts") or event.get("timestamp") or ""),
                "duration_ms": event.get("duration_ms"),
                "name": str(event.get("name") or ""),
                "payload": payload,
                "meta": meta,
            }
        )
    normalized.sort(key=lambda e: e.get("ts", ""))
    return normalized


def load_run_for_analysis(run_id: str, config: MaidaConfig) -> tuple[str, dict, list[dict]]:
    """Load a run as event-like records for baseline/diff/assert analysis.

    Current OTel traces are fully supported. Legacy ``run.json`` /
    ``events.jsonl`` directories are supported only when they declare the
    current ``spec_version`` and already contain event-shaped records.
    """
    resolved_id = resolve_run_id(run_id, config)

    try:
        current_run_dir = _trace_dir(resolved_id, config)
    except ValueError:
        current_run_dir = None
    if current_run_dir is not None and current_run_dir.is_dir():
        looks_current = (
            (current_run_dir / META_JSON).exists()
            or (current_run_dir / SPANS_JSONL).exists()
            or not (current_run_dir / RUN_JSON).exists()
        )
        if looks_current:
            meta, spans = load_validated_run(resolved_id, config)
            return resolved_id, meta, spans_to_events(spans)

    meta = load_run_meta(resolved_id, config)

    declared_spec = meta.get("spec_version")
    if not machine_minor_compatible(
        declared_spec,
        SPEC_VERSION,
        stream="trace",
        legacy=frozenset({"0.2"}),
    ):
        if declared_spec is None:
            problem = f"{RUN_JSON} is missing spec_version"
        else:
            problem = f"{RUN_JSON} declares spec_version {declared_spec!r}"
        raise UnsupportedTraceFormatError(resolved_id, problem)

    events = _load_legacy_events_jsonl(resolved_id, config)
    return resolved_id, meta, _normalize_legacy_events(resolved_id, events)


def load_validated_run(trace_id: str, config: MaidaConfig) -> tuple[dict, list[dict]]:
    """Load and strictly validate a current-format run for user-facing reads."""
    trace_id = _validate_trace_id(trace_id)
    run_dir = _trace_dir(trace_id, config)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")

    try:
        validated = validate_trace_path(run_dir)
    except TraceInputError as exc:
        raise RunValidationError(trace_id, format_diagnostic_problem(exc.diagnostic)) from exc
    except TraceValidationError as exc:
        raise RunValidationError(trace_id, format_diagnostic_problem(exc.diagnostics[0])) from exc
    if validated.trace_id != trace_id:
        raise RunValidationError(trace_id, "meta.json trace_id does not match run directory")
    return validated.meta, validated.spans
