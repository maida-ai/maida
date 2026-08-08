"""Normalize and atomically import local Claude Code captures."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from maida._tracing._otel import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_ID,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    MAIDA_ERROR_COUNT,
    MAIDA_ERROR_MESSAGE,
    MAIDA_ERROR_TYPE,
    MAIDA_EVENT_TYPE,
    MAIDA_LLM_COUNT,
    MAIDA_LOOP_WARNING_COUNT,
    MAIDA_META,
    MAIDA_RUN_NAME,
    MAIDA_TOOL_COUNT,
    MAIDA_TOOL_NAME,
)
from maida.capture.claude_code import _sanitize
from maida.config import MaidaConfig
from maida.constants import SPEC_VERSION
from maida.events import span_to_event_dict
from maida.loopdetect import detect_loop, pattern_key
from maida.storage import install_validated_run, load_validated_run

_MAPPING_VERSION = 1
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_KNOWN_LOGS = frozenset(
    {
        "claude_code.user_prompt",
        "claude_code.assistant_response",
        "claude_code.tool_result",
        "claude_code.api_request",
        "claude_code.api_error",
        "claude_code.api_refusal",
        "claude_code.tool_decision",
    }
)
_NONNEGATIVE_FIELDS = frozenset(
    {
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "result_tokens",
        "prompt_length",
        "response_length",
        "tool_input_size_bytes",
        "tool_result_size_bytes",
        "event.sequence",
        "attempt",
        "cost_usd",
        "cost_usd_micros",
    }
)
_KIND_NAMES = {
    0: "INTERNAL",
    1: "INTERNAL",
    2: "SERVER",
    3: "CLIENT",
    4: "PRODUCER",
    5: "CONSUMER",
}


class ClaudeCaptureInputError(ValueError):
    """A selected local capture is missing or malformed."""


class ClaudeCaptureImportError(RuntimeError):
    """A valid capture could not be installed safely."""


class ClaudeCaptureChangedError(ClaudeCaptureInputError):
    """The capture changed after the deterministic run was installed."""


@dataclass(frozen=True)
class ClaudeCaptureSegment:
    path: Path
    session_hash: str
    segment: str
    manifest: dict[str, Any]
    logs: list[dict[str, Any]]
    spans: list[dict[str, Any]]
    source_fingerprint: str
    service_versions: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedClaudeRun:
    trace_id: str
    session_hash: str
    segment: str
    source_fingerprint: str
    run_name: str
    meta: dict[str, Any]
    spans: list[dict[str, Any]]


@dataclass(frozen=True)
class ClaudeImportResult:
    trace_id: str
    run_name: str
    session_hash: str
    segment: str
    source_fingerprint: str
    imported: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_name": self.run_name,
            "session_hash": self.session_hash,
            "segment": self.segment,
            "source_fingerprint": self.source_fingerprint,
            "imported": self.imported,
        }


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
            raise ClaudeCaptureInputError(
                f"capture {display} line {line_no} is malformed JSON"
            ) from exc
        if not isinstance(value, dict):
            raise ClaudeCaptureInputError(
                f"capture {display} line {line_no} must be an object"
            )
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
            raise ClaudeCaptureInputError(
                f"capture has conflicting duplicate {signal} identities"
            )
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
            raise ClaudeCaptureInputError(
                f"{display} field {field!r} must be nonnegative"
            )


def _validate_resource(
    item: dict[str, Any], session_hash: str, display: str
) -> str | None:
    if item.get("session_hash") != session_hash:
        raise ClaudeCaptureInputError(f"{display} session hash does not match manifest")
    resource = item.get("resource")
    if not isinstance(resource, dict) or not isinstance(
        resource.get("attributes"), dict
    ):
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
    }
    missing = [
        field for field in required.get(event_name, ()) if field not in attributes
    ]
    if event_name in _KNOWN_LOGS and missing:
        raise ClaudeCaptureInputError(
            f"{event_name} is missing required field(s): {', '.join(missing)}"
        )
    return version


def _parse_time(value: object, display: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ClaudeCaptureInputError(f"{display} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ClaudeCaptureInputError(
            f"{display} must be an ISO-8601 timestamp"
        ) from exc
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
    if parent is not None and (
        not isinstance(parent, str) or _SPAN_ID_RE.fullmatch(parent) is None
    ):
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
        raise ClaudeCaptureInputError(
            "capture manifest has an unsupported source/version"
        )
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
                raise ClaudeCaptureInputError(
                    f"capture manifest signals.{signal} does not match stored records"
                )
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
    session_dir = (
        config.data_dir.expanduser() / "captures" / "claude-code" / session_hash
    )
    if segment == "latest":
        if not session_dir.is_dir():
            raise ClaudeCaptureInputError(
                f"Claude Code capture {session_hash[:12]} was not found"
            )
        candidates = sorted(
            entry.name
            for entry in session_dir.iterdir()
            if entry.is_dir() and (entry / "manifest.json").is_file()
        )
        if not candidates:
            raise ClaudeCaptureInputError(
                f"Claude Code capture {session_hash[:12]} has no segments"
            )
        selected = candidates[-1]
    else:
        if _SEGMENT_RE.fullmatch(segment) is None or segment in {".", ".."}:
            raise ClaudeCaptureInputError("--segment has an invalid value")
        selected = segment
    loaded = load_capture_segment(session_dir / selected)
    if loaded.session_hash != session_hash:
        raise ClaudeCaptureInputError("capture session hash does not match selection")
    return loaded


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _log_time(record: dict[str, Any]) -> datetime:
    attributes = record["record"]["attributes"]
    timestamp = attributes.get("event.timestamp")
    if isinstance(timestamp, str):
        return _parse_time(timestamp, "event.timestamp")
    nanos = record["record"].get("time_unix_nano")
    if not isinstance(nanos, int) or nanos < 0:
        raise ClaudeCaptureInputError("log record timestamp is invalid")
    return datetime.fromtimestamp(nanos / 1_000_000_000, timezone.utc)


def _meta_json(payload: dict[str, Any], config: MaidaConfig) -> str:
    return json.dumps(
        _sanitize(payload, config),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _truthy(value: Any) -> bool:
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _source_meta(
    *,
    source_kind: str,
    source_name: str,
    attributes: dict[str, Any],
    service_version: str | None,
    source_trace_id: str | None = None,
    source_span_id: str | None = None,
    source_record_identity: str | None = None,
    source_events: list[dict[str, Any]] | None = None,
    parent_cycle_broken: bool = False,
) -> dict[str, Any]:
    value = {
        "mapping_version": _MAPPING_VERSION,
        "service_version": service_version,
        "source_kind": source_kind,
        "source_name": source_name,
        "source_trace_id": source_trace_id,
        "source_span_id": source_span_id,
        "source_record_identity": source_record_identity,
        "source_attributes": attributes,
        "source_events": source_events or [],
    }
    if parent_cycle_broken:
        value["parent_cycle_broken"] = True
    return value


def _tool_args(attributes: dict[str, Any]) -> Any:
    raw = _parse_json_value(attributes.get("tool_input"))
    if isinstance(raw, dict):
        return raw
    args = {
        key: attributes[key]
        for key in (
            "file_path",
            "full_command",
            "skill_name",
            "subagent_type",
            "agent_id",
            "parent_agent_id",
            "workflow.run_id",
            "workflow.name",
        )
        if key in attributes
    }
    return args


def _normalized_span(
    *,
    trace_id: str,
    span_id: str,
    parent_span_id: str,
    name: str,
    start: datetime,
    end: datetime,
    kind: str,
    attributes: dict[str, Any],
    events: list[dict[str, Any]],
    status_code: str,
    status_description: str,
) -> dict[str, Any]:
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": name,
        "kind": kind,
        "start_time": _iso(start),
        "end_time": _iso(end),
        "duration_ms": max(0, int((end - start).total_seconds() * 1000)),
        "attributes": attributes,
        "events": events,
        "status_code": status_code,
        "status_description": status_description,
    }


def _cycle_members(parents: dict[str, str | None]) -> set[str]:
    cycles: set[str] = set()
    resolved: set[str] = set()
    for start in parents:
        order: list[str] = []
        positions: dict[str, int] = {}
        current: str | None = start
        while current is not None and current in parents and current not in resolved:
            if current in positions:
                cycles.update(order[positions[current] :])
                break
            positions[current] = len(order)
            order.append(current)
            current = parents[current]
        resolved.update(order)
    return cycles


def _service_version(item: dict[str, Any]) -> str | None:
    return item.get("resource", {}).get("attributes", {}).get("app.version")


def normalize_claude_capture(
    capture: ClaudeCaptureSegment,
    config: MaidaConfig,
) -> NormalizedClaudeRun:
    """Project one capture segment into the current Maida trace schema."""
    trace_id = _hash_id("claude-code", capture.session_hash, capture.segment, length=32)
    root_span_id = _hash_id(trace_id, "session-root", length=16)
    run_name = f"claude-code:{capture.session_hash[:12]}:{capture.segment}"
    used_ids = {root_span_id}

    def span_id(key: str) -> str:
        value = _hash_id(trace_id, key, length=16)
        if value in used_ids:
            raise ClaudeCaptureImportError("deterministic Claude span ID collision")
        used_ids.add(value)
        return value

    ordered_source_spans = sorted(
        capture.spans,
        key=lambda item: (
            _parse_time(item["span"]["start_time"], "span start_time"),
            item["span"]["trace_id"],
            item["span"]["span_id"],
        ),
    )
    source_by_key = {
        f"{item['span']['trace_id']}:{item['span']['span_id']}": item
        for item in ordered_source_spans
    }
    normalized_ids = {key: span_id(f"source-span:{key}") for key in source_by_key}
    parents: dict[str, str | None] = {}
    for key, item in source_by_key.items():
        source = item["span"]
        parent_key = (
            f"{source['trace_id']}:{source['parent_span_id']}"
            if source.get("parent_span_id")
            else None
        )
        parents[key] = parent_key if parent_key in source_by_key else None
    cycle_members = _cycle_members(parents)

    interaction_by_group: dict[str, str] = {}
    interaction_times: dict[str, list[datetime]] = {}
    actual_interactions: set[str] = set()
    for key, item in source_by_key.items():
        source = item["span"]
        if source["name"] == "claude_code.interaction":
            group = f"trace:{source['trace_id']}"
            interaction_by_group.setdefault(group, normalized_ids[key])
            actual_interactions.add(normalized_ids[key])

    def ensure_interaction(group: str) -> str:
        current = interaction_by_group.get(group)
        if current is None:
            current = span_id(f"synthetic-interaction:{group}")
            interaction_by_group[group] = current
        return current

    normalized: list[dict[str, Any]] = []
    normalized_by_source: dict[tuple[str, str], dict[str, Any]] = {}
    action_spans: list[dict[str, Any]] = []

    for key, item in source_by_key.items():
        source = item["span"]
        source_name = source["name"]
        group = f"trace:{source['trace_id']}"
        interaction_id = ensure_interaction(group)
        start = _parse_time(source["start_time"], "span start_time")
        end = _parse_time(source["end_time"], "span end_time")
        interaction_times.setdefault(group, []).extend((start, end))
        is_interaction = source_name == "claude_code.interaction"
        broken = key in cycle_members
        if is_interaction:
            parent_id = root_span_id
        elif broken:
            parent_id = interaction_id
        else:
            parent_key = parents[key]
            parent_id = normalized_ids[parent_key] if parent_key else interaction_id
            if parent_id == normalized_ids[key]:
                parent_id = interaction_id
                broken = True
        source_attributes = source["attributes"]
        meta = _source_meta(
            source_kind="span",
            source_name=source_name,
            attributes=source_attributes,
            service_version=_service_version(item),
            source_trace_id=source["trace_id"],
            source_span_id=source["span_id"],
            source_events=source.get("events") or [],
            parent_cycle_broken=broken,
        )
        attrs: dict[str, Any] = {MAIDA_META: _meta_json({"claude_code": meta}, config)}
        events: list[dict[str, Any]] = []
        status = source.get("status_code", "UNSET")
        status_description = str(source.get("status_description") or "")
        name = source_name

        is_action = False
        if source_name == "claude_code.interaction":
            name = "interaction"
        elif source_name == "claude_code.llm_request":
            model = str(source_attributes.get("model") or "unknown")
            name = model
            attrs.update(
                {
                    GEN_AI_OPERATION_NAME: "chat",
                    GEN_AI_SYSTEM: str(
                        source_attributes.get("gen_ai.system") or "anthropic"
                    ),
                    GEN_AI_REQUEST_MODEL: model,
                }
            )
            input_tokens = source_attributes.get("input_tokens")
            output_tokens = source_attributes.get("output_tokens")
            if isinstance(input_tokens, (int, float)) and not isinstance(
                input_tokens, bool
            ):
                attrs[GEN_AI_USAGE_INPUT_TOKENS] = int(input_tokens)
            if isinstance(output_tokens, (int, float)) and not isinstance(
                output_tokens, bool
            ):
                attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = int(output_tokens)
            if (
                GEN_AI_USAGE_INPUT_TOKENS in attrs
                or GEN_AI_USAGE_OUTPUT_TOKENS in attrs
            ):
                attrs[GEN_AI_USAGE_TOTAL_TOKENS] = attrs.get(
                    GEN_AI_USAGE_INPUT_TOKENS, 0
                ) + attrs.get(GEN_AI_USAGE_OUTPUT_TOKENS, 0)
            if source_attributes.get("request_id"):
                attrs[GEN_AI_RESPONSE_ID] = str(source_attributes["request_id"])
            if source_attributes.get("stop_reason"):
                attrs[GEN_AI_RESPONSE_FINISH_REASONS] = str(
                    source_attributes["stop_reason"]
                )
            if source_attributes.get("success") is False:
                status = "ERROR"
            is_action = True
        elif source_name == "claude_code.tool":
            tool_name = str(source_attributes.get("tool_name") or "unknown")
            name = tool_name
            attrs[MAIDA_TOOL_NAME] = tool_name
            args = _sanitize(_tool_args(source_attributes), config)
            events.append(
                {
                    "name": "maida.tool.args",
                    "timestamp": _iso(start),
                    "attributes": {"args": json.dumps(args, ensure_ascii=False)},
                }
            )
            if source_attributes.get("success") is False:
                status = "ERROR"
            is_action = True
        if status == "ERROR":
            attrs[MAIDA_ERROR_TYPE] = str(
                source_attributes.get("error_type") or "ClaudeCodeError"
            )
            attrs[MAIDA_ERROR_MESSAGE] = str(
                source_attributes.get("error") or status_description
            )

        projected = _normalized_span(
            trace_id=trace_id,
            span_id=normalized_ids[key],
            parent_span_id=parent_id,
            name=name,
            start=start,
            end=end,
            kind=_KIND_NAMES.get(source.get("kind"), "INTERNAL"),
            attributes=attrs,
            events=events,
            status_code=status,
            status_description=status_description,
        )
        normalized.append(projected)
        normalized_by_source[(source["trace_id"], source["span_id"])] = projected
        if is_action:
            action_spans.append(projected)

    ordered_logs = sorted(
        capture.logs,
        key=lambda item: (
            _log_time(item),
            int(item["record"]["attributes"].get("event.sequence", 0)),
            _source_identity(item, "logs"),
        ),
    )
    tool_results = {
        item["record"]["attributes"].get("tool_use_id")
        for item in ordered_logs
        if item["record"]["event_name"] == "claude_code.tool_result"
    }

    def log_group(item: dict[str, Any]) -> str:
        record = item["record"]
        if record.get("trace_id"):
            return f"trace:{record['trace_id']}"
        prompt_id = record["attributes"].get("prompt.id")
        return f"prompt:{prompt_id or 'session'}"

    for item in ordered_logs:
        record = item["record"]
        event_name = record["event_name"]
        attributes = record["attributes"]
        when = _log_time(item)
        group = log_group(item)
        interaction_id = ensure_interaction(group)
        interaction_times.setdefault(group, []).append(when)
        correlated = normalized_by_source.get(
            (record.get("trace_id", ""), record.get("span_id", ""))
        )
        correlated_type = (
            span_to_event_dict(correlated)["event_type"] if correlated else None
        )
        consumed = False
        if event_name == "claude_code.user_prompt" and correlated is not None:
            consumed = True
        elif (
            event_name
            in {
                "claude_code.api_request",
                "claude_code.api_error",
                "claude_code.assistant_response",
            }
            and correlated_type == "LLM_CALL"
        ):
            consumed = True
            for source_key, target_key in (
                ("input_tokens", GEN_AI_USAGE_INPUT_TOKENS),
                ("output_tokens", GEN_AI_USAGE_OUTPUT_TOKENS),
            ):
                value = attributes.get(source_key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    correlated["attributes"][target_key] = int(value)
            if (
                GEN_AI_USAGE_INPUT_TOKENS in correlated["attributes"]
                or GEN_AI_USAGE_OUTPUT_TOKENS in correlated["attributes"]
            ):
                correlated["attributes"][GEN_AI_USAGE_TOTAL_TOKENS] = correlated[
                    "attributes"
                ].get(GEN_AI_USAGE_INPUT_TOKENS, 0) + correlated["attributes"].get(
                    GEN_AI_USAGE_OUTPUT_TOKENS, 0
                )
            if event_name == "claude_code.api_error":
                correlated["status_code"] = "ERROR"
                correlated["attributes"][MAIDA_ERROR_TYPE] = "ClaudeCodeAPIError"
                correlated["attributes"][MAIDA_ERROR_MESSAGE] = str(
                    attributes.get("error") or "Claude Code API request failed"
                )
        elif (
            event_name
            in {
                "claude_code.tool_result",
                "claude_code.tool_decision",
            }
            and correlated_type == "TOOL_CALL"
        ):
            consumed = True
            if event_name == "claude_code.tool_result":
                args = _sanitize(_tool_args(attributes), config)
                correlated["events"] = [
                    event
                    for event in correlated["events"]
                    if event.get("name") != "maida.tool.args"
                ]
                correlated["events"].append(
                    {
                        "name": "maida.tool.args",
                        "timestamp": correlated["start_time"],
                        "attributes": {"args": json.dumps(args, ensure_ascii=False)},
                    }
                )
                if not _truthy(attributes.get("success")):
                    correlated["status_code"] = "ERROR"
                    correlated["attributes"][MAIDA_ERROR_TYPE] = str(
                        attributes.get("error_type") or "ClaudeCodeToolError"
                    )
                    correlated["attributes"][MAIDA_ERROR_MESSAGE] = str(
                        attributes.get("error") or "Claude Code tool failed"
                    )

        if consumed:
            raw_meta = correlated["attributes"].get(MAIDA_META) if correlated else None
            if raw_meta:
                meta = json.loads(raw_meta)
                logs = meta["claude_code"].setdefault("source_logs", [])
                logs.append(
                    {
                        "event_name": event_name,
                        "record_identity": _source_identity(item, "logs"),
                        "attributes": attributes,
                    }
                )
                correlated["attributes"][MAIDA_META] = _meta_json(meta, config)
            continue

        if event_name == "claude_code.user_prompt":
            continue
        if (
            event_name == "claude_code.tool_decision"
            and attributes.get("tool_use_id") in tool_results
        ):
            continue

        meta = _source_meta(
            source_kind="log",
            source_name=event_name,
            attributes=attributes,
            service_version=_service_version(item),
            source_trace_id=record.get("trace_id") or None,
            source_span_id=record.get("span_id") or None,
            source_record_identity=_source_identity(item, "logs"),
        )
        attrs = {MAIDA_META: _meta_json({"claude_code": meta}, config)}
        events: list[dict[str, Any]] = []
        name = event_name
        status = "OK"
        status_description = ""
        duration = attributes.get("duration_ms", 0)
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            duration = 0
        end = when
        start = when if duration == 0 else when - timedelta(milliseconds=duration)
        interaction_times[group].extend((start, end))

        is_action = False
        if event_name in {"claude_code.api_request", "claude_code.api_error"}:
            model = str(attributes.get("model") or "unknown")
            name = model
            attrs.update(
                {
                    GEN_AI_OPERATION_NAME: "chat",
                    GEN_AI_SYSTEM: "anthropic",
                    GEN_AI_REQUEST_MODEL: model,
                }
            )
            input_tokens = attributes.get("input_tokens")
            output_tokens = attributes.get("output_tokens")
            if isinstance(input_tokens, (int, float)) and not isinstance(
                input_tokens, bool
            ):
                attrs[GEN_AI_USAGE_INPUT_TOKENS] = int(input_tokens)
            if isinstance(output_tokens, (int, float)) and not isinstance(
                output_tokens, bool
            ):
                attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = int(output_tokens)
            attrs[GEN_AI_USAGE_TOTAL_TOKENS] = attrs.get(
                GEN_AI_USAGE_INPUT_TOKENS, 0
            ) + attrs.get(GEN_AI_USAGE_OUTPUT_TOKENS, 0)
            if attributes.get("request_id"):
                attrs[GEN_AI_RESPONSE_ID] = str(attributes["request_id"])
            if event_name == "claude_code.api_error":
                status = "ERROR"
                attrs[MAIDA_ERROR_TYPE] = "ClaudeCodeAPIError"
                attrs[MAIDA_ERROR_MESSAGE] = str(attributes.get("error") or "API error")
            is_action = True
        elif event_name == "claude_code.tool_result" or (
            event_name == "claude_code.tool_decision"
            and attributes.get("decision") == "reject"
        ):
            tool_name = str(attributes.get("tool_name") or "unknown")
            name = tool_name
            attrs[MAIDA_TOOL_NAME] = tool_name
            args = _sanitize(_tool_args(attributes), config)
            events.append(
                {
                    "name": "maida.tool.args",
                    "timestamp": _iso(start),
                    "attributes": {"args": json.dumps(args, ensure_ascii=False)},
                }
            )
            failed = event_name == "claude_code.tool_decision" or not _truthy(
                attributes.get("success")
            )
            if failed:
                status = "ERROR"
                attrs[MAIDA_ERROR_TYPE] = str(
                    attributes.get("error_type") or "ClaudeCodeToolError"
                )
                attrs[MAIDA_ERROR_MESSAGE] = str(
                    attributes.get("error") or "Claude Code tool was rejected"
                )
            is_action = True

        projected = _normalized_span(
            trace_id=trace_id,
            span_id=span_id(f"source-log:{_source_identity(item, 'logs')}"),
            parent_span_id=interaction_id,
            name=name,
            start=start,
            end=end,
            kind="INTERNAL",
            attributes=attrs,
            events=events,
            status_code=status,
            status_description=status_description,
        )
        normalized.append(projected)
        if is_action:
            action_spans.append(projected)

    for group, interaction_id in interaction_by_group.items():
        if interaction_id in actual_interactions:
            continue
        times = interaction_times.get(group)
        if not times:
            continue
        start, end = min(times), max(times)
        normalized.append(
            _normalized_span(
                trace_id=trace_id,
                span_id=interaction_id,
                parent_span_id=root_span_id,
                name="interaction",
                start=start,
                end=end,
                kind="INTERNAL",
                attributes={
                    MAIDA_META: _meta_json(
                        {
                            "claude_code": {
                                "mapping_version": _MAPPING_VERSION,
                                "source_kind": "synthetic_interaction",
                                "source_name": group,
                                "service_version": capture.service_versions[-1]
                                if capture.service_versions
                                else None,
                            }
                        },
                        config,
                    )
                },
                events=[],
                status_code="OK",
                status_description="",
            )
        )

    action_spans.sort(key=lambda span: (span["start_time"], span["span_id"]))
    loop_spans: list[dict[str, Any]] = []
    action_window: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for action in action_spans:
        event = span_to_event_dict(action)
        action_window.append(event)
        if len(action_window) > config.loop_window:
            action_window = action_window[-config.loop_window :]
        payload = detect_loop(
            action_window, config.loop_window, config.loop_repetitions
        )
        if payload is None:
            continue
        key = pattern_key(payload)
        if key in emitted:
            continue
        emitted.add(key)
        when = _parse_time(action["end_time"], "action end_time")
        loop_spans.append(
            _normalized_span(
                trace_id=trace_id,
                span_id=span_id(f"loop:{key}"),
                parent_span_id=root_span_id,
                name="loop_warning",
                start=when,
                end=when,
                kind="INTERNAL",
                attributes={MAIDA_EVENT_TYPE: "LOOP_WARNING"},
                events=[
                    {
                        "name": "maida.loop.warning",
                        "timestamp": _iso(when),
                        "attributes": _sanitize(payload, config),
                    }
                ],
                status_code="OK",
                status_description="",
            )
        )
    normalized.extend(loop_spans)
    if not normalized:
        raise ClaudeCaptureInputError("capture did not contain normalizable records")

    starts = [_parse_time(span["start_time"], "span start_time") for span in normalized]
    ends = [_parse_time(span["end_time"], "span end_time") for span in normalized]
    root_start, root_end = min(starts), max(ends)
    llm_calls = sum(
        1
        for span in action_spans
        if span_to_event_dict(span)["event_type"] == "LLM_CALL"
    )
    tool_calls = sum(
        1
        for span in action_spans
        if span_to_event_dict(span)["event_type"] == "TOOL_CALL"
    )
    errors = sum(1 for span in normalized if span["status_code"] == "ERROR")
    root_source = {
        "mapping_version": _MAPPING_VERSION,
        "source": "local-capture",
        "session_hash": capture.session_hash,
        "segment": capture.segment,
        "source_fingerprint": capture.source_fingerprint,
        "service_versions": list(capture.service_versions),
    }
    root_attrs = {
        MAIDA_RUN_NAME: run_name,
        MAIDA_LLM_COUNT: llm_calls,
        MAIDA_TOOL_COUNT: tool_calls,
        MAIDA_ERROR_COUNT: errors,
        MAIDA_LOOP_WARNING_COUNT: len(loop_spans),
        MAIDA_META: _meta_json({"claude_code": root_source}, config),
    }
    root = {
        "trace_id": trace_id,
        "span_id": root_span_id,
        "parent_span_id": None,
        "name": run_name,
        "kind": "INTERNAL",
        "start_time": _iso(root_start),
        "end_time": _iso(root_end),
        "duration_ms": max(0, int((root_end - root_start).total_seconds() * 1000)),
        "attributes": root_attrs,
        "events": [],
        "status_code": "ERROR" if errors else "OK",
        "status_description": "Claude Code capture contains failures" if errors else "",
    }
    all_spans = [
        root,
        *sorted(normalized, key=lambda span: (span["start_time"], span["span_id"])),
    ]
    meta = {
        "spec_version": SPEC_VERSION,
        "trace_id": trace_id,
        "run_name": run_name,
        "started_at": root["start_time"],
        "ended_at": root["end_time"],
        "duration_ms": root["duration_ms"],
        "status": "error" if errors else "ok",
        "counts": {
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "errors": errors,
            "loop_warnings": len(loop_spans),
        },
    }
    return NormalizedClaudeRun(
        trace_id=trace_id,
        session_hash=capture.session_hash,
        segment=capture.segment,
        source_fingerprint=capture.source_fingerprint,
        run_name=run_name,
        meta=meta,
        spans=all_spans,
    )


def _existing_matches(run: NormalizedClaudeRun, config: MaidaConfig) -> bool:
    try:
        _meta, spans = load_validated_run(run.trace_id, config)
    except FileNotFoundError:
        return False
    except Exception as exc:
        raise ClaudeCaptureImportError(
            f"existing destination run {run.trace_id} is invalid"
        ) from exc
    root = next((span for span in spans if span.get("parent_span_id") is None), None)
    if root is None:
        raise ClaudeCaptureImportError(
            f"existing destination run {run.trace_id} has no root span"
        )
    try:
        source = json.loads(root["attributes"][MAIDA_META])["claude_code"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ClaudeCaptureImportError(
            f"existing destination run {run.trace_id} is not a Claude import"
        ) from exc
    if (
        source.get("session_hash") != run.session_hash
        or source.get("segment") != run.segment
    ):
        raise ClaudeCaptureImportError(
            f"deterministic trace ID collision at destination {run.trace_id}"
        )
    if (
        source.get("mapping_version") != _MAPPING_VERSION
        or source.get("source_fingerprint") != run.source_fingerprint
    ):
        raise ClaudeCaptureChangedError(
            f"Claude Code capture changed since destination {run.trace_id} was imported; "
            "refusing to overwrite the local run"
        )
    return True


def import_claude_capture(
    session_id: str,
    config: MaidaConfig,
    *,
    segment: str = "latest",
) -> ClaudeImportResult:
    """Load, normalize, and atomically install one Claude capture."""
    capture = load_claude_capture(session_id, config, segment=segment)
    run = normalize_claude_capture(capture, config)
    if _existing_matches(run, config):
        imported = False
    else:
        try:
            install_validated_run(run.meta, run.spans, config)
        except FileExistsError as exc:
            raise ClaudeCaptureImportError(
                f"destination run {run.trace_id} was created concurrently; rerun import"
            ) from exc
        imported = True
    return ClaudeImportResult(
        trace_id=run.trace_id,
        run_name=run.run_name,
        session_hash=run.session_hash,
        segment=run.segment,
        source_fingerprint=run.source_fingerprint,
        imported=imported,
    )
