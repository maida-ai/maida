"""Claude normalization: assembly."""

from __future__ import annotations

from typing import Any

from maida._tracing.attributes import (
    MAIDA_ERROR_COUNT,
    MAIDA_EVENT_TYPE,
    MAIDA_LLM_COUNT,
    MAIDA_LOOP_WARNING_COUNT,
    MAIDA_META,
    MAIDA_RUN_NAME,
    MAIDA_TOOL_COUNT,
)
from maida.capture.common import _sanitize
from maida.constants import SPEC_VERSION
from maida.events import span_to_event_dict
from maida.integrations._claude_code.context import NormalizationContext
from maida.integrations._claude_code.loading import _parse_time
from maida.integrations._claude_code.projection import _iso, _meta_json, _normalized_span, _terminal_outcome
from maida.integrations._claude_code.types import _MAPPING_VERSION, ClaudeCaptureInputError, NormalizedClaudeRun
from maida.loopdetect import detect_loop, pattern_key


def assemble_run(context: NormalizationContext) -> NormalizedClaudeRun:
    for group, interaction_id in context.interaction_by_group.items():
        if interaction_id in context.actual_interactions:
            continue
        times = context.interaction_times.get(group)
        if not times:
            continue
        start, end = (min(times), max(times))
        context.normalized.append(
            _normalized_span(
                trace_id=context.trace_id,
                span_id=interaction_id,
                parent_span_id=context.root_span_id,
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
                                "service_version": context.capture.service_versions[-1]
                                if context.capture.service_versions
                                else None,
                            }
                        },
                        context.config,
                    )
                },
                events=[],
                status_code="OK",
                status_description="",
            )
        )
    context.action_spans.sort(key=lambda span: (span["start_time"], span["span_id"]))
    loop_spans: list[dict[str, Any]] = []
    action_window: list[dict[str, Any]] = []
    emitted: set[str] = set()
    for action in context.action_spans:
        event = span_to_event_dict(action)
        action_window.append(event)
        if len(action_window) > context.config.loop_window:
            action_window = action_window[-context.config.loop_window :]
        payload = detect_loop(action_window, context.config.loop_window, context.config.loop_repetitions)
        if payload is None:
            continue
        key = pattern_key(payload)
        if key in emitted:
            continue
        emitted.add(key)
        when = _parse_time(action["end_time"], "action end_time")
        loop_spans.append(
            _normalized_span(
                trace_id=context.trace_id,
                span_id=context.span_id(f"loop:{key}"),
                parent_span_id=context.root_span_id,
                name="loop_warning",
                start=when,
                end=when,
                kind="INTERNAL",
                attributes={MAIDA_EVENT_TYPE: "LOOP_WARNING"},
                events=[
                    {
                        "name": "maida.loop.warning",
                        "timestamp": _iso(when),
                        "attributes": _sanitize(payload, context.config),
                    }
                ],
                status_code="OK",
                status_description="",
            )
        )
    context.normalized.extend(loop_spans)
    if not context.normalized:
        raise ClaudeCaptureInputError("capture did not contain normalizable records")
    starts = [_parse_time(span["start_time"], "span start_time") for span in context.normalized]
    ends = [_parse_time(span["end_time"], "span end_time") for span in context.normalized]
    root_start, root_end = (min(starts), max(ends))
    llm_calls = sum((1 for span in context.action_spans if span_to_event_dict(span)["event_type"] == "LLM_CALL"))
    tool_calls = sum((1 for span in context.action_spans if span_to_event_dict(span)["event_type"] == "TOOL_CALL"))
    errors = sum((1 for span in context.normalized if span["status_code"] == "ERROR"))
    root_status, root_description = _terminal_outcome(context.capture)
    root_source = {
        "mapping_version": _MAPPING_VERSION,
        "source": "local-capture",
        "session_hash": context.capture.session_hash,
        "segment": context.capture.segment,
        "source_fingerprint": context.capture.source_fingerprint,
        "service_versions": list(context.capture.service_versions),
    }
    root_attrs = {
        MAIDA_RUN_NAME: context.run_name,
        MAIDA_LLM_COUNT: llm_calls,
        MAIDA_TOOL_COUNT: tool_calls,
        MAIDA_ERROR_COUNT: errors,
        MAIDA_LOOP_WARNING_COUNT: len(loop_spans),
        MAIDA_META: _meta_json({"claude_code": root_source}, context.config),
    }
    root = {
        "trace_id": context.trace_id,
        "span_id": context.root_span_id,
        "parent_span_id": None,
        "name": context.run_name,
        "kind": "INTERNAL",
        "start_time": _iso(root_start),
        "end_time": _iso(root_end),
        "duration_ms": max(0, int((root_end - root_start).total_seconds() * 1000)),
        "attributes": root_attrs,
        "events": [],
        "status_code": root_status,
        "status_description": root_description,
    }
    by_normalized_id = {span["span_id"]: span for span in context.normalized}

    def topology_depth(span: dict[str, Any]) -> int:
        depth = 1
        parent = span.get("parent_span_id")
        seen: set[str] = set()
        while isinstance(parent, str) and parent in by_normalized_id:
            if parent in seen:
                break
            seen.add(parent)
            depth += 1
            parent = by_normalized_id[parent].get("parent_span_id")
        return depth

    all_spans = [
        root,
        *sorted(context.normalized, key=lambda span: (span["start_time"], topology_depth(span), span["span_id"])),
    ]
    meta = {
        "spec_version": SPEC_VERSION,
        "trace_id": context.trace_id,
        "run_name": context.run_name,
        "started_at": root["start_time"],
        "ended_at": root["end_time"],
        "duration_ms": root["duration_ms"],
        "status": "error" if root_status == "ERROR" else "ok" if root_status == "OK" else "running",
        "counts": {
            "llm_calls": llm_calls,
            "tool_calls": tool_calls,
            "errors": errors,
            "loop_warnings": len(loop_spans),
        },
    }
    return NormalizedClaudeRun(
        trace_id=context.trace_id,
        session_hash=context.capture.session_hash,
        segment=context.capture.segment,
        source_fingerprint=context.capture.source_fingerprint,
        run_name=context.run_name,
        meta=meta,
        spans=all_spans,
    )
