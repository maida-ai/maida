"""Shared Claude projection primitives and terminal interpretation."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from maida.capture.common import _sanitize
from maida.config import MaidaConfig
from maida.integrations._claude_code.loading import _parse_time, _source_identity
from maida.integrations._claude_code.types import _MAPPING_VERSION, ClaudeCaptureInputError, ClaudeCaptureSegment


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
    return datetime.fromtimestamp(nanos / 1000000000, timezone.utc)


def _meta_json(payload: dict[str, Any], config: MaidaConfig) -> str:
    return json.dumps(_sanitize(payload, config), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
        while current is not None and current in parents and (current not in resolved):
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


def _terminal_outcome(capture: ClaudeCaptureSegment) -> tuple[str, str]:
    """Use top-level completion evidence, never a child operation's failure."""
    interactions = [
        item["span"]
        for item in capture.spans
        if item["span"]["name"] == "claude_code.interaction" and (not item["span"].get("parent_span_id"))
    ]
    if interactions:
        terminal = max(
            interactions,
            key=lambda span: (
                _parse_time(span["end_time"], "span end_time"),
                _parse_time(span["start_time"], "span start_time"),
                span["span_id"],
            ),
        )
        if terminal["status_code"] == "ERROR":
            return ("ERROR", str(terminal.get("status_description") or "Claude Code interaction failed"))
        return ("OK", "")
    session_ends = [item for item in capture.logs if item["record"]["event_name"] == "claude_code.hook.session_end"]
    if session_ends:
        terminal = max(session_ends, key=lambda item: (_log_time(item), _source_identity(item, "logs")))
        reason = terminal["record"]["attributes"].get("reason")
        if reason in {"clear", "resume", "logout", "prompt_input_exit", "other", "bypass_permissions_disabled"}:
            return ("OK", "")
    return ("UNSET", "Claude Code capture has no recognized terminal outcome")
