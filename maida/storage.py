"""
Local storage for Maida runs using OpenTelemetry span data.

Each run (trace) is stored in: <data_dir>/runs/<trace_id_hex>/
  - meta.json: run metadata (run_name, status, counts, etc.)
  - spans.jsonl: one JSON span dict per line (written by MaidaLocalSpanExporter)

Note: trace_id_hex is 32 hex characters (128-bit trace ID).
"""

import json
import logging
import os
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from maida.config import MaidaConfig
from maida.constants import SPEC_VERSION, default_counts
from maida.events import spans_to_events, utc_now_iso_ms_z

META_JSON = "meta.json"
SPANS_JSONL = "spans.jsonl"
RUN_JSON = "run.json"
EVENTS_JSONL = "events.jsonl"

logger = logging.getLogger(__name__)

_TRACE_ID_LEN = 32
_SPAN_ID_LEN = 16
_HEX_CHARS = frozenset("0123456789abcdef")
_SAFE_VERSION_RE = re.compile(r"^\d{1,4}(?:\.\d{1,4}){0,3}$")


class RunValidationError(RuntimeError):
    """A stored run exists but does not match the supported trace contract."""

    def __init__(self, trace_id: str, problem: str) -> None:
        self.trace_id = trace_id
        self.problem = problem
        short = trace_id[:8] if isinstance(trace_id, str) and trace_id else "unknown"
        super().__init__(
            f"Run validation failed for {short}: {problem}. "
            "Next step: rerun the traced agent to create a fresh run, "
            "or run `maida demo` to create a known-good local trace."
        )


class UnsupportedTraceFormatError(RuntimeError):
    """A stored run uses a trace format Maida cannot project safely."""

    def __init__(self, run_id: str, problem: str) -> None:
        self.run_id = run_id
        self.problem = problem
        short = run_id[:8] if isinstance(run_id, str) and run_id else "unknown"
        super().__init__(
            f"unsupported trace format for run {short}: {problem}. "
            "The earliest fully supported trace format is 0.2. "
            "Next step: rerun the traced agent to create a fresh trace, "
            "or run `maida demo` to create a known-good local trace."
        )


def _validate_trace_id(trace_id: str) -> str:
    """Validate that trace_id is a 32-char hex string (no path traversal)."""
    if not trace_id or not isinstance(trace_id, str):
        raise ValueError("invalid trace_id")
    t = trace_id.strip().lower()
    if len(t) != _TRACE_ID_LEN or not all(c in "0123456789abcdef" for c in t.lower()):
        raise ValueError("invalid trace_id")
    if ".." in t or "/" in t or "\\" in t:
        raise ValueError("invalid trace_id")
    return t


def _validate_span_id(span_id: str, *, field_name: str) -> str:
    if not span_id or not isinstance(span_id, str):
        raise ValueError(f"invalid {field_name}")
    sid = span_id.strip().lower()
    if len(sid) != _SPAN_ID_LEN or not all(c in "0123456789abcdef" for c in sid):
        raise ValueError(f"invalid {field_name}")
    return sid


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _runs_dir(config: MaidaConfig) -> Path:
    return config.data_dir.expanduser() / "runs"


def _trace_dir(trace_id: str, config: MaidaConfig) -> Path:
    trace_id = _validate_trace_id(trace_id)
    base = _runs_dir(config)
    path = base / trace_id
    try:
        resolved = path.resolve()
        base_resolved = base.resolve()
        if not resolved.is_relative_to(base_resolved):
            raise ValueError("invalid trace_id")
    except (ValueError, OSError):
        raise ValueError("invalid trace_id")
    return path


def _legacy_run_dir(run_id: str, config: MaidaConfig) -> Path:
    if not run_id or not isinstance(run_id, str):
        raise ValueError("invalid run_id")
    rid = run_id.strip()
    if not rid or ".." in rid or "/" in rid or "\\" in rid:
        raise ValueError("invalid run_id")
    return _runs_dir(config) / rid


