"""
Event type naming and span-type helpers for the OTel-based trace format.

Provides:
- EventType enum (preserved for consumer backward compat: derived from span properties)
- new_event() (preserved for guardrail/loop detection backward compat)
- utc_now_iso_ms_z(): ISO8601 timestamp helper
- derive_span_type(): Classify a span dict as "RUN", "LLM_CALL", "TOOL_CALL", etc.
- Build helper functions for span attributes
"""

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from maida.constants import DEPTH_LIMIT, SPEC_VERSION, TRUNCATED_MARKER


class EventType(str, Enum):
    """Event type enum preserved for consumer backward compatibility.

    These are now derived from OTel span properties rather than being
    explicit write-time labels. Consumers can still filter by event_type.
    """

    RUN_START = "RUN_START"
    RUN_END = "RUN_END"
    LLM_CALL = "LLM_CALL"
    TOOL_CALL = "TOOL_CALL"
    STATE_UPDATE = "STATE_UPDATE"
    ERROR = "ERROR"
    LOOP_WARNING = "LOOP_WARNING"


_MAX_JSON_DEPTH = DEPTH_LIMIT


def _json_safe_value(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_JSON_DEPTH:
        return TRUNCATED_MARKER
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item, depth + 1) for item in value]
    return str(value)


def _ensure_json_safe(obj: Any) -> Any:
    return _json_safe_value(obj, 0)


