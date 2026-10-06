"""Claude normalization: hook tools."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from maida._tracing.attributes import MAIDA_ERROR_MESSAGE, MAIDA_ERROR_TYPE, MAIDA_META, MAIDA_TOOL_NAME
from maida.capture.common import _sanitize
from maida.integrations._claude_code.context import NormalizationContext
from maida.integrations._claude_code.loading import _source_identity
from maida.integrations._claude_code.projection import (
    _iso,
    _log_time,
    _meta_json,
    _normalized_span,
    _service_version,
    _source_meta,
    _tool_args,
)
from maida.integrations._claude_code.types import _HOOK_TOOL_LOGS, ClaudeCaptureInputError


def project_hook_tools(context: NormalizationContext) -> None:
    context.ordered_logs = sorted(
        context.capture.logs,
        key=lambda item: (
            _log_time(item),
            int(item["record"]["attributes"].get("event.sequence", 0)),
            _source_identity(item, "logs"),
        ),
    )
    hook_tools: dict[str, list[dict[str, Any]]] = {}
    for item in context.ordered_logs:
        record = item["record"]
        if record["event_name"] not in _HOOK_TOOL_LOGS:
            continue
        tool_use_id = record["attributes"].get("tool_use_id")
        if isinstance(tool_use_id, str) and tool_use_id:
            hook_tools.setdefault(tool_use_id, []).append(item)
    context.consumed_hook_logs: set[str] = set()
    for tool_use_id, source_logs in hook_tools.items():
        pre = next(
            (item for item in source_logs if item["record"]["event_name"] == "claude_code.hook.pre_tool_use"), None
        )
        terminals = [item for item in source_logs if item["record"]["event_name"] != "claude_code.hook.pre_tool_use"]
        if len(terminals) > 1:
            raise ClaudeCaptureInputError(f"hook tool {tool_use_id!r} has conflicting terminal events")
        terminal = terminals[0] if terminals else None
        names = {item["record"]["attributes"].get("tool_name") for item in source_logs}
        if len(names) != 1 or not all((isinstance(name, str) and name for name in names)):
            raise ClaudeCaptureInputError(f"hook tool {tool_use_id!r} has conflicting tool names")
        primary = terminal or pre
        if primary is None:
            continue
        merged_attributes: dict[str, Any] = {}
        if pre is not None:
            merged_attributes.update(pre["record"]["attributes"])
        if terminal is not None:
            merged_attributes.update(terminal["record"]["attributes"])
        tool_name = str(merged_attributes["tool_name"])
        prompt_id = merged_attributes.get("prompt.id")
        group = f"prompt:{prompt_id or 'session'}"
        interaction_id = context.ensure_interaction(group)
        end = _log_time(primary)
        duration = merged_attributes.get("duration_ms", 0)
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            duration = 0
        duration_start = end - timedelta(milliseconds=duration)
        start = min(_log_time(pre), duration_start) if pre is not None else duration_start
        context.interaction_times.setdefault(group, []).extend((start, end))
        terminal_name = terminal["record"]["event_name"] if terminal else None
        terminal_missing = terminal is None
        pre_tool_missing = pre is None
        meta = _source_meta(
            source_kind="hook_pair",
            source_name="claude_code.hook.tool_call",
            attributes=merged_attributes,
            service_version=_service_version(primary),
            source_record_identity=tool_use_id,
        )
        meta.update(
            {
                "terminal_missing": terminal_missing,
                "pre_tool_missing": pre_tool_missing,
                "source_logs": [
                    {
                        "event_name": item["record"]["event_name"],
                        "record_identity": _source_identity(item, "logs"),
                        "attributes": item["record"]["attributes"],
                    }
                    for item in source_logs
                ],
            }
        )
        attrs: dict[str, Any] = {
            MAIDA_TOOL_NAME: tool_name,
            MAIDA_META: _meta_json({"claude_code": meta}, context.config),
        }
        args = _sanitize(_tool_args(merged_attributes), context.config)
        events = [
            {
                "name": "maida.tool.args",
                "timestamp": _iso(start),
                "attributes": {"args": json.dumps(args, ensure_ascii=False)},
            }
        ]
        if terminal_name == "claude_code.hook.post_tool_use":
            result = _sanitize(merged_attributes.get("tool_response"), context.config)
            events.append(
                {
                    "name": "maida.tool.result",
                    "timestamp": _iso(end),
                    "attributes": {"result": json.dumps(result, ensure_ascii=False)},
                }
            )
        status = "UNSET" if terminal_missing else "OK"
        status_description = ""
        if terminal_name == "claude_code.hook.post_tool_use_failure":
            status = "ERROR"
            status_description = str(merged_attributes.get("error") or "Claude Code tool failed")
            attrs[MAIDA_ERROR_TYPE] = (
                "ClaudeCodeToolInterrupted" if merged_attributes.get("is_interrupt") is True else "ClaudeCodeToolError"
            )
            attrs[MAIDA_ERROR_MESSAGE] = status_description
        elif terminal_name == "claude_code.hook.permission_denied":
            status = "ERROR"
            status_description = str(merged_attributes.get("reason") or "Claude Code tool was denied")
            attrs[MAIDA_ERROR_TYPE] = "ClaudeCodePermissionDenied"
            attrs[MAIDA_ERROR_MESSAGE] = status_description
        projected = _normalized_span(
            trace_id=context.trace_id,
            span_id=context.span_id(f"hook-tool:{tool_use_id}"),
            parent_span_id=interaction_id,
            name=tool_name,
            start=start,
            end=end,
            kind="INTERNAL",
            attributes=attrs,
            events=events,
            status_code=status,
            status_description=status_description,
        )
        context.normalized.append(projected)
        context.action_spans.append(projected)
        context.consumed_hook_logs.update((_source_identity(item, "logs") for item in source_logs))
