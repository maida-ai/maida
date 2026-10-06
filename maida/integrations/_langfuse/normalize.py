"""Read-only langfuse import: normalize."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from maida._tracing.attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
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
from maida._tracing.redact import _redact_and_truncate
from maida.config import MaidaConfig
from maida.constants import SPEC_VERSION
from maida.integrations._langfuse.observations import _deduplicate_observations
from maida.integrations._langfuse.types import (
    _KNOWN_STRUCTURAL_TYPES,
    _MAPPING_VERSION,
    IncompleteLangfuseTrace,
    LangfuseImportError,
    NormalizedLangfuseRun,
)
from maida.loopdetect import detect_loop, pattern_key


def _hash_id(*parts: str, length: int) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LangfuseImportError(f"Langfuse observation field {field_name!r} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise LangfuseImportError(f"Langfuse observation field {field_name!r} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise LangfuseImportError(f"Langfuse observation field {field_name!r} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _safe_source_value(value: Any, config: MaidaConfig) -> Any:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            parsed = value
        value = parsed
    return _redact_and_truncate(value, config)


def _json_event_value(value: Any, config: MaidaConfig) -> str:
    return json.dumps(_safe_source_value(value, config), ensure_ascii=False, default=str)


def _meta_attribute(payload: dict[str, Any], config: MaidaConfig) -> str:
    return json.dumps(
        _redact_and_truncate(payload, config),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def _token_value(row: dict[str, Any], *keys: str) -> int | None:
    usage = row.get("usageDetails")
    usage = usage if isinstance(usage, dict) else {}
    for key in keys:
        value = usage.get(key)
        if value is None:
            value = row.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, (int, float)) and value >= 0:
            return int(value)
    return None


def _source_meta(row: dict[str, Any], config: MaidaConfig) -> dict[str, Any]:
    return _redact_and_truncate(
        {
            "langfuse": {
                "observation_id": row.get("id"),
                "observation_type": row.get("type"),
                "observation_name": row.get("name"),
                "trace_id": row.get("traceId"),
                "project_id": row.get("projectId"),
                "parent_observation_id": row.get("parentObservationId"),
                "is_root_observation": row.get("isRootObservation"),
                "session_id": row.get("sessionId"),
                "environment": row.get("environment"),
                "version": row.get("version"),
                "release": row.get("release"),
                "tags": row.get("tags") or [],
                "completion_start_time": row.get("completionStartTime"),
                "created_at": row.get("createdAt"),
                "updated_at": row.get("updatedAt"),
                "latency": row.get("latency"),
                "time_to_first_token": row.get("timeToFirstToken"),
                "usage_details": row.get("usageDetails") or {},
                "cost_details": row.get("costDetails") or {},
                "total_cost": row.get("totalCost"),
                "metadata": row.get("metadata") or {},
            }
        },
        config,
    )


def _normalized_observation_span(
    row: dict[str, Any],
    *,
    maida_trace_id: str,
    span_id: str,
    parent_span_id: str,
    config: MaidaConfig,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    observation_type = str(row.get("type") or "UNKNOWN").upper()
    start = _parse_timestamp(row.get("startTime"), field_name="startTime")
    raw_end = row.get("endTime")
    if raw_end is None:
        if observation_type != "EVENT":
            raise IncompleteLangfuseTrace(f"incomplete observation {row.get('id')!r} has no endTime")
        end = start
    else:
        end = _parse_timestamp(raw_end, field_name="endTime")
    if end < start:
        raise LangfuseImportError(f"Langfuse observation {row.get('id')!r} ends before it starts")
    duration_ms = max(0, int((end - start).total_seconds() * 1000))
    level = str(row.get("level") or "DEFAULT").upper()
    is_error = level == "ERROR"
    status_message = str(row.get("statusMessage") or "")
    source_meta = _source_meta(row, config)
    attrs: dict[str, Any] = {MAIDA_META: _meta_attribute(source_meta, config)}
    events: list[dict[str, Any]] = []
    action_event: dict[str, Any] | None = None

    if observation_type == "GENERATION":
        model = str(row.get("providedModelName") or row.get("model") or row.get("name") or "unknown")
        attrs.update(
            {
                GEN_AI_OPERATION_NAME: "chat",
                GEN_AI_SYSTEM: "unknown",
                GEN_AI_REQUEST_MODEL: model,
            }
        )
        model_parameters = row.get("modelParameters")
        for _ in range(2):
            if not isinstance(model_parameters, str):
                break
            try:
                model_parameters = json.loads(model_parameters)
            except (json.JSONDecodeError, TypeError):
                model_parameters = {}
                break
        if isinstance(model_parameters, dict):
            temperature = model_parameters.get("temperature")
            if isinstance(temperature, (int, float)) and not isinstance(temperature, bool):
                attrs[GEN_AI_REQUEST_TEMPERATURE] = temperature
        input_tokens = _token_value(
            row,
            "input",
            "input_tokens",
            "prompt_tokens",
            "promptTokens",
            "inputUsage",
        )
        output_tokens = _token_value(
            row,
            "output",
            "output_tokens",
            "completion_tokens",
            "completionTokens",
            "outputUsage",
        )
        total_tokens = _token_value(
            row,
            "total",
            "total_tokens",
            "totalTokens",
            "totalUsage",
        )
        if total_tokens is None and (input_tokens is not None or output_tokens is not None):
            total_tokens = (input_tokens or 0) + (output_tokens or 0)
        if input_tokens is not None:
            attrs[GEN_AI_USAGE_INPUT_TOKENS] = input_tokens
        if output_tokens is not None:
            attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = output_tokens
        if total_tokens is not None:
            attrs[GEN_AI_USAGE_TOTAL_TOKENS] = total_tokens
        if row.get("input") is not None:
            events.append(
                {
                    "name": "gen_ai.user.message",
                    "timestamp": _iso_utc(start),
                    "attributes": {"content": _safe_source_value(row.get("input"), config)},
                }
            )
        if row.get("output") is not None:
            events.append(
                {
                    "name": "gen_ai.assistant.message",
                    "timestamp": _iso_utc(end),
                    "attributes": {"content": _safe_source_value(row.get("output"), config)},
                }
            )
        name = model
        kind = "CLIENT"
        action_event = {
            "event_id": span_id,
            "event_type": "LLM_CALL",
            "payload": {"model": model},
            "ts": _iso_utc(start),
            "end_ts": _iso_utc(end),
        }
    elif observation_type == "TOOL":
        name = str(row.get("name") or "unknown_tool")
        attrs[MAIDA_TOOL_NAME] = name
        if row.get("input") is not None:
            events.append(
                {
                    "name": "maida.tool.args",
                    "timestamp": _iso_utc(start),
                    "attributes": {"args": _json_event_value(row.get("input"), config)},
                }
            )
        if row.get("output") is not None:
            events.append(
                {
                    "name": "maida.tool.result",
                    "timestamp": _iso_utc(end),
                    "attributes": {"result": _json_event_value(row.get("output"), config)},
                }
            )
        kind = "INTERNAL"
        action_event = {
            "event_id": span_id,
            "event_type": "TOOL_CALL",
            "payload": {
                "tool_name": name,
                "args": _safe_source_value(row.get("input"), config),
            },
            "ts": _iso_utc(start),
            "end_ts": _iso_utc(end),
        }
    else:
        name = str(row.get("name") or observation_type.lower())
        kind = "INTERNAL"

    if is_error:
        attrs[MAIDA_ERROR_TYPE] = "LangfuseObservationError"
        attrs[MAIDA_ERROR_MESSAGE] = _redact_and_truncate(
            status_message or "Langfuse observation reported ERROR", config
        )

    span = {
        "trace_id": maida_trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "name": _redact_and_truncate(name, config),
        "kind": kind,
        "start_time": _iso_utc(start),
        "end_time": _iso_utc(end),
        "duration_ms": duration_ms,
        "attributes": attrs,
        "events": events,
        "status_code": "ERROR" if is_error else "OK",
        "status_description": _redact_and_truncate(status_message, config),
    }
    return span, action_event


def normalize_langfuse_trace(observations: list[dict[str, Any]], config: MaidaConfig) -> NormalizedLangfuseRun:
    """Normalize one complete Langfuse trace into a strict Maida local run."""
    observations = _deduplicate_observations(observations)
    if not observations:
        raise LangfuseImportError("Cannot normalize an empty Langfuse trace")

    source_trace_ids = {row.get("traceId") for row in observations}
    project_ids = {row.get("projectId") for row in observations}
    if len(source_trace_ids) != 1 or not all(isinstance(item, str) and item for item in source_trace_ids):
        raise LangfuseImportError("Observations must belong to one Langfuse trace")
    if len(project_ids) != 1 or not all(isinstance(item, str) and item for item in project_ids):
        raise LangfuseImportError("Observations must belong to one Langfuse project")
    source_trace_id = next(iter(source_trace_ids))
    project_id = next(iter(project_ids))
    assert isinstance(source_trace_id, str)
    assert isinstance(project_id, str)

    ordered = sorted(
        observations,
        key=lambda row: (
            _parse_timestamp(row.get("startTime"), field_name="startTime"),
            str(row.get("id")),
        ),
    )
    source_fingerprint = _hash_id(
        f"mapping-v{_MAPPING_VERSION}",
        json.dumps(
            ordered,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
        length=64,
    )
    run_name = next(
        (
            str(row["traceName"])
            for row in ordered
            if isinstance(row.get("traceName"), str) and row["traceName"].strip()
        ),
        f"langfuse:{source_trace_id}",
    )
    maida_trace_id = _hash_id("langfuse", project_id, source_trace_id, length=32)
    root_span_id = _hash_id(maida_trace_id, "root", length=16)

    span_ids: dict[str, str] = {}
    used_span_ids = {root_span_id}
    for row in ordered:
        observation_id = str(row["id"])
        span_id = _hash_id(maida_trace_id, observation_id, length=16)
        if span_id in used_span_ids:
            raise LangfuseImportError("Deterministic Langfuse span ID collision")
        span_ids[observation_id] = span_id
        used_span_ids.add(span_id)

    parent_observation_ids: dict[str, str | None] = {}
    for row in ordered:
        observation_id = str(row["id"])
        candidate = row.get("parentObservationId")
        parent_observation_ids[observation_id] = (
            candidate if isinstance(candidate, str) and candidate in span_ids else None
        )
    resolved: set[str] = set()
    for observation_id in parent_observation_ids:
        chain: set[str] = set()
        current: str | None = observation_id
        while current is not None and current not in resolved:
            if current in chain:
                raise LangfuseImportError(f"Langfuse trace contains a parent cycle at observation {current!r}")
            chain.add(current)
            current = parent_observation_ids[current]
        resolved.update(chain)

    spans: list[dict[str, Any]] = []
    action_events: list[dict[str, Any]] = []
    errors = 0
    unmapped: set[str] = set()
    starts: list[datetime] = []
    ends: list[datetime] = []
    for row in ordered:
        observation_id = str(row["id"])
        parent_observation_id = parent_observation_ids[observation_id]
        parent_span_id = span_ids[parent_observation_id] if parent_observation_id is not None else root_span_id
        span, action_event = _normalized_observation_span(
            row,
            maida_trace_id=maida_trace_id,
            span_id=span_ids[observation_id],
            parent_span_id=parent_span_id,
            config=config,
        )
        spans.append(span)
        starts.append(_parse_timestamp(span["start_time"], field_name="startTime"))
        ends.append(_parse_timestamp(span["end_time"], field_name="endTime"))
        if span["status_code"] == "ERROR":
            errors += 1
        if action_event is not None:
            action_events.append(action_event)
        observation_type = str(row.get("type") or "UNKNOWN").upper()
        if observation_type not in {
            "GENERATION",
            "TOOL",
            *_KNOWN_STRUCTURAL_TYPES,
        }:
            unmapped.add(observation_type)

    loop_spans: list[dict[str, Any]] = []
    emitted_patterns: set[str] = set()
    action_window: list[dict[str, Any]] = []
    for action_event in sorted(action_events, key=lambda event: event["ts"]):
        action_window.append(action_event)
        if len(action_window) > config.loop_window:
            action_window = action_window[-config.loop_window :]
        payload = detect_loop(action_window, config.loop_window, config.loop_repetitions)
        if payload is None:
            continue
        key = pattern_key(payload)
        if key in emitted_patterns:
            continue
        emitted_patterns.add(key)
        warning_span_id = _hash_id(maida_trace_id, "loop", key, length=16)
        if warning_span_id in used_span_ids:
            raise LangfuseImportError("Deterministic loop-warning span ID collision")
        used_span_ids.add(warning_span_id)
        warning_ts = action_event["end_ts"]
        loop_spans.append(
            {
                "trace_id": maida_trace_id,
                "span_id": warning_span_id,
                "parent_span_id": root_span_id,
                "name": "loop_warning",
                "kind": "INTERNAL",
                "start_time": warning_ts,
                "end_time": warning_ts,
                "duration_ms": 0,
                "attributes": {MAIDA_EVENT_TYPE: "LOOP_WARNING"},
                "events": [
                    {
                        "name": "maida.loop.warning",
                        "timestamp": warning_ts,
                        "attributes": _redact_and_truncate(payload, config),
                    }
                ],
                "status_code": "OK",
                "status_description": "",
            }
        )
    spans.extend(loop_spans)

    root_start = min(starts)
    root_end = max(ends)
    llm_calls = sum(1 for row in ordered if str(row.get("type")).upper() == "GENERATION")
    tool_calls = sum(1 for row in ordered if str(row.get("type")).upper() == "TOOL")
    session_ids = sorted(
        {str(row["sessionId"]) for row in ordered if isinstance(row.get("sessionId"), str) and row["sessionId"]}
    )
    root_source_meta = {
        "langfuse": {
            "trace_id": source_trace_id,
            "project_id": project_id,
            "observation_fingerprint": source_fingerprint,
            "mapping_version": _MAPPING_VERSION,
            "session_ids": session_ids,
            "source": "api-v2-observations",
        }
    }
    root_attrs = {
        MAIDA_RUN_NAME: _redact_and_truncate(run_name, config),
        MAIDA_LLM_COUNT: llm_calls,
        MAIDA_TOOL_COUNT: tool_calls,
        MAIDA_ERROR_COUNT: errors,
        MAIDA_LOOP_WARNING_COUNT: len(loop_spans),
        MAIDA_META: _meta_attribute(root_source_meta, config),
    }
    root_span = {
        "trace_id": maida_trace_id,
        "span_id": root_span_id,
        "parent_span_id": None,
        "name": _redact_and_truncate(run_name, config),
        "kind": "INTERNAL",
        "start_time": _iso_utc(root_start),
        "end_time": _iso_utc(root_end),
        "duration_ms": max(0, int((root_end - root_start).total_seconds() * 1000)),
        "attributes": root_attrs,
        "events": [],
        "status_code": "ERROR" if errors else "OK",
        "status_description": ("Langfuse trace contains error observations" if errors else ""),
    }
    spans = [
        root_span,
        *sorted(spans, key=lambda span: (span["start_time"], span["span_id"])),
    ]
    meta = {
        "spec_version": SPEC_VERSION,
        "trace_id": maida_trace_id,
        "run_name": root_span["name"],
        "started_at": root_span["start_time"],
        "ended_at": root_span["end_time"],
        "duration_ms": root_span["duration_ms"],
        "status": "error" if errors else "ok",
        "counts": {
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "errors": errors,
            "loop_warnings": len(loop_spans),
        },
    }
    return NormalizedLangfuseRun(
        trace_id=maida_trace_id,
        source_trace_id=source_trace_id,
        project_id=project_id,
        source_fingerprint=source_fingerprint,
        run_name=str(root_span["name"]),
        meta=meta,
        spans=spans,
        unmapped_observation_types=tuple(sorted(unmapped)),
    )