def new_event(
    event_type: EventType | str,
    run_id: str,
    name: str,
    payload: Any,
    parent_id: str | None = None,
    duration_ms: int | None = None,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an event dict (backward compat for guardrail/loop detection)."""
    type_str = (
        event_type.value if isinstance(event_type, EventType) else str(event_type)
    )
    event_id = str(uuid.uuid4())
    ts = utc_now_iso_ms_z()
    safe_payload = _ensure_json_safe(payload) if payload is not None else {}
    if not isinstance(safe_payload, dict):
        safe_payload = {"value": safe_payload}
    safe_meta = _ensure_json_safe(meta) if meta is not None else {}
    if not isinstance(safe_meta, dict):
        safe_meta = {"value": safe_meta}
    return {
        "spec_version": SPEC_VERSION,
        "event_id": event_id,
        "run_id": run_id,
        "parent_id": parent_id,
        "event_type": type_str,
        "ts": ts,
        "duration_ms": duration_ms,
        "name": str(name),
        "payload": safe_payload,
        "meta": safe_meta,
    }


def utc_now_iso_ms_z() -> str:
    """Return current UTC time as ISO8601 with millisecond precision and trailing Z."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def derive_span_type(span: dict) -> str:
    """Derive a Maida EventType string from an OTel span dict for consumer backward compat."""
    parent_id = span.get("parent_span_id")
    name = span.get("name", "")
    attrs = span.get("attributes", {})

    if parent_id is None:
        return EventType.RUN_START.value

    if "gen_ai.system" in attrs or GEN_AI_OPERATION_NAME in attrs:
        return EventType.LLM_CALL.value

    tool_name = attrs.get(MAIDA_TOOL_NAME, "")
    if tool_name:
        return EventType.TOOL_CALL.value

    if name == "state":
        return EventType.STATE_UPDATE.value

    if name == "loop_warning" or attrs.get(MAIDA_EVENT_TYPE) == "LOOP_WARNING":
        return EventType.LOOP_WARNING.value

    if MAIDA_ERROR_TYPE in attrs:
        return EventType.ERROR.value

    return "UNKNOWN"


def derive_event_payload(span: dict) -> dict[str, Any]:
    """Build a Maida-style event payload dict from an OTel span dict.

    This bridges the OTel format to the existing consumer interfaces
    (baseline, assertions, diff, loop detection) that expect the old
    payload structure.
    """
    span_type = derive_span_type(span)
    attrs = span.get("attributes", {})

    if span_type == EventType.RUN_START.value:
        return {
            "run_name": attrs.get("maida.run_name"),
            "python_version": attrs.get("maida.python_version"),
            "platform": attrs.get("maida.platform"),
            "cwd": attrs.get("maida.cwd"),
            "argv": attrs.get("maida.argv"),
        }

    if span_type == EventType.LLM_CALL.value:
        prompt = None
        response = None
        for ev in span.get("events", []):
            if ev.get("name") == "gen_ai.user.message":
                prompt = ev.get("attributes", {}).get("content")
            elif ev.get("name") == "gen_ai.assistant.message":
                response = ev.get("attributes", {}).get("content")
        error_obj = None
        if span.get("status_code") == "ERROR":
            error_obj = {
                "error_type": attrs.get(MAIDA_ERROR_TYPE, "Error"),
                "message": attrs.get(MAIDA_ERROR_MESSAGE, ""),
                "stack": attrs.get(MAIDA_ERROR_STACK),
            }
        prompt_tokens = attrs.get("gen_ai.usage.input_tokens")
        completion_tokens = attrs.get("gen_ai.usage.output_tokens")
        total_tokens = attrs.get("gen_ai.usage.total_tokens")
        if total_tokens is None and (
            prompt_tokens is not None or completion_tokens is not None
        ):
            total_tokens = (prompt_tokens or 0) + (completion_tokens or 0)
        return {
            "model": span.get("name", ""),
            "prompt": prompt,
            "response": response,
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            },
            "provider": attrs.get("gen_ai.system", "unknown"),
            "temperature": attrs.get("gen_ai.request.temperature"),
            "stop_reason": attrs.get("gen_ai.response.finish_reasons"),
            "status": "ok" if span.get("status_code") != "ERROR" else "error",
            "error": error_obj,
        }

    if span_type == EventType.TOOL_CALL.value:
        tool_args = None
        tool_result = None
        for ev in span.get("events", []):
            if ev.get("name") == "maida.tool.args":
                raw = ev.get("attributes", {}).get("args")
                if raw is not None:
                    try:
                        tool_args = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        tool_args = raw
            elif ev.get("name") == "maida.tool.result":
                raw = ev.get("attributes", {}).get("result")
                if raw is not None:
                    try:
                        tool_result = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        tool_result = raw
        error_obj = None
        if span.get("status_code") == "ERROR":
            error_obj = {
                "error_type": attrs.get(MAIDA_ERROR_TYPE, "Error"),
                "message": attrs.get(MAIDA_ERROR_MESSAGE, ""),
                "stack": attrs.get(MAIDA_ERROR_STACK),
            }
        return {
            "tool_name": attrs.get("maida.tool_name", span.get("name", "")),
            "args": tool_args,
            "result": tool_result,
            "status": "ok" if span.get("status_code") != "ERROR" else "error",
            "error": error_obj,
        }

    if span_type == EventType.STATE_UPDATE.value:
        state = None
        diff = None
        for ev in span.get("events", []):
            if ev.get("name") == "state":
                raw_state = ev.get("attributes", {}).get("state")
                if raw_state is not None:
                    try:
                        state = json.loads(raw_state)
                    except (json.JSONDecodeError, TypeError):
                        state = raw_state
                raw_diff = ev.get("attributes", {}).get("diff")
                if raw_diff is not None:
                    try:
                        diff = json.loads(raw_diff)
                    except (json.JSONDecodeError, TypeError):
                        diff = raw_diff
        return {
            "state": state,
            "diff": diff,
        }

    if span_type == EventType.LOOP_WARNING.value:
        loop_data = {}
        for ev in span.get("events", []):
            if ev.get("name") == "maida.loop.warning":
                for k, v in ev.get("attributes", {}).items():
                    loop_data[k] = v
        return loop_data

    if span_type == EventType.ERROR.value:
        return {
            "error_type": attrs.get(MAIDA_ERROR_TYPE, "Error"),
            "message": attrs.get(MAIDA_ERROR_MESSAGE, ""),
            "stack": attrs.get(MAIDA_ERROR_STACK),
        }

    return {}


def span_to_event_dict(span: dict) -> dict[str, Any]:
    """Convert a stored OTel span dict back into a Maida event-like dict (for backward compat)."""
    span_type = derive_span_type(span)
    payload = derive_event_payload(span)
    parent_span_id = span.get("parent_span_id")

    event_type = span_type
    if parent_span_id is None and event_type == EventType.RUN_START.value:
        event_type = EventType.RUN_START.value

    span_attrs = span.get("attributes", {})
    meta_val = {}
    raw_meta = span_attrs.get(MAIDA_META)
    if raw_meta is not None:
        try:
            meta_val = json.loads(raw_meta) if isinstance(raw_meta, str) else raw_meta
        except (json.JSONDecodeError, TypeError):
            meta_val = {"raw": raw_meta}
    if not isinstance(meta_val, dict):
        meta_val = {}

    return {
        "spec_version": SPEC_VERSION,
        "event_id": span.get("span_id", ""),
        "run_id": span.get("trace_id", ""),
        "parent_id": parent_span_id,
        "event_type": event_type,
        "ts": span.get("start_time", ""),
        "duration_ms": span.get("duration_ms"),
        "name": span.get("name", ""),
        "payload": payload,
        "meta": meta_val,
    }