def _meta_path(trace_id: str, config: MaidaConfig) -> Path:
    return _trace_dir(trace_id, config) / META_JSON


def _spans_path(trace_id: str, config: MaidaConfig) -> Path:
    return _trace_dir(trace_id, config) / SPANS_JSONL


def list_runs(limit: int, config: MaidaConfig) -> list[dict]:
    """List most recent runs by started_at descending. Returns list of meta dicts."""
    runs_base = _runs_dir(config)
    if not runs_base.is_dir():
        return []

    candidates: list[tuple[datetime | None, dict]] = []
    for entry in runs_base.iterdir():
        if not entry.is_dir():
            continue
        trace_id = entry.name
        meta_f: Path
        try:
            _validate_trace_id(trace_id)
        except ValueError:
            meta_f = entry / RUN_JSON
        else:
            meta_f = entry / META_JSON
            if not meta_f.is_file():
                legacy_meta_f = entry / RUN_JSON
                if legacy_meta_f.is_file():
                    meta_f = legacy_meta_f
        if not meta_f.is_file():
            continue
        try:
            with open(meta_f, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        started_str = meta.get("started_at")
        started_dt = _parse_iso_z(started_str) if started_str else None
        candidates.append((started_dt, meta))

    def sort_key(item: tuple[datetime | None, dict]) -> tuple[bool, datetime]:
        dt, _ = item
        return (dt is None, dt or datetime.min.replace(tzinfo=timezone.utc))

    candidates.sort(key=sort_key, reverse=True)
    return [meta for _, meta in candidates[:limit]]


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
                raise UnsupportedTraceFormatError(
                    run_id, f"{EVENTS_JSONL} line {line_no} is malformed JSON"
                )
            if not isinstance(event, dict):
                raise UnsupportedTraceFormatError(
                    run_id, f"{EVENTS_JSONL} line {line_no} must be a JSON object"
                )
            events.append(event)
    return events


def _normalize_legacy_events(run_id: str, events: list[dict]) -> list[dict]:
    normalized: list[dict] = []
    for idx, event in enumerate(events, start=1):
        event_type = event.get("event_type")
        if not isinstance(event_type, str) or not event_type:
            raise UnsupportedTraceFormatError(
                run_id, f"{EVENTS_JSONL} event {idx} is missing event_type"
            )

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


def load_run_for_analysis(
    run_id: str, config: MaidaConfig
) -> tuple[str, dict, list[dict]]:
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
    if declared_spec != SPEC_VERSION:
        if declared_spec is None:
            problem = f"{RUN_JSON} is missing spec_version"
        else:
            problem = f"{RUN_JSON} declares spec_version {declared_spec!r}"
        raise UnsupportedTraceFormatError(resolved_id, problem)

    events = _load_legacy_events_jsonl(resolved_id, config)
    return resolved_id, meta, _normalize_legacy_events(resolved_id, events)


def append_event(run_id: str, event: dict, config: MaidaConfig) -> None:
    """Compatibility wrapper for legacy event append callers."""
    path = _legacy_run_dir(run_id, config) / EVENTS_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def create_run(run_name: str, config: MaidaConfig) -> dict:
    """Compatibility wrapper for legacy run.json callers."""
    run_id = str(uuid.uuid4())
    run_dir = _legacy_run_dir(run_id, config)
    meta = {
        "spec_version": SPEC_VERSION,
        "run_id": run_id,
        "run_name": run_name,
        "started_at": utc_now_iso_ms_z(),
        "ended_at": None,
        "duration_ms": None,
        "status": "running",
        "counts": default_counts(),
        "last_event_ts": None,
        "paths": {
            "run_json": str(run_dir / RUN_JSON),
            "events_jsonl": str(run_dir / EVENTS_JSONL),
        },
    }
    _atomic_write_json(run_dir / RUN_JSON, meta)
    (run_dir / EVENTS_JSONL).touch()
    return meta


def finalize_run(
    run_id: str, status: str, counts: dict[str, int], config: MaidaConfig
) -> dict:
    """Compatibility wrapper for legacy run finalization callers."""
    path = _legacy_run_dir(run_id, config) / RUN_JSON
    if not path.is_file():
        raise FileNotFoundError(f"No run found for run_id '{run_id}'")
    with open(path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    ended_at = utc_now_iso_ms_z()
    started_dt = _parse_iso_z(meta.get("started_at"))
    ended_dt = _parse_iso_z(ended_at)
    duration_ms = None
    if started_dt and ended_dt:
        duration_ms = max(0, int((ended_dt - started_dt).total_seconds() * 1000))
    meta.update(
        {
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "status": status,
            "counts": counts,
        }
    )
    _atomic_write_json(path, meta)
    return meta


def _trace_candidates(
    config: MaidaConfig, prefix: str | None = None
) -> list[tuple[datetime | None, str]]:
    """Collect (started_at, trace_id) for runs, newest first.

    When *prefix* is given, only trace IDs matching it are included.
    Raises FileNotFoundError if the runs directory does not exist.
    """
    runs_base = _runs_dir(config)
    if not runs_base.is_dir():
        raise FileNotFoundError(f"No runs directory at {runs_base}")

    candidates: list[tuple[datetime | None, str]] = []
    for entry in runs_base.iterdir():
        if not entry.is_dir():
            continue
        tid = entry.name
        try:
            _validate_trace_id(tid)
        except ValueError:
            continue
        if prefix is not None and tid != prefix and not tid.startswith(prefix):
            continue
        meta_f = entry / META_JSON
        if not meta_f.is_file():
            continue
        try:
            with open(meta_f, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        started_str = meta.get("started_at")
        started_dt = _parse_iso_z(started_str) if started_str else None
        candidates.append((started_dt, tid))

    def sort_key(item: tuple[datetime | None, str]) -> tuple[bool, datetime]:
        dt, _ = item
        return (dt is None, dt or datetime.min.replace(tzinfo=timezone.utc))

    candidates.sort(key=sort_key, reverse=True)
    return candidates


def resolve_trace_id(prefix: str, config: MaidaConfig) -> str:
    """Resolve a trace_id prefix (short or full) to the full 32-char hex trace_id.

    Raises FileNotFoundError if no match.
    """
    if not prefix or not prefix.strip():
        raise FileNotFoundError("Trace ID is required")
    prefix = prefix.strip().lower()
    if ".." in prefix or "/" in prefix or "\\" in prefix:
        raise FileNotFoundError("Trace ID is required")
    candidates = _trace_candidates(config, prefix)
    if not candidates:
        raise FileNotFoundError(f"No run found matching '{prefix}'")
    return candidates[0][1]


def resolve_trace_id_for_read(prefix: str, config: MaidaConfig) -> str:
    """Resolve a trace ID for strict read paths, including incomplete run dirs.

    Normal prefix resolution is metadata-driven. For explicit full trace IDs, keep
    a directory that exists but is missing/corrupt metadata so validation can
    report the real format problem instead of a generic not-found error.
    """
    try:
        return resolve_trace_id(prefix, config)
    except FileNotFoundError as original:
        raw = (prefix or "").strip().lower()
        try:
            trace_id = _validate_trace_id(raw)
        except ValueError:
            raise original
        try:
            run_dir = _trace_dir(trace_id, config)
        except ValueError:
            raise original
        if run_dir.is_dir():
            return trace_id
        raise original


def resolve_latest_trace_id(config: MaidaConfig) -> str:
    """Return the trace_id of the most recently started run.

    Raises FileNotFoundError if there are no runs yet.
    """
    try:
        candidates = _trace_candidates(config)
    except FileNotFoundError:
        candidates = []
    if not candidates:
        raise FileNotFoundError(
            "No runs found. Run your traced agent first (or try `maida demo`)."
        )
    return candidates[0][1]


def resolve_latest_run_id(config: MaidaConfig) -> str:
    """Return the most recent run identifier, whether current trace or legacy run."""
    runs = list_runs(limit=1, config=config)
    if not runs:
        raise FileNotFoundError(
            "No runs found. Run your traced agent first (or try `maida demo`)."
        )
    run_id = runs[0].get("trace_id") or runs[0].get("run_id")
    if not run_id:
        raise FileNotFoundError(
            "No runs found. Run your traced agent first (or try `maida demo`)."
        )
    return str(run_id)


def _safe_version_display(value: object) -> str:
    text = str(value)
    return text if _SAFE_VERSION_RE.fullmatch(text) else "<redacted>"


def _run_validation_error(trace_id: str, problem: str) -> RunValidationError:
    return RunValidationError(trace_id, problem)


def _read_json_file_for_validation(path: Path, trace_id: str, display: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except json.JSONDecodeError:
        raise _run_validation_error(trace_id, f"{display} is malformed JSON")
    except OSError:
        raise _run_validation_error(trace_id, f"{display} could not be read")
    if not isinstance(payload, dict):
        raise _run_validation_error(trace_id, f"{display} must contain a JSON object")
    return payload


def _validate_counts(trace_id: str, counts: object) -> None:
    if not isinstance(counts, dict):
        raise _run_validation_error(
            trace_id, "meta.json field 'counts' must be an object"
        )
    for key in ("llm_calls", "tool_calls", "errors", "loop_warnings"):
        value = counts.get(key)
        if not isinstance(value, int) or value < 0:
            raise _run_validation_error(
                trace_id,
                f"meta.json counts.{key} must be a non-negative integer",
            )


def _validate_meta(trace_id: str, meta: dict) -> None:
    declared_spec = meta.get("spec_version")
    if declared_spec is not None and declared_spec != SPEC_VERSION:
        raise _run_validation_error(
            trace_id,
            "meta.json declares unsupported spec_version "
            f"{_safe_version_display(declared_spec)!r}; expected {SPEC_VERSION!r}",
        )

    required = (
        "trace_id",
        "run_name",
        "started_at",
        "ended_at",
        "duration_ms",
        "status",
        "counts",
    )
    for field in required:
        if field not in meta:
            raise _run_validation_error(
                trace_id, f"meta.json is missing field {field!r}"
            )

    if meta.get("trace_id") != trace_id:
        raise _run_validation_error(
            trace_id, "meta.json trace_id does not match run directory"
        )
    if meta.get("run_name") is not None and not isinstance(meta.get("run_name"), str):
        raise _run_validation_error(
            trace_id, "meta.json field 'run_name' must be a string or null"
        )
    if not isinstance(meta.get("started_at"), str):
        raise _run_validation_error(
            trace_id, "meta.json field 'started_at' must be a string"
        )
    if meta.get("ended_at") is not None and not isinstance(meta.get("ended_at"), str):
        raise _run_validation_error(
            trace_id, "meta.json field 'ended_at' must be a string or null"
        )
    duration = meta.get("duration_ms")
    if duration is not None and (not isinstance(duration, int) or duration < 0):
        raise _run_validation_error(
            trace_id,
            "meta.json field 'duration_ms' must be a non-negative integer or null",
        )
    if meta.get("status") not in ("running", "ok", "error"):
        raise _run_validation_error(
            trace_id, "meta.json field 'status' must be running, ok, or error"
        )
    _validate_counts(trace_id, meta.get("counts"))


def _load_spans_for_validation(path: Path, trace_id: str) -> list[dict]:
    spans: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if not line.strip():
                    continue
                try:
                    span = json.loads(line)
                except json.JSONDecodeError:
                    raise _run_validation_error(
                        trace_id,
                        f"spans.jsonl line {line_no} is malformed JSON",
                    )
                if not isinstance(span, dict):
                    raise _run_validation_error(
                        trace_id,
                        f"spans.jsonl line {line_no} must contain a JSON object",
                    )
                spans.append(span)
    except OSError:
        raise _run_validation_error(trace_id, "spans.jsonl could not be read")
    if not spans:
        raise _run_validation_error(trace_id, "spans.jsonl contains no spans")
    return spans


def _validate_span_events(trace_id: str, line_no: int, events: object) -> None:
    if not isinstance(events, list):
        raise _run_validation_error(
            trace_id, f"spans.jsonl line {line_no} field 'events' must be an array"
        )
    for idx, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            raise _run_validation_error(
                trace_id,
                f"spans.jsonl line {line_no} event {idx} must be an object",
            )
        for field in ("name", "timestamp", "attributes"):
            if field not in event:
                raise _run_validation_error(
                    trace_id,
                    f"spans.jsonl line {line_no} event {idx} is missing field {field!r}",
                )
        if not isinstance(event.get("attributes"), dict):
            raise _run_validation_error(
                trace_id,
                f"spans.jsonl line {line_no} event {idx} field 'attributes' must be an object",
            )


def _require_span_string_field(
    trace_id: str, line_no: int, span: dict, field: str
) -> None:
    if not isinstance(span.get(field), str):
        raise _run_validation_error(
            trace_id, f"spans.jsonl line {line_no} field {field!r} must be a string"
        )


def _require_optional_span_string_field(
    trace_id: str, line_no: int, span: dict, field: str
) -> None:
    value = span.get(field)
    if value is not None and not isinstance(value, str):
        raise _run_validation_error(
            trace_id,
            f"spans.jsonl line {line_no} field {field!r} must be a string or null",
        )


def _require_optional_span_non_negative_int_field(
    trace_id: str, line_no: int, span: dict, field: str
) -> None:
    value = span.get(field)
    if value is not None and (not isinstance(value, int) or value < 0):
        raise _run_validation_error(
            trace_id,
            f"spans.jsonl line {line_no} field {field!r} must be a non-negative integer or null",
        )


def _require_span_object_field(
    trace_id: str, line_no: int, span: dict, field: str
) -> None:
    if not isinstance(span.get(field), dict):
        raise _run_validation_error(
            trace_id, f"spans.jsonl line {line_no} field {field!r} must be an object"
        )


def _validate_spans(
    trace_id: str, spans: list[dict], *, require_root_span: bool
) -> None:
    root_count = 0
    required = (
        "trace_id",
        "span_id",
        "parent_span_id",
        "name",
        "kind",
        "start_time",
        "end_time",
        "duration_ms",
        "attributes",
        "events",
        "status_code",
        "status_description",
    )
    for line_no, span in enumerate(spans, start=1):
        declared_spec = span.get("spec_version")
        if declared_spec is not None and declared_spec != SPEC_VERSION:
            raise _run_validation_error(
                trace_id,
                f"spans.jsonl line {line_no} declares unsupported spec_version "
                f"{_safe_version_display(declared_spec)!r}; expected {SPEC_VERSION!r}",
            )
        for field in required:
            if field not in span:
                raise _run_validation_error(
                    trace_id, f"spans.jsonl line {line_no} is missing field {field!r}"
                )
        if span.get("trace_id") != trace_id:
            raise _run_validation_error(
                trace_id,
                f"spans.jsonl line {line_no} trace_id does not match run directory",
            )
        try:
            _validate_span_id(span.get("span_id"), field_name="span_id")
        except ValueError:
            raise _run_validation_error(
                trace_id, f"spans.jsonl line {line_no} has an invalid span_id"
            )
        parent_span_id = span.get("parent_span_id")
        if parent_span_id is None:
            root_count += 1
        else:
            try:
                _validate_span_id(parent_span_id, field_name="parent_span_id")
            except ValueError:
                raise _run_validation_error(
                    trace_id,
                    f"spans.jsonl line {line_no} has an invalid parent_span_id",
                )
        _require_span_string_field(trace_id, line_no, span, "name")
        _require_span_string_field(trace_id, line_no, span, "kind")
        _require_span_string_field(trace_id, line_no, span, "start_time")
        _require_optional_span_string_field(trace_id, line_no, span, "end_time")
        _require_optional_span_non_negative_int_field(
            trace_id, line_no, span, "duration_ms"
        )
        _require_span_object_field(trace_id, line_no, span, "attributes")
        _validate_span_events(trace_id, line_no, span.get("events"))
        if span.get("status_code") not in ("OK", "ERROR", "UNSET"):
            raise _run_validation_error(
                trace_id,
                f"spans.jsonl line {line_no} field 'status_code' must be OK, ERROR, or UNSET",
            )
        _require_span_string_field(trace_id, line_no, span, "status_description")
    if require_root_span and root_count == 0:
        raise _run_validation_error(trace_id, "spans.jsonl has no root span")


def load_validated_run(trace_id: str, config: MaidaConfig) -> tuple[dict, list[dict]]:
    """Load and strictly validate a current-format run for user-facing reads."""
    trace_id = _validate_trace_id(trace_id)
    run_dir = _trace_dir(trace_id, config)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")

    meta_path = run_dir / META_JSON
    spans_path = run_dir / SPANS_JSONL
    if not meta_path.is_file():
        raise _run_validation_error(trace_id, "required file meta.json is missing")
    if not spans_path.is_file():
        raise _run_validation_error(trace_id, "required file spans.jsonl is missing")

    meta = _read_json_file_for_validation(meta_path, trace_id, META_JSON)
    _validate_meta(trace_id, meta)
    spans = _load_spans_for_validation(spans_path, trace_id)
    _validate_spans(trace_id, spans, require_root_span=meta.get("status") != "running")
    return meta, spans


def resolve_run_id(prefix: str, config: MaidaConfig) -> str:
    """Compatibility wrapper for legacy run IDs and new trace IDs."""
    if not prefix or not prefix.strip():
        raise FileNotFoundError("Run ID is required")
    prefix = prefix.strip()
    if ".." in prefix or "/" in prefix or "\\" in prefix:
        raise FileNotFoundError("Run ID is required")
    try:
        return resolve_trace_id_for_read(prefix, config)
    except FileNotFoundError:
        pass

    runs_base = _runs_dir(config)
    if not runs_base.is_dir():
        raise FileNotFoundError(f"No runs directory at {runs_base}")

    candidates: list[tuple[datetime | None, str]] = []
    for entry in runs_base.iterdir():
        if not entry.is_dir():
            continue
        rid = entry.name
        if rid != prefix and not rid.startswith(prefix):
            continue
        meta_f = entry / RUN_JSON
        if not meta_f.is_file():
            continue
        try:
            with open(meta_f, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        started_str = meta.get("started_at")
        started_dt = _parse_iso_z(started_str) if started_str else None
        candidates.append((started_dt, rid))

    if not candidates:
        raise FileNotFoundError(f"No run found matching '{prefix}'")

    def sort_key(item: tuple[datetime | None, str]) -> tuple[bool, datetime]:
        dt, _ = item
        return (dt is None, dt or datetime.min.replace(tzinfo=timezone.utc))

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0][1]


def _parse_iso_z(s: str) -> datetime | None:
    """Parse ISO8601 UTC timestamp with optional trailing Z."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    try:
        normalized = s.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None


def get_run_paths(trace_id: str, config: MaidaConfig) -> dict:
    """Return local filesystem paths for a run."""
    trace_dir = _trace_dir(trace_id, config)
    meta_p = trace_dir / META_JSON
    spans_p = trace_dir / SPANS_JSONL
    if not meta_p.is_file():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    return {
        "run_dir": str(trace_dir),
        "meta_json": str(meta_p),
        "spans_jsonl": str(spans_p),
    }


def rename_run(trace_id: str, run_name: str, config: MaidaConfig) -> dict:
    """Update meta.json with a new run_name."""
    path = _meta_path(trace_id, config)
    if not path.is_file():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    new_name = (run_name or "").strip()
    if not new_name:
        raise ValueError("run_name must be non-empty")
    with open(path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["run_name"] = new_name
    _atomic_write_json(path, meta)
    return meta


def delete_run(trace_id: str, config: MaidaConfig) -> None:
    """Delete a run directory and all its contents."""
    run_dir = _trace_dir(trace_id, config)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    shutil.rmtree(run_dir)
