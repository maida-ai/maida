"""Loopback OTLP/HTTP receiver for Claude Code telemetry."""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from fastapi import FastAPI, HTTPException, Request, Response
from google.protobuf.message import DecodeError
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
    ExportLogsServiceResponse,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
    ExportTraceServiceResponse,
)

from maida._tracing._redact import (
    _key_matches_redact,
    _truncate_string,
)
from maida.config import MaidaConfig
from maida.constants import REDACTED_MARKER, TRUNCATED_MARKER
from maida.events import utc_now_iso_ms_z

DEFAULT_MAX_REQUEST_BYTES = 8 * 1024 * 1024
_PROTOBUF_CONTENT_TYPES = frozenset({"application/x-protobuf", "application/protobuf"})
_SERVICE_NAME = "claude-code"
_SEGMENT = "0001"
_TOKEN_COUNTERS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "result_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "totalTokens",
    }
)
_NONNEGATIVE_FIELDS = _TOKEN_COUNTERS | frozenset(
    {
        "duration_ms",
        "interaction.duration_ms",
        "ttft_ms",
        "prompt_length",
        "response_length",
        "tool_input_size_bytes",
        "tool_result_size_bytes",
        "cost_usd",
        "cost_usd_micros",
        "attempt",
        "event.sequence",
        "num_hooks",
        "num_success",
        "num_blocking",
        "num_non_blocking_error",
        "num_cancelled",
    }
)
_JSON_CONTENT_FIELDS = frozenset(
    {
        "body",
        "hook_definitions",
        "tool_input",
        "tool_parameters",
    }
)
_KNOWN_LOG_EVENTS = frozenset(
    {
        "claude_code.user_prompt",
        "claude_code.assistant_response",
        "claude_code.tool_result",
        "claude_code.api_request",
        "claude_code.api_error",
        "claude_code.api_refusal",
        "claude_code.api_request_body",
        "claude_code.api_response_body",
        "claude_code.tool_decision",
        "claude_code.permission_mode_changed",
        "claude_code.auth",
        "claude_code.mcp_server_connection",
        "claude_code.internal_error",
        "claude_code.plugin_installed",
        "claude_code.plugin_loaded",
        "claude_code.skill_activated",
        "claude_code.at_mention",
        "claude_code.api_retries_exhausted",
        "claude_code.hook_registered",
        "claude_code.hook_execution_start",
        "claude_code.hook_execution_complete",
        "claude_code.hook_plugin_metrics",
        "claude_code.compaction",
        "claude_code.feedback_survey",
    }
)
_KNOWN_SPANS = frozenset(
    {
        "claude_code.interaction",
        "claude_code.llm_request",
        "claude_code.tool",
        "claude_code.tool.blocked_on_user",
        "claude_code.tool.execution",
        "claude_code.hook",
    }
)
_REQUIRED_LOG_FIELDS: dict[str, tuple[str, ...]] = {
    "claude_code.user_prompt": ("prompt_length",),
    "claude_code.assistant_response": ("response_length", "model"),
    "claude_code.tool_result": (
        "tool_name",
        "tool_use_id",
        "success",
        "duration_ms",
    ),
    "claude_code.api_request": (
        "model",
        "duration_ms",
        "input_tokens",
        "output_tokens",
    ),
    "claude_code.api_error": ("model", "duration_ms", "attempt"),
    "claude_code.tool_decision": (
        "tool_name",
        "tool_use_id",
        "decision",
    ),
}
_REQUIRED_SPAN_FIELDS: dict[str, tuple[str, ...]] = {
    "claude_code.interaction": ("interaction.duration_ms",),
    "claude_code.llm_request": (
        "model",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "success",
    ),
    "claude_code.tool": ("tool_name", "duration_ms", "tool_use_id"),
    "claude_code.tool.blocked_on_user": ("duration_ms", "decision"),
    "claude_code.tool.execution": ("duration_ms", "tool_use_id", "success"),
    "claude_code.hook": ("hook_event", "hook_name", "duration_ms"),
}


class CaptureValidationError(ValueError):
    """An OTLP batch does not satisfy the Claude Code capture contract."""


