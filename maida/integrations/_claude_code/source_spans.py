"""Claude normalization: source spans."""

from __future__ import annotations

import json
from typing import Any

from maida._tracing.attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_FINISH_REASONS,
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
from maida.integrations._claude_code.context import NormalizationContext
from maida.integrations._claude_code.loading import _parse_time
from maida.integrations._claude_code.projection import (
    _cycle_members,
    _iso,
    _meta_json,
    _normalized_span,
    _service_version,
    _source_meta,
    _tool_args,
)
from maida.integrations._claude_code.types import _KIND_NAMES


def project_source_spans(context: NormalizationContext) -> None:
    ordered_source_spans = sorted(
        context.capture.spans,
        key=lambda item: (
            _parse_time(item["span"]["start_time"], "span start_time"),
            item["span"]["trace_id"],
            item["span"]["span_id"],
        ),
    )
    source_by_key = {f"{item['span']['trace_id']}:{item['span']['span_id']}": item for item in ordered_source_spans}
    normalized_ids = {key: context.span_id(f"source-span:{key}") for key in source_by_key}
    parents: dict[str, str | None] = {}
    for key, item in source_by_key.items():
        source = item["span"]
        parent_key = f"{source['trace_id']}:{source['parent_span_id']}" if source.get("parent_span_id") else None
        parents[key] = parent_key if parent_key in source_by_key else None
    cycle_members = _cycle_members(parents)
    for key, item in source_by_key.items():
        source = item["span"]
        if source["name"] == "claude_code.interaction":
            group = f"trace:{source['trace_id']}"
            context.interaction_by_group.setdefault(group, normalized_ids[key])
            context.actual_interactions.add(normalized_ids[key])
    for key, item in source_by_key.items():
        source = item["span"]
        source_name = source["name"]
        group = f"trace:{source['trace_id']}"
        interaction_id = context.ensure_interaction(group)
        start = _parse_time(source["start_time"], "span start_time")
        end = _parse_time(source["end_time"], "span end_time")
        context.interaction_times.setdefault(group, []).extend((start, end))
        is_interaction = source_name == "claude_code.interaction"
        broken = key in cycle_members
        if is_interaction:
            parent_id = context.root_span_id
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
        attrs: dict[str, Any] = {MAIDA_META: _meta_json({"claude_code": meta}, context.config)}
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
                    GEN_AI_SYSTEM: str(source_attributes.get("gen_ai.system") or "anthropic"),
                    GEN_AI_REQUEST_MODEL: model,
                }
            )
            input_tokens = source_attributes.get("input_tokens")
            output_tokens = source_attributes.get("output_tokens")
            if isinstance(input_tokens, (int, float)) and (not isinstance(input_tokens, bool)):
                attrs[GEN_AI_USAGE_INPUT_TOKENS] = int(input_tokens)
            if isinstance(output_tokens, (int, float)) and (not isinstance(output_tokens, bool)):
                attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = int(output_tokens)
            if GEN_AI_USAGE_INPUT_TOKENS in attrs or GEN_AI_USAGE_OUTPUT_TOKENS in attrs:
                attrs[GEN_AI_USAGE_TOTAL_TOKENS] = attrs.get(GEN_AI_USAGE_INPUT_TOKENS, 0) + attrs.get(
                    GEN_AI_USAGE_OUTPUT_TOKENS, 0
                )
            if source_attributes.get("request_id"):
                attrs[GEN_AI_RESPONSE_ID] = str(source_attributes["request_id"])
            if source_attributes.get("stop_reason"):
                attrs[GEN_AI_RESPONSE_FINISH_REASONS] = str(source_attributes["stop_reason"])
            if source_attributes.get("success") is False:
                status = "ERROR"
            is_action = True
        elif source_name == "claude_code.tool":
            tool_name = str(source_attributes.get("tool_name") or "unknown")
            name = tool_name
            attrs[MAIDA_TOOL_NAME] = tool_name
            args = _sanitize(_tool_args(source_attributes), context.config)
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
            attrs[MAIDA_ERROR_TYPE] = str(source_attributes.get("error_type") or "ClaudeCodeError")
            attrs[MAIDA_ERROR_MESSAGE] = str(source_attributes.get("error") or status_description)
        projected = _normalized_span(
            trace_id=context.trace_id,
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
        context.normalized.append(projected)
        context.normalized_by_source[source["trace_id"], source["span_id"]] = projected
        if is_action:
            context.action_spans.append(projected)
