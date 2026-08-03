"""Read-only Langfuse observation import into Maida's local trace contract."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from maida._tracing._otel import (
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
from maida._tracing._redact import _redact_and_truncate
from maida.config import MaidaConfig
from maida.constants import SPEC_VERSION
from maida.loopdetect import detect_loop, pattern_key
from maida.storage import install_validated_run, load_validated_run


_OBSERVATION_FIELDS = "basic,time,io,metadata,model,usage,metrics,trace_context"
_MAPPING_VERSION = 1
_KNOWN_STRUCTURAL_TYPES = {
    "SPAN",
    "EVENT",
    "AGENT",
    "CHAIN",
    "RETRIEVER",
    "EVALUATOR",
    "EMBEDDING",
    "GUARDRAIL",
}


class LangfuseImportError(RuntimeError):
    """Langfuse data could not be fetched, normalized, or persisted safely."""


class LangfuseInputError(ValueError):
    """The user-provided Langfuse import selection is invalid."""


class IncompleteLangfuseTrace(LangfuseImportError):
    """A source trace still contains an unfinished non-event observation."""


@dataclass(frozen=True)
class NormalizedLangfuseRun:
    trace_id: str
    source_trace_id: str
    project_id: str
    source_fingerprint: str
    run_name: str
    meta: dict[str, Any]
    spans: list[dict[str, Any]]
    unmapped_observation_types: tuple[str, ...] = ()


@dataclass
class LangfuseImportSummary:
    imported: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    unmapped_observation_types: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "unmapped_observation_types": sorted(self.unmapped_observation_types),
        }


class LangfuseClient:
    """Small stdlib client for Langfuse's read-only observations API."""

    def __init__(
        self,
        base_url: str,
        public_key: str,
        secret_key: str,
        timeout: float = 5.0,
    ) -> None:
        parsed = urlsplit(base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise LangfuseInputError(
                "Langfuse base URL must be an http(s) origin without credentials, "
                "a query, or a fragment"
            )
        if not public_key or not secret_key:
            raise LangfuseInputError(
                "Set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY before importing"
            )
        if timeout <= 0:
            raise LangfuseInputError("LANGFUSE_TIMEOUT must be greater than zero")
        self.base_url = base_url.rstrip("/")
        self.public_key = public_key
        self.secret_key = secret_key
        self.timeout = timeout

    def fetch_observations(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Fetch every cursor page for a single observations query."""
        rows: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        cursor: str | None = None
        while True:
            page_params = {
                "limit": 1000,
                "fields": _OBSERVATION_FIELDS,
                **params,
            }
            if cursor is not None:
                page_params["cursor"] = cursor
            payload = self._get_json(page_params)
            data = payload.get("data")
            meta = payload.get("meta")
            if not isinstance(data, list) or not isinstance(meta, dict):
                raise LangfuseImportError(
                    "Langfuse observations response must contain data[] and meta{}"
                )
            for item in data:
                if not isinstance(item, dict):
                    raise LangfuseImportError(
                        "Langfuse observations response contains a non-object row"
                    )
                rows.append(item)
            next_cursor = meta.get("cursor")
            if next_cursor is None or next_cursor == "":
                break
            if not isinstance(next_cursor, str):
                raise LangfuseImportError(
                    "Langfuse observations cursor must be a string"
                )
            if next_cursor in seen_cursors:
                raise LangfuseImportError(
                    "Langfuse returned a repeated pagination cursor"
                )
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        return _deduplicate_observations(rows)

    def _get_json(self, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(params, doseq=True)
        url = f"{self.base_url}/api/public/v2/observations?{query}"
        credentials = base64.b64encode(
            f"{self.public_key}:{self.secret_key}".encode("utf-8")
        ).decode("ascii")
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {credentials}",
                "X-Langfuse-Sdk-Name": "maida",
            },
            method="GET",
        )
        host = urlsplit(self.base_url).netloc
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise LangfuseImportError(
                    f"Langfuse authentication failed at {host} (HTTP {exc.code})"
                ) from exc
            raise LangfuseImportError(
                f"Langfuse request failed at {host} (HTTP {exc.code})"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise LangfuseImportError(
                f"Could not reach Langfuse at {host}: {type(exc).__name__}"
            ) from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LangfuseImportError(
                f"Langfuse returned malformed JSON from {host}"
            ) from exc
        if not isinstance(payload, dict):
            raise LangfuseImportError("Langfuse response root must be an object")
        return payload


def _deduplicate_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        observation_id = row.get("id")
        if not isinstance(observation_id, str) or not observation_id:
            raise LangfuseImportError("Langfuse observation is missing a string id")
        previous = by_id.get(observation_id)
        if previous is not None and previous != row:
            raise LangfuseImportError(
                f"Langfuse returned conflicting rows for observation {observation_id!r}"
            )
        if previous is None:
            order.append(observation_id)
            by_id[observation_id] = row
    return [by_id[observation_id] for observation_id in order]


def _hash_id(*parts: str, length: int) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return digest[:length]


def _parse_timestamp(value: object, *, field_name: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise LangfuseImportError(
            f"Langfuse observation field {field_name!r} must be an ISO-8601 timestamp"
        )
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise LangfuseImportError(
            f"Langfuse observation field {field_name!r} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise LangfuseImportError(
            f"Langfuse observation field {field_name!r} must include a timezone"
        )
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
    return json.dumps(
        _safe_source_value(value, config), ensure_ascii=False, default=str
    )


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
            raise IncompleteLangfuseTrace(
                f"incomplete observation {row.get('id')!r} has no endTime"
            )
        end = start
    else:
        end = _parse_timestamp(raw_end, field_name="endTime")
    if end < start:
        raise LangfuseImportError(
            f"Langfuse observation {row.get('id')!r} ends before it starts"
        )
    duration_ms = max(0, int((end - start).total_seconds() * 1000))
    level = str(row.get("level") or "DEFAULT").upper()
    is_error = level == "ERROR"
    status_message = str(row.get("statusMessage") or "")
    source_meta = _source_meta(row, config)
    attrs: dict[str, Any] = {MAIDA_META: _meta_attribute(source_meta, config)}
    events: list[dict[str, Any]] = []
    action_event: dict[str, Any] | None = None

    if observation_type == "GENERATION":
        model = str(
            row.get("providedModelName")
            or row.get("model")
            or row.get("name")
            or "unknown"
        )
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
            if isinstance(temperature, (int, float)) and not isinstance(
                temperature, bool
            ):
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
        if total_tokens is None and (
            input_tokens is not None or output_tokens is not None
        ):
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
                    "attributes": {
                        "content": _safe_source_value(row.get("input"), config)
                    },
                }
            )
        if row.get("output") is not None:
            events.append(
                {
                    "name": "gen_ai.assistant.message",
                    "timestamp": _iso_utc(end),
                    "attributes": {
                        "content": _safe_source_value(row.get("output"), config)
                    },
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
                    "attributes": {
                        "result": _json_event_value(row.get("output"), config)
                    },
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


def normalize_langfuse_trace(
    observations: list[dict[str, Any]], config: MaidaConfig
) -> NormalizedLangfuseRun:
    """Normalize one complete Langfuse trace into a strict Maida local run."""
    observations = _deduplicate_observations(observations)
    if not observations:
        raise LangfuseImportError("Cannot normalize an empty Langfuse trace")

    source_trace_ids = {row.get("traceId") for row in observations}
    project_ids = {row.get("projectId") for row in observations}
    if len(source_trace_ids) != 1 or not all(
        isinstance(item, str) and item for item in source_trace_ids
    ):
        raise LangfuseImportError("Observations must belong to one Langfuse trace")
    if len(project_ids) != 1 or not all(
        isinstance(item, str) and item for item in project_ids
    ):
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
                raise LangfuseImportError(
                    f"Langfuse trace contains a parent cycle at observation {current!r}"
                )
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
        parent_span_id = (
            span_ids[parent_observation_id]
            if parent_observation_id is not None
            else root_span_id
        )
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
        payload = detect_loop(
            action_window, config.loop_window, config.loop_repetitions
        )
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
    llm_calls = sum(
        1 for row in ordered if str(row.get("type")).upper() == "GENERATION"
    )
    tool_calls = sum(1 for row in ordered if str(row.get("type")).upper() == "TOOL")
    session_ids = sorted(
        {
            str(row["sessionId"])
            for row in ordered
            if isinstance(row.get("sessionId"), str) and row["sessionId"]
        }
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
        "status_description": (
            "Langfuse trace contains error observations" if errors else ""
        ),
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


def _parse_selection_time(value: str, *, option: str) -> str:
    try:
        parsed = _parse_timestamp(value, field_name=option)
    except LangfuseImportError as exc:
        raise LangfuseInputError(str(exc)) from exc
    return _iso_utc(parsed)


def _discovery_filter(
    *,
    from_time: str,
    to_time: str,
    trace_name: str | None,
    session_id: str | None,
    environments: tuple[str, ...],
) -> str:
    from_iso = _parse_selection_time(from_time, option="--from")
    to_iso = _parse_selection_time(to_time, option="--to")
    if _parse_timestamp(from_iso, field_name="--from") >= _parse_timestamp(
        to_iso, field_name="--to"
    ):
        raise LangfuseInputError("--from must be earlier than --to")
    filters: list[dict[str, Any]] = [
        {
            "type": "datetime",
            "column": "startTime",
            "operator": ">=",
            "value": from_iso,
        },
        {
            "type": "datetime",
            "column": "startTime",
            "operator": "<",
            "value": to_iso,
        },
    ]
    if trace_name:
        filters.append(
            {
                "type": "string",
                "column": "traceName",
                "operator": "=",
                "value": trace_name,
            }
        )
    if session_id:
        filters.append(
            {
                "type": "string",
                "column": "sessionId",
                "operator": "=",
                "value": session_id,
            }
        )
    if environments:
        filters.append(
            {
                "type": "stringOptions",
                "column": "environment",
                "operator": "any of",
                "value": list(environments),
            }
        )
    return json.dumps(filters, separators=(",", ":"))


def _existing_source_matches(run: NormalizedLangfuseRun, config: MaidaConfig) -> bool:
    try:
        _meta, spans = load_validated_run(run.trace_id, config)
    except FileNotFoundError:
        return False
    except Exception as exc:
        raise LangfuseImportError(
            f"Existing destination run {run.trace_id} is invalid"
        ) from exc
    root = next((span for span in spans if span.get("parent_span_id") is None), None)
    if root is None:
        raise LangfuseImportError(
            f"Existing destination run {run.trace_id} has no root span"
        )
    raw_meta = root.get("attributes", {}).get(MAIDA_META)
    try:
        source = json.loads(raw_meta)["langfuse"]
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise LangfuseImportError(
            f"Existing destination run {run.trace_id} is not the same Langfuse import"
        ) from exc
    if (
        source.get("trace_id") != run.source_trace_id
        or source.get("project_id") != run.project_id
    ):
        raise LangfuseImportError(
            f"Deterministic trace ID collision at destination {run.trace_id}"
        )
    if (
        source.get("mapping_version") != _MAPPING_VERSION
        or source.get("observation_fingerprint") != run.source_fingerprint
    ):
        raise LangfuseImportError(
            f"Langfuse source trace changed since destination {run.trace_id} was "
            "imported; refusing to overwrite the local run"
        )
    return True


def import_langfuse_traces(
    client: LangfuseClient,
    config: MaidaConfig,
    *,
    source_trace_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    trace_name: str | None = None,
    session_id: str | None = None,
    environments: tuple[str, ...] = (),
) -> LangfuseImportSummary:
    """Fetch, normalize, validate, and locally install selected Langfuse traces."""
    if source_trace_id:
        if any((from_time, to_time, trace_name, session_id, environments)):
            raise LangfuseInputError(
                "--trace-id cannot be combined with range or grouping filters"
            )
        source_ids = [source_trace_id]
        hydrated = {
            source_trace_id: client.fetch_observations({"traceId": source_trace_id})
        }
    else:
        if from_time is None or to_time is None:
            raise LangfuseInputError(
                "Pass --trace-id or both timezone-aware --from and --to values"
            )
        filter_json = _discovery_filter(
            from_time=from_time,
            to_time=to_time,
            trace_name=trace_name,
            session_id=session_id,
            environments=environments,
        )
        discovery_rows = client.fetch_observations({"filter": filter_json})
        source_ids = sorted(
            {
                str(row["traceId"])
                for row in discovery_rows
                if isinstance(row.get("traceId"), str) and row["traceId"]
            }
        )
        hydrated = {
            trace_id: client.fetch_observations({"traceId": trace_id})
            for trace_id in source_ids
        }
    if not source_ids:
        raise LangfuseInputError(
            "No Langfuse observations matched the selection. Check the time range "
            "and filters, or run `maida demo` to verify Maida locally."
        )

    summary = LangfuseImportSummary()
    prepared: list[NormalizedLangfuseRun] = []
    for trace_id in source_ids:
        rows = hydrated.get(trace_id) or []
        if not rows:
            summary.skipped.append(
                {"source_trace_id": trace_id, "reason": "no observations"}
            )
            continue
        try:
            run = normalize_langfuse_trace(rows, config)
        except IncompleteLangfuseTrace as exc:
            summary.skipped.append({"source_trace_id": trace_id, "reason": str(exc)})
            continue
        prepared.append(run)
        summary.unmapped_observation_types.update(run.unmapped_observation_types)

    pending: list[NormalizedLangfuseRun] = []
    for run in prepared:
        if _existing_source_matches(run, config):
            summary.skipped.append(
                {
                    "source_trace_id": run.source_trace_id,
                    "trace_id": run.trace_id,
                    "run_name": run.run_name,
                    "reason": "already imported",
                }
            )
            continue
        pending.append(run)

    for run in pending:
        try:
            install_validated_run(run.meta, run.spans, config)
        except FileExistsError as exc:
            raise LangfuseImportError(
                f"Destination run {run.trace_id} was created concurrently; rerun "
                "the import to verify it"
            ) from exc
        summary.imported.append(
            {
                "source_trace_id": run.source_trace_id,
                "trace_id": run.trace_id,
                "run_name": run.run_name,
            }
        )
    return summary


def client_from_environment(base_url: str | None = None) -> LangfuseClient:
    """Create a Langfuse client from the SDK-compatible environment variables."""
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    resolved_base_url = (
        (base_url or "").strip()
        or os.environ.get("LANGFUSE_BASE_URL", "").strip()
        or os.environ.get("LANGFUSE_HOST", "").strip()
        or "https://cloud.langfuse.com"
    )
    timeout_text = os.environ.get("LANGFUSE_TIMEOUT", "5").strip()
    try:
        timeout = float(timeout_text)
    except ValueError as exc:
        raise LangfuseInputError("LANGFUSE_TIMEOUT must be a number") from exc
    return LangfuseClient(
        base_url=resolved_base_url,
        public_key=public_key,
        secret_key=secret_key,
        timeout=timeout,
    )