def spans_to_events(spans: list[dict]) -> list[dict[str, Any]]:
    """Convert a list of OTel span dicts into a flat list of Maida event-like dicts.

    For each span, yields its main event via ``span_to_event_dict``.
    For the root span (no parent), also extracts:
      - ``exception`` events → ``ERROR`` event dicts
      - ``maida.loop.warning`` events → ``LOOP_WARNING`` event dicts
      - A synthetic ``RUN_END`` event (from the root span's end_time)
    """
    root_span: dict | None = None
    for span in spans:
        if span.get("parent_span_id") is None:
            root_span = span
            break

    events: list[dict[str, Any]] = []

    for span in spans:
        ev = span_to_event_dict(span)
        events.append(ev)

    if root_span is not None:
        span_start = root_span.get("start_time", "")
        span_end = root_span.get("end_time", "")
        root_trace_id = root_span.get("trace_id", "")
        root_span_id = root_span.get("span_id", "")

        for sev in root_span.get("events", []):
            name = sev.get("name", "")
            attrs = sev.get("attributes", {})
            ev_ts = sev.get("timestamp", span_end)

            if name == "exception":
                error_payload = {
                    "error_type": attrs.get(MAIDA_ERROR_TYPE, "Error"),
                    "message": attrs.get(MAIDA_ERROR_MESSAGE, ""),
                    "stack": attrs.get(MAIDA_ERROR_STACK),
                }
                events.append(
                    {
                        "spec_version": SPEC_VERSION,
                        "event_id": root_span_id,
                        "run_id": root_trace_id,
                        "parent_id": None,
                        "event_type": EventType.ERROR.value,
                        "ts": ev_ts,
                        "duration_ms": None,
                        "name": "exception",
                        "payload": error_payload,
                        "meta": {},
                    }
                )

            elif name == "state":
                state_payload = {}
                state_val = attrs.get("state")
                if state_val is not None:
                    try:
                        state_payload["state"] = json.loads(state_val)
                    except (json.JSONDecodeError, TypeError):
                        state_payload["state"] = state_val
                diff_val = attrs.get("diff")
                if diff_val is not None:
                    try:
                        state_payload["diff"] = json.loads(diff_val)
                    except (json.JSONDecodeError, TypeError):
                        state_payload["diff"] = diff_val
                events.append(
                    {
                        "spec_version": SPEC_VERSION,
                        "event_id": root_span_id,
                        "run_id": root_trace_id,
                        "parent_id": None,
                        "event_type": EventType.STATE_UPDATE.value,
                        "ts": ev_ts,
                        "duration_ms": None,
                        "name": "state",
                        "payload": state_payload,
                        "meta": {},
                    }
                )

            elif name == "maida.loop.warning":
                loop_payload = dict(attrs)
                events.append(
                    {
                        "spec_version": SPEC_VERSION,
                        "event_id": root_span_id,
                        "run_id": root_trace_id,
                        "parent_id": None,
                        "event_type": EventType.LOOP_WARNING.value,
                        "ts": ev_ts,
                        "duration_ms": None,
                        "name": "loop_warning",
                        "payload": loop_payload,
                        "meta": {},
                    }
                )

        root_attrs = root_span.get("attributes", {})
        run_name = root_attrs.get("maida.run_name")
        status_code = root_span.get("status_code", "UNSET")
        status = (
            "ok"
            if status_code == "OK"
            else "error"
            if status_code == "ERROR"
            else "unknown"
        )
        events.append(
            {
                "spec_version": SPEC_VERSION,
                "event_id": root_span_id,
                "run_id": root_trace_id,
                "parent_id": None,
                "event_type": EventType.RUN_END.value,
                "ts": span_end or span_start,
                "duration_ms": root_span.get("duration_ms"),
                "name": run_name or "",
                "payload": {"status": status},
                "meta": {},
            }
        )

    events.sort(key=lambda e: e.get("ts", ""))
    return events


try:
    from maida._tracing._otel import (
        GEN_AI_OPERATION_NAME,
        MAIDA_ERROR_TYPE,
        MAIDA_ERROR_MESSAGE,
        MAIDA_ERROR_STACK,
        MAIDA_EVENT_TYPE,
        MAIDA_META,
        MAIDA_TOOL_NAME,
    )
except ImportError:
    GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
    MAIDA_ERROR_TYPE = "maida.error_type"
    MAIDA_ERROR_MESSAGE = "maida.error_message"
    MAIDA_ERROR_STACK = "maida.error_stack"
    MAIDA_EVENT_TYPE = "maida.event_type"
    MAIDA_META = "maida.meta"
    MAIDA_TOOL_NAME = "maida.tool_name"
