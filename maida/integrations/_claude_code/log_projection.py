"""Claude normalization: log projection."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from maida._tracing.attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_ID,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    MAIDA_ERROR_MESSAGE,
    MAIDA_ERROR_TYPE,
    MAIDA_META,
    MAIDA_TOOL_NAME,
)
from maida.capture.common import _sanitize
from maida.events import span_to_event_dict
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
    _truthy,
)


def project_logs(context: NormalizationContext) -> None:
    tool_results = {
        item["record"]["attributes"].get("tool_use_id")
        for item in context.ordered_logs
        if item["record"]["event_name"] == "claude_code.tool_result"
    }

    def log_group(item: dict[str, Any]) -> str:
        record = item["record"]
        if record.get("trace_id"):
            return f"trace:{record['trace_id']}"
        prompt_id = record["attributes"].get("prompt.id")
        return f"prompt:{prompt_id or 'session'}"

    for item in context.ordered_logs:
        record = item["record"]
        if _source_identity(item, "logs") in context.consumed_hook_logs:
            continue
        event_name = record["event_name"]
        attributes = record["attributes"]
        when = _log_time(item)
        group = log_group(item)
        interaction_id = context.ensure_interaction(group)
        context.interaction_times.setdefault(group, []).append(when)
        correlated = context.normalized_by_source.get((record.get("trace_id", ""), record.get("span_id", "")))
        correlated_type = span_to_event_dict(correlated)["event_type"] if correlated else None
        consumed = False
        if event_name == "claude_code.user_prompt" and correlated is not None:
            consumed = True
        elif (
            event_name in {"claude_code.api_request", "claude_code.api_error", "claude_code.assistant_response"}
            and correlated_type == "LLM_CALL"
        ):
            consumed = True
            for source_key, target_key in (
                ("input_tokens", GEN_AI_USAGE_INPUT_TOKENS),
                ("output_tokens", GEN_AI_USAGE_OUTPUT_TOKENS),
            ):
                value = attributes.get(source_key)
                if isinstance(value, (int, float)) and (not isinstance(value, bool)):
                    correlated["attributes"][target_key] = int(value)
            if (
                GEN_AI_USAGE_INPUT_TOKENS in correlated["attributes"]
                or GEN_AI_USAGE_OUTPUT_TOKENS in correlated["attributes"]
            ):
                correlated["attributes"][GEN_AI_USAGE_TOTAL_TOKENS] = correlated["attributes"].get(
                    GEN_AI_USAGE_INPUT_TOKENS, 0
                ) + correlated["attributes"].get(GEN_AI_USAGE_OUTPUT_TOKENS, 0)
            if event_name == "claude_code.api_error":
                correlated["status_code"] = "ERROR"
                correlated["attributes"][MAIDA_ERROR_TYPE] = "ClaudeCodeAPIError"
                correlated["attributes"][MAIDA_ERROR_MESSAGE] = str(
                    attributes.get("error") or "Claude Code API request failed"
                )
        elif event_name in {"claude_code.tool_result", "claude_code.tool_decision"} and correlated_type == "TOOL_CALL":
            consumed = True
            if event_name == "claude_code.tool_result":
                args = _sanitize(_tool_args(attributes), context.config)
                correlated["events"] = [
                    event for event in correlated["events"] if event.get("name") != "maida.tool.args"
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
                correlated["attributes"][MAIDA_META] = _meta_json(meta, context.config)
            continue
        if event_name == "claude_code.user_prompt":
            continue
        if event_name == "claude_code.tool_decision" and attributes.get("tool_use_id") in tool_results:
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
        attrs = {MAIDA_META: _meta_json({"claude_code": meta}, context.config)}
        events: list[dict[str, Any]] = []
        name = event_name
        status = "OK"
        status_description = ""
        duration = attributes.get("duration_ms", 0)
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            duration = 0
        end = when
        start = when if duration == 0 else when - timedelta(milliseconds=duration)
        context.interaction_times[group].extend((start, end))
        is_action = False
        if event_name in {"claude_code.api_request", "claude_code.api_error"}:
            model = str(attributes.get("model") or "unknown")
            name = model
            attrs.update({GEN_AI_OPERATION_NAME: "chat", GEN_AI_SYSTEM: "anthropic", GEN_AI_REQUEST_MODEL: model})
            input_tokens = attributes.get("input_tokens")
            output_tokens = attributes.get("output_tokens")
            if isinstance(input_tokens, (int, float)) and (not isinstance(input_tokens, bool)):
                attrs[GEN_AI_USAGE_INPUT_TOKENS] = int(input_tokens)
            if isinstance(output_tokens, (int, float)) and (not isinstance(output_tokens, bool)):
                attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = int(output_tokens)
            attrs[GEN_AI_USAGE_TOTAL_TOKENS] = attrs.get(GEN_AI_USAGE_INPUT_TOKENS, 0) + attrs.get(
                GEN_AI_USAGE_OUTPUT_TOKENS, 0
            )
            if attributes.get("request_id"):
                attrs[GEN_AI_RESPONSE_ID] = str(attributes["request_id"])
            if event_name == "claude_code.api_error":
                status = "ERROR"
                attrs[MAIDA_ERROR_TYPE] = "ClaudeCodeAPIError"
                attrs[MAIDA_ERROR_MESSAGE] = str(attributes.get("error") or "API error")
            is_action = True
        elif event_name == "claude_code.tool_result" or (
            event_name == "claude_code.tool_decision" and attributes.get("decision") == "reject"
        ):
            tool_name = str(attributes.get("tool_name") or "unknown")
            name = tool_name
            attrs[MAIDA_TOOL_NAME] = tool_name
            args = _sanitize(_tool_args(attributes), context.config)
            events.append(
                {
                    "name": "maida.tool.args",
                    "timestamp": _iso(start),
                    "attributes": {"args": json.dumps(args, ensure_ascii=False)},
                }
            )
            failed = event_name == "claude_code.tool_decision" or not _truthy(attributes.get("success"))
            if failed:
                status = "ERROR"
                attrs[MAIDA_ERROR_TYPE] = str(attributes.get("error_type") or "ClaudeCodeToolError")
                attrs[MAIDA_ERROR_MESSAGE] = str(attributes.get("error") or "Claude Code tool was rejected")
            is_action = True
        projected = _normalized_span(
            trace_id=context.trace_id,
            span_id=context.span_id(f"source-log:{_source_identity(item, 'logs')}"),
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
        context.normalized.append(projected)
        if is_action:
            context.action_spans.append(projected)