class CaptureConflictError(RuntimeError):
    """A source identity was delivered again with different content."""


def _any_value(value: Any) -> Any:
    kind = value.WhichOneof("value")
    if kind is None:
        return None
    if kind == "array_value":
        return [_any_value(item) for item in value.array_value.values]
    if kind == "kvlist_value":
        return _attributes(value.kvlist_value.values)
    if kind == "bytes_value":
        return {"bytes_b64": base64.b64encode(value.bytes_value).decode("ascii")}
    return getattr(value, kind)


def _attributes(values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in values:
        if item.key in result:
            raise CaptureValidationError(f"duplicate attribute {item.key!r}")
        result[item.key] = _any_value(item.value)
    return result


def _json_sanitize_string(value: str, config: MaidaConfig) -> str:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return _truncate_string(value, config.max_field_bytes)
    return json.dumps(
        _sanitize(decoded, config),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _sanitize(
    value: Any, config: MaidaConfig, *, key: str | None = None, depth: int = 0
) -> Any:
    if depth > 12:
        return TRUNCATED_MARKER
    if (
        key in _TOKEN_COUNTERS
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return value
    if (
        key is not None
        and config.redact
        and _key_matches_redact(key, config.redact_keys)
    ):
        return REDACTED_MARKER
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if key in _JSON_CONTENT_FIELDS:
            return _json_sanitize_string(value, config)
        return _truncate_string(value, config.max_field_bytes)
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize(
                child_value,
                config,
                key=str(child_key),
                depth=depth + 1,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, config, depth=depth + 1) for item in value]
    return _truncate_string(str(value), config.max_field_bytes)


def _session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _require_service(resource: dict[str, Any]) -> None:
    if resource.get("service.name") != _SERVICE_NAME:
        raise CaptureValidationError("resource service.name must be 'claude-code'")


def _session_id(resource: dict[str, Any], attributes: dict[str, Any]) -> str:
    value = attributes.get("session.id", resource.get("session.id"))
    if not isinstance(value, str) or not value.strip():
        raise CaptureValidationError("session.id must be a nonempty string")
    return value.strip()


def _event_name(record: Any, attributes: dict[str, Any], body: Any) -> str:
    value = getattr(record, "event_name", "") or attributes.get("event.name")
    if not value and isinstance(body, str):
        value = body
    if not isinstance(value, str) or not value.strip():
        raise CaptureValidationError("log record is missing event.name")
    value = value.strip()
    if not value.startswith("claude_code."):
        value = f"claude_code.{value}"
    return value


def _validate_fields(
    signal_name: str,
    attributes: dict[str, Any],
    *,
    known_names: frozenset[str],
    required: dict[str, tuple[str, ...]],
) -> None:
    if signal_name not in known_names:
        return
    missing = [
        field for field in required.get(signal_name, ()) if field not in attributes
    ]
    if missing:
        raise CaptureValidationError(
            f"{signal_name} is missing required field(s): {', '.join(missing)}"
        )
    for field in _NONNEGATIVE_FIELDS:
        value = attributes.get(field)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise CaptureValidationError(
                f"{signal_name} field {field!r} must be nonnegative"
            )
    if signal_name == "claude_code.api_error" and attributes.get("attempt", 0) < 1:
        raise CaptureValidationError("claude_code.api_error attempt must be at least 1")
    if signal_name in {"claude_code.tool_result", "claude_code.tool.execution"}:
        if not isinstance(attributes.get("success"), (bool, str)):
            raise CaptureValidationError(f"{signal_name} success must be boolean-like")
    if signal_name == "claude_code.tool_decision" and attributes.get(
        "decision"
    ) not in {
        "accept",
        "reject",
    }:
        raise CaptureValidationError("claude_code.tool_decision has invalid decision")


def _coerce_known_scalars(attributes: dict[str, Any]) -> dict[str, Any]:
    """Normalize Claude's OTel log string encoding for numeric signal fields."""
    normalized = dict(attributes)
    for field in _NONNEGATIVE_FIELDS:
        value = normalized.get(field)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        try:
            if any(character in text.lower() for character in (".", "e")):
                normalized[field] = float(text)
            else:
                normalized[field] = int(text)
        except ValueError:
            pass
    return normalized


def _valid_trace_id(value: bytes, *, optional: bool = False) -> str:
    if optional and not value:
        return ""
    if len(value) != 16 or not any(value):
        raise CaptureValidationError("trace_id must be a nonzero 16-byte identifier")
    return value.hex()


def _valid_span_id(value: bytes, *, optional: bool = False) -> str:
    if optional and not value:
        return ""
    if len(value) != 8 or not any(value):
        raise CaptureValidationError("span_id must be a nonzero 8-byte identifier")
    return value.hex()


def _scope(scope: Any) -> dict[str, Any]:
    return {
        "name": scope.name,
        "version": scope.version,
        "attributes": _attributes(scope.attributes),
        "dropped_attributes_count": scope.dropped_attributes_count,
    }


def _decode_logs(
    request: ExportLogsServiceRequest, config: MaidaConfig
) -> list[dict[str, Any]]:
    decoded: list[dict[str, Any]] = []
    for resource_logs in request.resource_logs:
        resource = _attributes(resource_logs.resource.attributes)
        _require_service(resource)
        for scope_logs in resource_logs.scope_logs:
            scope = _scope(scope_logs.scope)
            for record in scope_logs.log_records:
                attributes = _coerce_known_scalars(_attributes(record.attributes))
                session_id = _session_id(resource, attributes)
                body = _any_value(record.body)
                event_name = _event_name(record, attributes, body)
                _validate_fields(
                    event_name,
                    attributes,
                    known_names=_KNOWN_LOG_EVENTS,
                    required=_REQUIRED_LOG_FIELDS,
                )
                trace_id = _valid_trace_id(record.trace_id, optional=True)
                span_id = _valid_span_id(record.span_id, optional=True)
                session_hash = _session_hash(session_id)
                attributes["session.id"] = session_hash
                resource = dict(resource)
                if "session.id" in resource:
                    resource["session.id"] = session_hash
                decoded.append(
                    _sanitize(
                        {
                            "resource": {
                                "attributes": resource,
                                "dropped_attributes_count": resource_logs.resource.dropped_attributes_count,
                                "schema_url": resource_logs.schema_url,
                            },
                            "scope": scope,
                            "record": {
                                "event_name": event_name,
                                "time_unix_nano": record.time_unix_nano,
                                "observed_time_unix_nano": record.observed_time_unix_nano,
                                "severity_number": record.severity_number,
                                "severity_text": record.severity_text,
                                "body": body,
                                "attributes": attributes,
                                "dropped_attributes_count": record.dropped_attributes_count,
                                "flags": record.flags,
                                "trace_id": trace_id,
                                "span_id": span_id,
                            },
                            "session_hash": session_hash,
                        },
                        config,
                    )
                )
    if not decoded:
        raise CaptureValidationError("OTLP logs batch is empty")
    return decoded


def _span_event(event: Any) -> dict[str, Any]:
    return {
        "time_unix_nano": event.time_unix_nano,
        "name": event.name,
        "attributes": _attributes(event.attributes),
        "dropped_attributes_count": event.dropped_attributes_count,
    }


def _span_link(link: Any) -> dict[str, Any]:
    return {
        "trace_id": _valid_trace_id(link.trace_id),
        "span_id": _valid_span_id(link.span_id),
        "trace_state": link.trace_state,
        "attributes": _attributes(link.attributes),
        "dropped_attributes_count": link.dropped_attributes_count,
        "flags": link.flags,
    }


def _status_code(status: Any) -> str:
    names = {0: "UNSET", 1: "OK", 2: "ERROR"}
    if status.code not in names:
        raise CaptureValidationError("span has invalid status code")
    return names[status.code]


def _nanos_iso(value: int) -> str:
    if value <= 0:
        raise CaptureValidationError("span timestamps must be positive")
    return (
        datetime.fromtimestamp(value / 1_000_000_000, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _decode_traces(
    request: ExportTraceServiceRequest, config: MaidaConfig
) -> list[dict[str, Any]]:
    decoded: list[dict[str, Any]] = []
    for resource_spans in request.resource_spans:
        resource = _attributes(resource_spans.resource.attributes)
        _require_service(resource)
        for scope_spans in resource_spans.scope_spans:
            scope = _scope(scope_spans.scope)
            for span in scope_spans.spans:
                attributes = _attributes(span.attributes)
                session_id = _session_id(resource, attributes)
                if span.name in _KNOWN_SPANS:
                    _validate_fields(
                        span.name,
                        attributes,
                        known_names=_KNOWN_SPANS,
                        required=_REQUIRED_SPAN_FIELDS,
                    )
                trace_id = _valid_trace_id(span.trace_id)
                span_id = _valid_span_id(span.span_id)
                parent_span_id = _valid_span_id(span.parent_span_id, optional=True)
                if span.end_time_unix_nano < span.start_time_unix_nano:
                    raise CaptureValidationError("span end time precedes start time")
                duration_ms = (
                    span.end_time_unix_nano - span.start_time_unix_nano
                ) // 1_000_000
                session_hash = _session_hash(session_id)
                attributes["session.id"] = session_hash
                resource = dict(resource)
                if "session.id" in resource:
                    resource["session.id"] = session_hash
                decoded.append(
                    _sanitize(
                        {
                            "resource": {
                                "attributes": resource,
                                "dropped_attributes_count": resource_spans.resource.dropped_attributes_count,
                                "schema_url": resource_spans.schema_url,
                            },
                            "scope": scope,
                            "span": {
                                "trace_id": trace_id,
                                "span_id": span_id,
                                "trace_state": span.trace_state,
                                "parent_span_id": parent_span_id or None,
                                "flags": span.flags,
                                "name": span.name,
                                "kind": int(span.kind),
                                "start_time_unix_nano": span.start_time_unix_nano,
                                "end_time_unix_nano": span.end_time_unix_nano,
                                "start_time": _nanos_iso(span.start_time_unix_nano),
                                "end_time": _nanos_iso(span.end_time_unix_nano),
                                "duration_ms": duration_ms,
                                "attributes": attributes,
                                "events": [_span_event(event) for event in span.events],
                                "links": [_span_link(link) for link in span.links],
                                "dropped_attributes_count": span.dropped_attributes_count,
                                "dropped_events_count": span.dropped_events_count,
                                "dropped_links_count": span.dropped_links_count,
                                "status_code": _status_code(span.status),
                                "status_description": span.status.message,
                            },
                            "session_hash": session_hash,
                        },
                        config,
                    )
                )
    if not decoded:
        raise CaptureValidationError("OTLP traces batch is empty")
    return decoded


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_identity(record: dict[str, Any], signal: str) -> str:
    source = record["record"] if signal == "logs" else record["span"]
    if signal == "spans":
        parts = (record["session_hash"], source["trace_id"], source["span_id"])
    else:
        attrs = source["attributes"]
        parts = (
            record["session_hash"],
            source["event_name"],
            attrs.get("event.sequence"),
            source.get("time_unix_nano"),
            source.get("trace_id"),
            source.get("span_id"),
            attrs.get("tool_use_id"),
            attrs.get("request_id"),
            attrs.get("prompt.id"),
        )
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CaptureConflictError(
                    f"existing capture {path} line {line_no} is invalid JSON"
                ) from exc
            if not isinstance(value, dict):
                raise CaptureConflictError(
                    f"existing capture {path} line {line_no} is not an object"
                )
            records.append(value)
    return records


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(_canonical(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


_PROCESS_LOCK = threading.RLock()


@contextmanager
def _capture_lock(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".claude-code.lock"
    with _PROCESS_LOCK, lock_path.open("a+") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _capture_dir(config: MaidaConfig, session_hash: str) -> Path:
    return (
        config.data_dir.expanduser()
        / "captures"
        / _SERVICE_NAME
        / session_hash
        / _SEGMENT
    )


def _store(
    records: list[dict[str, Any]], signal: str, config: MaidaConfig
) -> tuple[int, int]:
    filename = "logs.jsonl" if signal == "logs" else "spans.jsonl"
    groups: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        groups.setdefault(record["session_hash"], []).append(record)

    captures_root = config.data_dir.expanduser() / "captures"
    accepted = 0
    deduplicated = 0
    with _capture_lock(captures_root):
        planned: list[tuple[Path, list[dict[str, Any]], list[dict[str, Any]]]] = []
        for session_hash, incoming in groups.items():
            path = _capture_dir(config, session_hash) / filename
            existing = _read_jsonl(path)
            identities = {
                _record_identity(item, signal): _canonical(item) for item in existing
            }
            additions: list[dict[str, Any]] = []
            for item in incoming:
                identity = _record_identity(item, signal)
                canonical = _canonical(item)
                previous = identities.get(identity)
                if previous is None:
                    identities[identity] = canonical
                    additions.append(item)
                elif previous == canonical:
                    deduplicated += 1
                else:
                    raise CaptureConflictError(
                        "conflicting duplicate Claude Code telemetry identity"
                    )
            planned.append((path, existing, additions))

        now = utc_now_iso_ms_z()
        for path, existing, additions in planned:
            if additions:
                _atomic_jsonl(path, existing + additions)
                accepted += len(additions)
            segment_dir = path.parent
            manifest_path = segment_dir / "manifest.json"
            manifest = {
                "capture_version": 1,
                "source": _SERVICE_NAME,
                "session_hash": segment_dir.parent.name,
                "segment": _SEGMENT,
                "created_at": now,
                "updated_at": now,
                "signals": {},
            }
            if manifest_path.is_file():
                with manifest_path.open(encoding="utf-8") as stream:
                    current = json.load(stream)
                if isinstance(current, dict):
                    manifest.update(current)
                    manifest["updated_at"] = now
            signals = dict(manifest.get("signals") or {})
            signals[signal] = len(existing) + len(additions)
            manifest["signals"] = signals
            _atomic_json(manifest_path, manifest)
    return accepted, deduplicated


async def _body(request: Request, max_request_bytes: int) -> bytes:
    content_type = (
        request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    )
    if content_type not in _PROTOBUF_CONTENT_TYPES:
        raise HTTPException(
            status_code=415, detail="content type must be application/x-protobuf"
        )
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > max_request_bytes:
                raise HTTPException(status_code=413, detail="OTLP request is too large")
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="invalid Content-Length"
            ) from exc
    body = await request.body()
    if len(body) > max_request_bytes:
        raise HTTPException(status_code=413, detail="OTLP request is too large")
    if not body:
        raise HTTPException(status_code=400, detail="OTLP request body is empty")
    return body


def create_claude_code_app(
    config: MaidaConfig,
    *,
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES,
) -> FastAPI:
    """Create the isolated loopback OTLP application."""
    app = FastAPI(title="Maida Claude Code capture", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/logs")
    async def logs(request: Request) -> Response:
        body = await _body(request, max_request_bytes)
        message = ExportLogsServiceRequest()
        try:
            message.ParseFromString(body)
            decoded = _decode_logs(message, config)
            _store(decoded, "logs", config)
        except DecodeError as exc:
            raise HTTPException(
                status_code=400, detail="invalid OTLP logs protobuf"
            ) from exc
        except CaptureValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CaptureConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response = ExportLogsServiceResponse().SerializeToString()
        return Response(response, media_type="application/x-protobuf")

    @app.post("/v1/traces")
    async def traces(request: Request) -> Response:
        body = await _body(request, max_request_bytes)
        message = ExportTraceServiceRequest()
        try:
            message.ParseFromString(body)
            decoded = _decode_traces(message, config)
            _store(decoded, "spans", config)
        except DecodeError as exc:
            raise HTTPException(
                status_code=400, detail="invalid OTLP traces protobuf"
            ) from exc
        except CaptureValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except CaptureConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        response = ExportTraceServiceResponse().SerializeToString()
        return Response(response, media_type="application/x-protobuf")

    return app
