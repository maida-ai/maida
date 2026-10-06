"""Read-only claude_code import: loading."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maida.config import MaidaConfig
from maida.integrations._claude_code.types import (
    _HASH_RE,
    _KNOWN_LOGS,
    _MAPPING_VERSION,
    _NONNEGATIVE_FIELDS,
    _SEGMENT_RE,
    _SPAN_ID_RE,
    _TRACE_ID_RE,
    ClaudeCaptureImportError,
    ClaudeCaptureInputError,
    ClaudeCaptureSegment,
)


def _hash_id(*parts: str, length: int) -> str:
    value = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:length]
    if set(value) == {"0"}:
        raise ClaudeCaptureImportError("deterministic identifier resolved to zero")
    return value


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_json(path: Path, display: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ClaudeCaptureInputError(f"capture is missing {display}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaudeCaptureInputError(f"capture {display} is malformed") from exc
    if not isinstance(value, dict):
        raise ClaudeCaptureInputError(f"capture {display} must be an object")
    return value


def _read_jsonl(path: Path, display: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    values: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ClaudeCaptureInputError(f"capture {display} could not be read") from exc
    for line_no, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ClaudeCaptureInputError(f"capture {display} line {line_no} is malformed JSON") from exc
        if not isinstance(value, dict):
            raise ClaudeCaptureInputError(f"capture {display} line {line_no} must be an object")
        values.append(value)
    return values


def _source_identity(item: dict[str, Any], signal: str) -> str:
    if signal == "spans":
        span = item["span"]
        parts = (item["session_hash"], span["trace_id"], span["span_id"])
    else:
        record = item["record"]
        attributes = record["attributes"]
        parts = (
            item["session_hash"],
            record["event_name"],
            attributes.get("event.sequence"),
            record.get("time_unix_nano"),
            record.get("trace_id"),
            record.get("span_id"),
            attributes.get("tool_use_id"),
            attributes.get("request_id"),
            attributes.get("prompt.id"),
        )
    return repr(parts)


def _deduplicate(values: list[dict[str, Any]], signal: str) -> list[dict[str, Any]]:
    by_identity: dict[str, str] = {}
    unique: list[dict[str, Any]] = []
    for value in values:
        identity = _source_identity(value, signal)
        canonical = _canonical(value)
        previous = by_identity.get(identity)
        if previous is not None and previous != canonical:
            raise ClaudeCaptureInputError(f"capture has conflicting duplicate {signal} identities")
        if previous is None:
            by_identity[identity] = canonical
            unique.append(value)
    return unique


def _require_nonnegative(attributes: dict[str, Any], display: str) -> None:
    for field in _NONNEGATIVE_FIELDS:
        value = attributes.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ClaudeCaptureInputError(f"{display} field {field!r} must be nonnegative")


def _validate_resource(item: dict[str, Any], session_hash: str, display: str) -> str | None:
    if item.get("session_hash") != session_hash:
        raise ClaudeCaptureInputError(f"{display} session hash does not match manifest")
    resource = item.get("resource")
    if not isinstance(resource, dict) or not isinstance(resource.get("attributes"), dict):
        raise ClaudeCaptureInputError(f"{display} resource attributes are missing")
    attributes = resource["attributes"]
    if attributes.get("service.name") != "claude-code":
        raise ClaudeCaptureInputError(f"{display} service.name must be 'claude-code'")
    version = attributes.get("app.version")
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise ClaudeCaptureInputError(f"{display} app.version must be a string")
    return version


def _validate_log(item: dict[str, Any], session_hash: str, line_no: int) -> str | None:
    display = f"logs.jsonl line {line_no}"
    version = _validate_resource(item, session_hash, display)
    record = item.get("record")
    if not isinstance(record, dict) or not isinstance(record.get("attributes"), dict):
        raise ClaudeCaptureInputError(f"{display} record attributes are missing")
    event_name = record.get("event_name")
    if not isinstance(event_name, str) or not event_name:
        raise ClaudeCaptureInputError(f"{display} event_name must be a string")
    attributes = record["attributes"]
    if attributes.get("session.id") != session_hash:
        raise ClaudeCaptureInputError(f"{display} session.id does not match manifest")
    timestamp = record.get("time_unix_nano")
    if not isinstance(timestamp, int) or timestamp < 0:
        raise ClaudeCaptureInputError(f"{display} time_unix_nano must be nonnegative")
    for field, pattern in (("trace_id", _TRACE_ID_RE), ("span_id", _SPAN_ID_RE)):
        value = record.get(field, "")
        if value and (not isinstance(value, str) or pattern.fullmatch(value) is None):
            raise ClaudeCaptureInputError(f"{display} has invalid {field}")
    _require_nonnegative(attributes, event_name)
    required = {
        "claude_code.api_request": (
            "model",
            "duration_ms",
            "input_tokens",
            "output_tokens",
        ),
        "claude_code.api_error": ("model", "duration_ms", "attempt"),
        "claude_code.tool_result": (
            "tool_name",
            "tool_use_id",
            "success",
            "duration_ms",
        ),
        "claude_code.tool_decision": ("tool_name", "tool_use_id", "decision"),
        "claude_code.user_prompt": ("prompt_length",),
        "claude_code.assistant_response": ("response_length", "model"),
        "claude_code.hook.session_start": ("hook_event_name", "source"),
        "claude_code.hook.pre_tool_use": (
            "hook_event_name",
            "tool_name",
            "tool_use_id",
            "tool_input",
        ),
        "claude_code.hook.post_tool_use": (
            "hook_event_name",
            "tool_name",
            "tool_use_id",
            "tool_input",
            "tool_response",
        ),
        "claude_code.hook.post_tool_use_failure": (
            "hook_event_name",
            "tool_name",
            "tool_use_id",
            "tool_input",
            "error",
        ),
        "claude_code.hook.permission_denied": (
            "hook_event_name",
            "tool_name",
            "tool_use_id",
            "tool_input",
            "reason",
        ),
        "claude_code.hook.session_end": ("hook_event_name", "reason"),
    }
    missing = [field for field in required.get(event_name, ()) if field not in attributes]
    if event_name in _KNOWN_LOGS and missing:
        raise ClaudeCaptureInputError(f"{event_name} is missing required field(s): {', '.join(missing)}")
    if event_name.startswith("claude_code.hook."):
        tool_input = attributes.get("tool_input")
        if tool_input is not None and not isinstance(tool_input, dict):
            raise ClaudeCaptureInputError(f"{event_name} tool_input must be an object")
    return version


def _parse_time(value: object, display: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ClaudeCaptureInputError(f"{display} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClaudeCaptureInputError(f"{display} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ClaudeCaptureInputError(f"{display} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _validate_span(item: dict[str, Any], session_hash: str, line_no: int) -> str | None:
    display = f"spans.jsonl line {line_no}"
    version = _validate_resource(item, session_hash, display)
    span = item.get("span")
    if not isinstance(span, dict) or not isinstance(span.get("attributes"), dict):
        raise ClaudeCaptureInputError(f"{display} span attributes are missing")
    for field, pattern in (("trace_id", _TRACE_ID_RE), ("span_id", _SPAN_ID_RE)):
        value = span.get(field)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ClaudeCaptureInputError(f"{display} has invalid {field}")
    parent = span.get("parent_span_id")
    if parent is not None and (not isinstance(parent, str) or _SPAN_ID_RE.fullmatch(parent) is None):
        raise ClaudeCaptureInputError(f"{display} has invalid parent_span_id")
    if not isinstance(span.get("name"), str) or not span["name"]:
        raise ClaudeCaptureInputError(f"{display} name must be a nonempty string")
    start = _parse_time(span.get("start_time"), f"{display} start_time")
    end = _parse_time(span.get("end_time"), f"{display} end_time")
    if end < start:
        raise ClaudeCaptureInputError(f"{display} ends before it starts")
    duration = span.get("duration_ms")
    if not isinstance(duration, int) or duration < 0:
        raise ClaudeCaptureInputError(f"{display} duration_ms must be nonnegative")
    if span["attributes"].get("session.id") != session_hash:
        raise ClaudeCaptureInputError(f"{display} session.id does not match manifest")
    _require_nonnegative(span["attributes"], span["name"])
    if not isinstance(span.get("events"), list):
        raise ClaudeCaptureInputError(f"{display} events must be an array")
    if span.get("status_code") not in {"UNSET", "OK", "ERROR"}:
        raise ClaudeCaptureInputError(f"{display} has invalid status_code")
    return version


def load_capture_segment(path: Path) -> ClaudeCaptureSegment:
    """Load and validate one immutable capture segment by filesystem path."""
    path = path.expanduser()
    manifest = _read_json(path / "manifest.json", "manifest.json")
    if manifest.get("capture_version") != 1 or manifest.get("source") != "claude-code":
        raise ClaudeCaptureInputError("capture manifest has an unsupported source/version")
    session_hash = manifest.get("session_hash")
    segment = manifest.get("segment")
    if not isinstance(session_hash, str) or _HASH_RE.fullmatch(session_hash) is None:
        raise ClaudeCaptureInputError("capture manifest has an invalid session_hash")
    if not isinstance(segment, str) or _SEGMENT_RE.fullmatch(segment) is None:
        raise ClaudeCaptureInputError("capture manifest has an invalid segment")
    logs = _read_jsonl(path / "logs.jsonl", "logs.jsonl")
    spans = _read_jsonl(path / "spans.jsonl", "spans.jsonl")
    if not logs and not spans:
        raise ClaudeCaptureInputError("capture segment contains no logs or spans")
    versions = {
        version
        for line_no, item in enumerate(logs, 1)
        if (version := _validate_log(item, session_hash, line_no)) is not None
    }
    versions.update(
        version
        for line_no, item in enumerate(spans, 1)
        if (version := _validate_span(item, session_hash, line_no)) is not None
    )
    logs = _deduplicate(logs, "logs")
    spans = _deduplicate(spans, "spans")
    signals = manifest.get("signals")
    if isinstance(signals, dict):
        for signal, values in (("logs", logs), ("spans", spans)):
            declared = signals.get(signal)
            if declared is not None and declared != len(values):
                raise ClaudeCaptureInputError(f"capture manifest signals.{signal} does not match stored records")
    source_fingerprint = _hash_id(
        f"mapping-v{_MAPPING_VERSION}",
        session_hash,
        segment,
        _canonical(logs),
        _canonical(spans),
        length=64,
    )
    return ClaudeCaptureSegment(
        path=path,
        session_hash=session_hash,
        segment=segment,
        manifest=manifest,
        logs=logs,
        spans=spans,
        source_fingerprint=source_fingerprint,
        service_versions=tuple(sorted(versions)),
    )


def load_claude_capture(
    session_id: str,
    config: MaidaConfig,
    *,
    segment: str = "latest",
) -> ClaudeCaptureSegment:
    """Resolve a raw Claude session ID safely and load one capture segment."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ClaudeCaptureInputError("--session-id must be a nonempty string")
    session_hash = hashlib.sha256(session_id.strip().encode("utf-8")).hexdigest()
    session_dir = config.data_dir.expanduser() / "captures" / "claude-code" / session_hash
    if segment == "latest":
        if not session_dir.is_dir():
            raise ClaudeCaptureInputError(f"Claude Code capture {session_hash[:12]} was not found")
        candidates = sorted(
            entry.name for entry in session_dir.iterdir() if entry.is_dir() and (entry / "manifest.json").is_file()
        )
        if not candidates:
            raise ClaudeCaptureInputError(f"Claude Code capture {session_hash[:12]} has no segments")
        selected = candidates[-1]
    else:
        if _SEGMENT_RE.fullmatch(segment) is None or segment in {".", ".."}:
            raise ClaudeCaptureInputError("--segment has an invalid value")
        selected = segment
    loaded = load_capture_segment(session_dir / selected)
    if loaded.session_hash != session_hash:
        raise ClaudeCaptureInputError("capture session hash does not match selection")
    return loaded
