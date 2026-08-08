"""Validation for externally emitted Maida trace directories.

The public trace contract is the native two-file layout: ``meta.json`` plus
``spans.jsonl``. This module has no storage or CLI side effects so file-backed
validation, imports, and stored-run reads can share one contract.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from maida.constants import SPEC_VERSION
from maida.schema_versions import machine_minor_compatible


META_JSON = "meta.json"
SPANS_JSONL = "spans.jsonl"

_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_SAFE_VERSION_RE = re.compile(r"^\d{1,4}(?:\.\d{1,4}){0,3}$")
_RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$",
    re.IGNORECASE,
)
_SPAN_KINDS = frozenset({"INTERNAL", "CLIENT", "SERVER", "PRODUCER", "CONSUMER"})
_STATUS_CODES = frozenset({"OK", "ERROR", "UNSET"})
_RUN_STATUSES = frozenset({"running", "ok", "error"})


@dataclass(frozen=True)
class TraceDiagnostic:
    """One sanitized, machine-readable trace validation problem."""

    code: str
    location: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "location": self.location,
            "message": self.message,
        }


class TraceInputError(ValueError):
    """The requested trace path cannot be interpreted or read."""

    def __init__(self, diagnostic: TraceDiagnostic) -> None:
        self.diagnostic = diagnostic
        super().__init__(diagnostic.message)


class TraceValidationError(ValueError):
    """Trace files were read, but their content violates the public contract."""

    def __init__(
        self,
        diagnostics: list[TraceDiagnostic],
        *,
        trace_id: str | None = None,
        spec_version: str | None = None,
        status: str | None = None,
        span_count: int | None = None,
    ) -> None:
        self.diagnostics = tuple(diagnostics)
        self.trace_id = trace_id
        self.spec_version = spec_version
        self.status = status
        self.span_count = span_count
        summary = "; ".join(
            f"{item.location}: {item.message}" for item in self.diagnostics
        )
        super().__init__(summary or "trace content is invalid")


@dataclass(frozen=True)
class ValidatedTrace:
    """A parsed trace that conforms to the current native trace contract."""

    meta: dict[str, Any]
    spans: list[dict[str, Any]]
    source_dir: Path | None = None

    @property
    def trace_id(self) -> str:
        return str(self.meta["trace_id"])

    @property
    def spec_version(self) -> str:
        return str(self.meta["spec_version"])

    @property
    def status(self) -> str:
        return str(self.meta["status"])


def _diagnostic(code: str, location: str, message: str) -> TraceDiagnostic:
    return TraceDiagnostic(code=code, location=location, message=message)


def _safe_version_display(value: object) -> str:
    text = str(value)
    return text if _SAFE_VERSION_RE.fullmatch(text) else "<redacted>"


def _valid_datetime(value: object) -> bool:
    if not isinstance(value, str) or _RFC3339_RE.fullmatch(value) is None:
        return False
    candidate = value[:-1] + "+00:00" if value[-1].lower() == "z" else value
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _valid_non_negative_int(value: object, *, nullable: bool) -> bool:
    if value is None:
        return nullable
    return type(value) is int and value >= 0


def _partial_string(
    meta: dict[str, Any], field: str, pattern: re.Pattern[str] | None = None
) -> str | None:
    value = meta.get(field)
    if not isinstance(value, str):
        return None
    if pattern is not None and pattern.fullmatch(value) is None:
        return None
    return value


def _require_fields(
    value: dict[str, Any],
    required: tuple[str, ...],
    *,
    location: str,
    diagnostics: list[TraceDiagnostic],
) -> None:
    for field in required:
        if field not in value:
            diagnostics.append(
                _diagnostic(
                    "missing_field",
                    location,
                    f"is missing field {field!r}",
                )
            )


def _validate_meta(meta: dict[str, Any], diagnostics: list[TraceDiagnostic]) -> None:
    required = (
        "spec_version",
        "trace_id",
        "run_name",
        "started_at",
        "ended_at",
        "duration_ms",
        "status",
        "counts",
    )
    _require_fields(meta, required, location=META_JSON, diagnostics=diagnostics)

    if "spec_version" in meta:
        declared = meta["spec_version"]
        if not isinstance(declared, str):
            diagnostics.append(
                _diagnostic(
                    "invalid_type",
                    f"{META_JSON}.spec_version",
                    "must be a semantic version string",
                )
            )
        elif not machine_minor_compatible(
            declared,
            SPEC_VERSION,
            stream="trace",
            legacy=frozenset({"0.2"}),
        ):
            diagnostics.append(
                _diagnostic(
                    "unsupported_version",
                    f"{META_JSON}.spec_version",
                    "declares unsupported spec_version "
                    f"{_safe_version_display(declared)!r}; expected {SPEC_VERSION!r}",
                )
            )

    if "trace_id" in meta and (
        not isinstance(meta["trace_id"], str)
        or _TRACE_ID_RE.fullmatch(meta["trace_id"]) is None
    ):
        diagnostics.append(
            _diagnostic(
                "invalid_id",
                f"{META_JSON}.trace_id",
                "must be 32 lowercase hexadecimal characters",
            )
        )

    run_name = meta.get("run_name")
    if "run_name" in meta and run_name is not None and not isinstance(run_name, str):
        diagnostics.append(
            _diagnostic(
                "invalid_type",
                f"{META_JSON}.run_name",
                "must be a string or null",
            )
        )

    if "started_at" in meta and not _valid_datetime(meta.get("started_at")):
        diagnostics.append(
            _diagnostic(
                "invalid_datetime",
                f"{META_JSON}.started_at",
                "must be an RFC 3339 date-time with a timezone",
            )
        )
    ended_at = meta.get("ended_at")
    if "ended_at" in meta and ended_at is not None and not _valid_datetime(ended_at):
        diagnostics.append(
            _diagnostic(
                "invalid_datetime",
                f"{META_JSON}.ended_at",
                "must be an RFC 3339 date-time or null",
            )
        )
    if "duration_ms" in meta and not _valid_non_negative_int(
        meta.get("duration_ms"), nullable=True
    ):
        diagnostics.append(
            _diagnostic(
                "invalid_value",
                f"{META_JSON}.duration_ms",
                "must be a non-negative integer or null",
            )
        )
    if "status" in meta and meta.get("status") not in _RUN_STATUSES:
        diagnostics.append(
            _diagnostic(
                "invalid_value",
                f"{META_JSON}.status",
                "must be running, ok, or error",
            )
        )

    if "counts" in meta:
        counts = meta.get("counts")
        if not isinstance(counts, dict):
            diagnostics.append(
                _diagnostic(
                    "invalid_type",
                    f"{META_JSON}.counts",
                    "must be an object",
                )
            )
        else:
            count_fields = ("llm_calls", "tool_calls", "errors", "loop_warnings")
            _require_fields(
                counts,
                count_fields,
                location=f"{META_JSON}.counts",
                diagnostics=diagnostics,
            )
            for field in count_fields:
                if field in counts and not _valid_non_negative_int(
                    counts[field], nullable=False
                ):
                    diagnostics.append(
                        _diagnostic(
                            "invalid_value",
                            f"{META_JSON}.counts.{field}",
                            "must be a non-negative integer",
                        )
                    )


def _validate_event(
    event: object,
    *,
    line_no: int,
    event_no: int,
    diagnostics: list[TraceDiagnostic],
) -> None:
    location = f"{SPANS_JSONL}:{line_no}.events[{event_no}]"
    if not isinstance(event, dict):
        diagnostics.append(_diagnostic("invalid_type", location, "must be an object"))
        return
    _require_fields(
        event,
        ("name", "timestamp", "attributes"),
        location=location,
        diagnostics=diagnostics,
    )
    if "name" in event and not isinstance(event.get("name"), str):
        diagnostics.append(
            _diagnostic("invalid_type", f"{location}.name", "must be a string")
        )
    if "timestamp" in event and not _valid_datetime(event.get("timestamp")):
        diagnostics.append(
            _diagnostic(
                "invalid_datetime",
                f"{location}.timestamp",
                "must be an RFC 3339 date-time with a timezone",
            )
        )
    if "attributes" in event and not isinstance(event.get("attributes"), dict):
        diagnostics.append(
            _diagnostic("invalid_type", f"{location}.attributes", "must be an object")
        )


def _validate_spans(
    spans: list[dict[str, Any]],
    *,
    meta_trace_id: str | None,
    status: str | None,
    diagnostics: list[TraceDiagnostic],
) -> None:
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
    span_line_by_id: dict[str, int] = {}
    parent_by_id: dict[str, str | None] = {}
    root_lines: list[int] = []

    for line_no, span in enumerate(spans, start=1):
        location = f"{SPANS_JSONL}:{line_no}"
        _require_fields(span, required, location=location, diagnostics=diagnostics)

        trace_id = span.get("trace_id")
        if "trace_id" in span:
            if (
                not isinstance(trace_id, str)
                or _TRACE_ID_RE.fullmatch(trace_id) is None
            ):
                diagnostics.append(
                    _diagnostic(
                        "invalid_id",
                        f"{location}.trace_id",
                        "must be 32 lowercase hexadecimal characters",
                    )
                )
            elif meta_trace_id is not None and trace_id != meta_trace_id:
                diagnostics.append(
                    _diagnostic(
                        "trace_id_mismatch",
                        f"{location}.trace_id",
                        "does not match meta.json trace_id",
                    )
                )

        span_id = span.get("span_id")
        valid_span_id = isinstance(span_id, str) and _SPAN_ID_RE.fullmatch(span_id)
        if "span_id" in span and not valid_span_id:
            diagnostics.append(
                _diagnostic(
                    "invalid_id",
                    f"{location}.span_id",
                    "must be 16 lowercase hexadecimal characters",
                )
            )
        if valid_span_id:
            assert isinstance(span_id, str)
            if span_id in span_line_by_id:
                diagnostics.append(
                    _diagnostic(
                        "duplicate_span_id",
                        f"{location}.span_id",
                        "duplicates an earlier span_id",
                    )
                )
            else:
                span_line_by_id[span_id] = line_no

        parent = span.get("parent_span_id")
        valid_parent = parent is None or (
            isinstance(parent, str) and _SPAN_ID_RE.fullmatch(parent) is not None
        )
        if "parent_span_id" in span and not valid_parent:
            diagnostics.append(
                _diagnostic(
                    "invalid_id",
                    f"{location}.parent_span_id",
                    "must be 16 lowercase hexadecimal characters or null",
                )
            )
        if parent is None and "parent_span_id" in span:
            root_lines.append(line_no)
        if valid_span_id and span_id not in parent_by_id and valid_parent:
            assert isinstance(span_id, str)
            parent_by_id[span_id] = parent

        if "name" in span and not isinstance(span.get("name"), str):
            diagnostics.append(
                _diagnostic("invalid_type", f"{location}.name", "must be a string")
            )
        if "kind" in span and span.get("kind") not in _SPAN_KINDS:
            diagnostics.append(
                _diagnostic(
                    "invalid_value",
                    f"{location}.kind",
                    "must be INTERNAL, CLIENT, SERVER, PRODUCER, or CONSUMER",
                )
            )
        if "start_time" in span and not _valid_datetime(span.get("start_time")):
            diagnostics.append(
                _diagnostic(
                    "invalid_datetime",
                    f"{location}.start_time",
                    "must be an RFC 3339 date-time with a timezone",
                )
            )
        end_time = span.get("end_time")
        if (
            "end_time" in span
            and end_time is not None
            and not _valid_datetime(end_time)
        ):
            diagnostics.append(
                _diagnostic(
                    "invalid_datetime",
                    f"{location}.end_time",
                    "must be an RFC 3339 date-time or null",
                )
            )
        if "duration_ms" in span and not _valid_non_negative_int(
            span.get("duration_ms"), nullable=True
        ):
            diagnostics.append(
                _diagnostic(
                    "invalid_value",
                    f"{location}.duration_ms",
                    "must be a non-negative integer or null",
                )
            )
        if "attributes" in span and not isinstance(span.get("attributes"), dict):
            diagnostics.append(
                _diagnostic(
                    "invalid_type", f"{location}.attributes", "must be an object"
                )
            )
        if "events" in span:
            events = span.get("events")
            if not isinstance(events, list):
                diagnostics.append(
                    _diagnostic(
                        "invalid_type", f"{location}.events", "must be an array"
                    )
                )
            else:
                for event_no, event in enumerate(events):
                    _validate_event(
                        event,
                        line_no=line_no,
                        event_no=event_no,
                        diagnostics=diagnostics,
                    )
        if "status_code" in span and span.get("status_code") not in _STATUS_CODES:
            diagnostics.append(
                _diagnostic(
                    "invalid_value",
                    f"{location}.status_code",
                    "must be OK, ERROR, or UNSET",
                )
            )
        if "status_description" in span and not isinstance(
            span.get("status_description"), str
        ):
            diagnostics.append(
                _diagnostic(
                    "invalid_type",
                    f"{location}.status_description",
                    "must be a string",
                )
            )

    completed = status in {"ok", "error"}
    if completed and not root_lines:
        diagnostics.append(
            _diagnostic("missing_root", SPANS_JSONL, "contains no root span")
        )
    if len(root_lines) > 1:
        diagnostics.append(
            _diagnostic(
                "multiple_roots",
                f"{SPANS_JSONL}:{root_lines[1]}.parent_span_id",
                "defines more than one root span",
            )
        )

    if completed:
        for span_id, parent in parent_by_id.items():
            if parent is not None and parent not in span_line_by_id:
                diagnostics.append(
                    _diagnostic(
                        "missing_parent",
                        f"{SPANS_JSONL}:{span_line_by_id[span_id]}.parent_span_id",
                        "references a parent_span_id not present in this trace",
                    )
                )

    inspected: set[str] = set()
    for start in parent_by_id:
        if start in inspected:
            continue
        ordered: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in parent_by_id:
            if current in positions:
                cycle = ordered[positions[current] :]
                first = min(cycle, key=lambda item: span_line_by_id[item])
                diagnostics.append(
                    _diagnostic(
                        "parent_cycle",
                        f"{SPANS_JSONL}:{span_line_by_id[first]}.parent_span_id",
                        "participates in a parent_span_id cycle",
                    )
                )
                break
            if current in inspected:
                break
            positions[current] = len(ordered)
            ordered.append(current)
            parent = parent_by_id[current]
            current = parent if isinstance(parent, str) else None
        inspected.update(ordered)


def validate_trace_payload(
    meta: dict[str, Any],
    spans: list[dict[str, Any]],
    *,
    source_dir: Path | None = None,
) -> ValidatedTrace:
    """Validate already-parsed native trace data without writing it."""
    diagnostics: list[TraceDiagnostic] = []
    if not isinstance(meta, dict):
        diagnostics.append(
            _diagnostic("invalid_type", META_JSON, "must contain a JSON object")
        )
        meta = {}
    if not isinstance(spans, list) or not spans:
        diagnostics.append(_diagnostic("empty_spans", SPANS_JSONL, "contains no spans"))
        spans = []

    _validate_meta(meta, diagnostics)
    meta_trace_id = _partial_string(meta, "trace_id", _TRACE_ID_RE)
    status = _partial_string(meta, "status")
    if spans:
        _validate_spans(
            spans,
            meta_trace_id=meta_trace_id,
            status=status,
            diagnostics=diagnostics,
        )

    spec_version = _partial_string(meta, "spec_version")
    safe_spec_version = (
        spec_version
        if spec_version is not None and _SAFE_VERSION_RE.fullmatch(spec_version)
        else None
    )
    safe_status = status if status in _RUN_STATUSES else None
    if diagnostics:
        raise TraceValidationError(
            diagnostics,
            trace_id=meta_trace_id,
            spec_version=safe_spec_version,
            status=safe_status,
            span_count=len(spans),
        )
    return ValidatedTrace(meta=meta, spans=spans, source_dir=source_dir)


def _resolve_trace_directory(path: Path) -> Path:
    candidate = path.expanduser()
    if not candidate.exists():
        raise TraceInputError(
            _diagnostic("path_not_found", "trace", "trace path does not exist")
        )
    if candidate.is_dir():
        run_dir = candidate
    elif candidate.is_file() and candidate.name == META_JSON:
        run_dir = candidate.parent
    else:
        raise TraceInputError(
            _diagnostic(
                "unsupported_path",
                "trace",
                "trace path must be a run directory or meta.json",
            )
        )
    for filename in (META_JSON, SPANS_JSONL):
        required = run_dir / filename
        if not required.is_file():
            raise TraceInputError(
                _diagnostic(
                    "missing_file",
                    filename,
                    f"required file {filename} is missing",
                )
            )
    return run_dir


def _read_meta(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            value = json.load(handle)
    except json.JSONDecodeError:
        raise TraceValidationError(
            [_diagnostic("malformed_json", META_JSON, "is malformed JSON")]
        )
    except OSError:
        raise TraceInputError(
            _diagnostic("unreadable_file", META_JSON, "meta.json could not be read")
        )
    if not isinstance(value, dict):
        raise TraceValidationError(
            [_diagnostic("invalid_type", META_JSON, "must contain a JSON object")]
        )
    return value


def _read_spans(path: Path) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    raise TraceValidationError(
                        [
                            _diagnostic(
                                "malformed_json",
                                f"{SPANS_JSONL}:{line_no}",
                                "is malformed JSON",
                            )
                        ]
                    )
                if not isinstance(value, dict):
                    raise TraceValidationError(
                        [
                            _diagnostic(
                                "invalid_type",
                                f"{SPANS_JSONL}:{line_no}",
                                "must contain a JSON object",
                            )
                        ]
                    )
                spans.append(value)
    except TraceValidationError:
        raise
    except OSError:
        raise TraceInputError(
            _diagnostic("unreadable_file", SPANS_JSONL, "spans.jsonl could not be read")
        )
    return spans


def validate_trace_path(path: Path) -> ValidatedTrace:
    """Read and validate a native trace directory or its ``meta.json`` file."""
    run_dir = _resolve_trace_directory(Path(path))
    meta = _read_meta(run_dir / META_JSON)
    spans = _read_spans(run_dir / SPANS_JSONL)
    return validate_trace_payload(meta, spans, source_dir=run_dir)


def format_diagnostic_problem(diagnostic: TraceDiagnostic) -> str:
    """Render a diagnostic in the established stored-run error vocabulary."""
    location = diagnostic.location
    if diagnostic.code == "invalid_id" and location.endswith(".span_id"):
        line = location.removeprefix(f"{SPANS_JSONL}:").split(".", 1)[0]
        return f"{SPANS_JSONL} line {line} has an invalid span_id"
    if diagnostic.code == "invalid_id" and location.endswith(".parent_span_id"):
        line = location.removeprefix(f"{SPANS_JSONL}:").split(".", 1)[0]
        return f"{SPANS_JSONL} line {line} has an invalid parent_span_id"
    if location.startswith(f"{SPANS_JSONL}:"):
        remainder = location.removeprefix(f"{SPANS_JSONL}:")
        line, _, suffix = remainder.partition(".")
        display = f"{SPANS_JSONL} line {line}"
        if suffix:
            display += f" field {suffix!r}"
    elif location.startswith(f"{META_JSON}."):
        display = f"{META_JSON} field {location.removeprefix(f'{META_JSON}.')!r}"
    else:
        display = location
    return f"{display} {diagnostic.message}"
