"""Offline langfuse normalize tests."""

from __future__ import annotations
from tests.support.paths import FIXTURES_ROOT
import json
import pytest
from typer.testing import CliRunner
from maida.config import load_config
from maida.events import EventType, spans_to_events
from maida.integrations.langfuse import (
    IncompleteLangfuseTrace,
    LangfuseImportError,
    normalize_langfuse_trace,
)
from tests.support.langfuse import _observation


runner = CliRunner()

FIXTURE_PATH = FIXTURES_ROOT / "langfuse" / "api-v2" / "observations.json"


def test_normalize_preserves_tree_maps_calls_and_redacts(temp_data_dir):
    config = load_config()
    rows = [
        _observation(
            "agent-1",
            observation_type="AGENT",
            name="worker",
            start_second=0,
            end_second=5,
            metadata={
                "password": "must-not-reach-disk",
                "oversized": "x" * (config.max_field_bytes + 1),
            },
        ),
        _observation(
            "generation-1",
            observation_type="GENERATION",
            name="answer",
            parent_id="agent-1",
            start_second=1,
            end_second=2,
            input_value=json.dumps({"api_key": "also-secret", "prompt": "hello"}),
            output_value="hi",
            usage={
                "input": 10,
                "output": 5,
                "total": 15,
                "cache_read_input_tokens": 4,
            },
        ),
        _observation(
            "tool-1",
            parent_id="agent-1",
            start_second=3,
            end_second=4,
            input_value={"query": "docs"},
            output_value={"count": 1},
        ),
    ]

    normalized = normalize_langfuse_trace(rows, config)
    serialized = json.dumps({"meta": normalized.meta, "spans": normalized.spans})

    assert "must-not-reach-disk" not in serialized
    assert "also-secret" not in serialized
    assert "__REDACTED__" in serialized
    assert "__TRUNCATED__" in serialized
    assert len(normalized.trace_id) == 32
    assert normalized.meta["run_name"] == "nightly-support-job"
    assert normalized.meta["counts"] == {
        "llm_calls": 1,
        "tool_calls": 1,
        "errors": 0,
        "loop_warnings": 0,
    }

    events = spans_to_events(normalized.spans)
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        "UNKNOWN",
        EventType.LLM_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.RUN_END.value,
    ]
    llm = next(event for event in events if event["event_type"] == "LLM_CALL")
    assert llm["payload"]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    tool = next(event for event in events if event["event_type"] == "TOOL_CALL")
    assert tool["payload"]["args"] == {"query": "docs"}
    assert tool["payload"]["result"] == {"count": 1}

    spans_by_source_id = {}
    for span in normalized.spans:
        raw_meta = span["attributes"].get("maida.meta")
        if raw_meta:
            source_id = json.loads(raw_meta).get("langfuse", {}).get("observation_id")
            if source_id:
                spans_by_source_id[source_id] = span
    assert spans_by_source_id["generation-1"]["parent_span_id"] == spans_by_source_id["agent-1"]["span_id"]


def test_normalize_maps_legacy_camel_case_usage_fields(temp_data_dir):
    row = _observation("generation-1", observation_type="GENERATION", usage={})
    row.update({"promptTokens": 8, "completionTokens": 3, "totalTokens": 11})

    normalized = normalize_langfuse_trace([row], load_config())
    llm = next(event for event in spans_to_events(normalized.spans) if event["event_type"] == "LLM_CALL")

    assert llm["payload"]["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 3,
        "total_tokens": 11,
    }


def test_normalize_attaches_missing_parent_to_synthetic_root(temp_data_dir):
    normalized = normalize_langfuse_trace([_observation("tool-1", parent_id="not-in-export")], load_config())
    root = next(span for span in normalized.spans if span["parent_span_id"] is None)
    tool = next(span for span in normalized.spans if span["name"] == "lookup")
    assert tool["parent_span_id"] == root["span_id"]


def test_normalize_preserves_physical_parent_for_logical_subagent_root(
    temp_data_dir,
):
    parent = _observation("agent-1", observation_type="AGENT", name="supervisor", end_second=4)
    child = _observation(
        "agent-2",
        observation_type="AGENT",
        name="runtime-worker",
        parent_id="agent-1",
        start_second=1,
        end_second=3,
    )
    child["isRootObservation"] = True

    normalized = normalize_langfuse_trace([parent, child], load_config())
    by_name = {span["name"]: span for span in normalized.spans}

    assert by_name["runtime-worker"]["parent_span_id"] == by_name["supervisor"]["span_id"]


def test_normalize_rejects_parent_cycles(temp_data_dir):
    rows = [
        _observation("tool-1", parent_id="tool-2"),
        _observation("tool-2", parent_id="tool-1", start_second=2, end_second=3),
    ]

    with pytest.raises(LangfuseImportError, match="parent cycle"):
        normalize_langfuse_trace(rows, load_config())


def test_normalize_detects_historical_tool_loop(temp_data_dir):
    rows = [
        _observation(
            f"tool-{index}",
            name="repeat",
            start_second=index,
            end_second=index + 1,
            input_value={"query": "same"},
        )
        for index in range(3)
    ]

    normalized = normalize_langfuse_trace(rows, load_config())
    events = spans_to_events(normalized.spans)

    assert normalized.meta["counts"]["loop_warnings"] == 1
    assert [event["event_type"] for event in events].count("LOOP_WARNING") == 1


def test_normalize_preserves_completed_source_error(temp_data_dir):
    normalized = normalize_langfuse_trace([_observation("tool-1", level="ERROR")], load_config())
    tool = next(event for event in spans_to_events(normalized.spans) if event["event_type"] == "TOOL_CALL")

    assert normalized.meta["status"] == "error"
    assert normalized.meta["counts"]["errors"] == 1
    assert tool["payload"]["status"] == "error"
    assert tool["payload"]["error"]["message"] == "source failure"


def test_normalize_rejects_incomplete_non_event_trace(temp_data_dir):
    with pytest.raises(IncompleteLangfuseTrace, match="incomplete observation"):
        normalize_langfuse_trace([_observation("tool-1", end_second=None)], load_config())


def test_normalize_allows_instantaneous_event_without_end_time(temp_data_dir):
    normalized = normalize_langfuse_trace(
        [
            _observation(
                "event-1",
                observation_type="EVENT",
                name="checkpoint",
                end_second=None,
            )
        ],
        load_config(),
    )
    event_span = next(span for span in normalized.spans if span["name"] == "checkpoint")
    assert event_span["end_time"] == event_span["start_time"]
    assert event_span["duration_ms"] == 0


def test_normalize_preserves_and_reports_future_observation_type(temp_data_dir):
    normalized = normalize_langfuse_trace(
        [
            _observation(
                "future-1",
                observation_type="FUTURE_CONTAINER",
                name="future-container",
            )
        ],
        load_config(),
    )

    assert normalized.unmapped_observation_types == ("FUTURE_CONTAINER",)
    assert any(span["name"] == "future-container" for span in normalized.spans)
