from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from maida.assertions import AssertionPolicy, RegressionReasonCode, run_assertions
from maida.baseline import create_baseline
from maida.config import load_config
from maida.constants import SPEC_VERSION
from maida.events import spans_to_events
from maida.integrations.claude_code import (
    ClaudeCaptureChangedError,
    ClaudeCaptureInputError,
    import_claude_capture,
    load_capture_segment,
    load_claude_capture,
    normalize_claude_capture,
)
from maida.storage import install_validated_run, load_validated_run


FIXTURES = Path(__file__).parents[1] / "fixtures" / "traces" / "claude-code" / "2.1.220"
SESSIONS = {
    "normal": "fixture-normal",
    "regression": "fixture-regression",
    "log-only": "fixture-log-only",
    "malformed": "fixture-malformed",
}


def _install_fixture(name: str, data_dir: Path) -> Path:
    session_id = SESSIONS[name]
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    destination = data_dir / "captures" / "claude-code" / session_hash / "0001"
    shutil.copytree(FIXTURES / name, destination)
    return destination


def _source_meta(span: dict) -> dict:
    raw = span.get("attributes", {}).get("maida.meta")
    return json.loads(raw)["claude_code"] if raw else {}


def test_normalize_prefers_trace_topology_and_enriches_without_duplicates(
    temp_data_dir,
):
    segment = load_capture_segment(FIXTURES / "normal")
    normalized = normalize_claude_capture(segment, load_config())
    repeated = normalize_claude_capture(segment, load_config())

    assert normalized.trace_id == repeated.trace_id
    assert normalized.spans == repeated.spans
    assert normalized.meta["spec_version"] == SPEC_VERSION
    assert normalized.meta["counts"] == {
        "llm_calls": 1,
        "tool_calls": 1,
        "errors": 0,
        "loop_warnings": 0,
    }

    events = spans_to_events(normalized.spans)
    assert [event["event_type"] for event in events].count("LLM_CALL") == 1
    assert [event["event_type"] for event in events].count("TOOL_CALL") == 1
    llm = next(event for event in events if event["event_type"] == "LLM_CALL")
    assert llm["payload"]["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "total_tokens": 16,
    }
    tool = next(event for event in events if event["event_type"] == "TOOL_CALL")
    assert tool["name"] == "Read"
    assert tool["payload"]["args"] == {"file_path": "/workspace/README.md"}

    interaction = next(
        span
        for span in normalized.spans
        if _source_meta(span).get("source_name") == "claude_code.interaction"
    )
    llm_span = next(
        span for span in normalized.spans if span["name"] == "claude-haiku-test"
    )
    tool_span = next(span for span in normalized.spans if span["name"] == "Read")
    assert llm_span["parent_span_id"] == interaction["span_id"]
    assert tool_span["parent_span_id"] == interaction["span_id"]
    assert _source_meta(tool_span)["source_span_id"] == "cccccccccccccccc"
    assert _source_meta(tool_span)["mapping_version"] == 1
    assert _source_meta(tool_span)["service_version"] == "2.1.220"
    assert (
        _source_meta(tool_span)["source_attributes"]["file_path"]
        == "/workspace/README.md"
    )

    unknown = next(
        span
        for span in normalized.spans
        if _source_meta(span).get("source_name") == "claude_code.future_signal"
    )
    assert unknown["attributes"].get("maida.tool_name") is None
    assert unknown["attributes"].get("gen_ai.operation.name") is None


def test_log_only_fallback_maps_failed_model_and_tool_calls(temp_data_dir):
    normalized = normalize_claude_capture(
        load_capture_segment(FIXTURES / "log-only"), load_config()
    )
    events = spans_to_events(normalized.spans)

    assert normalized.meta["counts"] == {
        "llm_calls": 1,
        "tool_calls": 1,
        "errors": 1,
        "loop_warnings": 0,
    }
    assert normalized.meta["status"] == "error"
    tool = next(event for event in events if event["event_type"] == "TOOL_CALL")
    assert tool["name"] == "Write"
    assert tool["payload"]["status"] == "error"
    assert tool["payload"]["args"]["file_path"] == "/workspace/out.txt"


def test_regression_fixture_detects_historical_loop(temp_data_dir):
    normalized = normalize_claude_capture(
        load_capture_segment(FIXTURES / "regression"), load_config()
    )
    events = spans_to_events(normalized.spans)

    assert normalized.meta["counts"]["tool_calls"] == 3
    assert normalized.meta["counts"]["loop_warnings"] == 1
    assert [event["event_type"] for event in events].count("LOOP_WARNING") == 1


def test_parent_cycle_is_broken_at_interaction_boundary(temp_data_dir):
    segment = load_capture_segment(FIXTURES / "normal")
    spans = deepcopy(segment.spans)
    spans[1]["span"]["parent_span_id"] = spans[2]["span"]["span_id"]
    spans[2]["span"]["parent_span_id"] = spans[1]["span"]["span_id"]
    cycled = replace(segment, spans=spans)

    normalized = normalize_claude_capture(cycled, load_config())
    repaired = [
        span
        for span in normalized.spans
        if _source_meta(span).get("parent_cycle_broken")
    ]
    assert len(repaired) == 2
    assert all(span["parent_span_id"] is not None for span in repaired)


def test_malformed_fixture_and_traversal_segment_are_rejected(temp_data_dir):
    with pytest.raises(ClaudeCaptureInputError, match="input_tokens"):
        load_capture_segment(FIXTURES / "malformed")
    _install_fixture("normal", temp_data_dir)
    with pytest.raises(ClaudeCaptureInputError, match="segment"):
        load_claude_capture("fixture-normal", load_config(), segment="../0001")


def test_import_is_atomic_idempotent_and_refuses_changed_source(temp_data_dir):
    capture_dir = _install_fixture("normal", temp_data_dir)
    config = load_config()

    first = import_claude_capture("fixture-normal", config)
    second = import_claude_capture("fixture-normal", config)
    assert first.imported is True
    assert second.imported is False
    assert first.trace_id == second.trace_id
    meta, spans = load_validated_run(first.trace_id, config)
    assert meta["spec_version"] == SPEC_VERSION
    assert spans

    with (capture_dir / "logs.jsonl").open("a", encoding="utf-8") as stream:
        changed = json.loads(
            (FIXTURES / "normal" / "logs.jsonl").read_text().splitlines()[-1]
        )
        changed["record"]["attributes"]["event.sequence"] = 99
        stream.write(json.dumps(changed) + "\n")
    manifest = json.loads((capture_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["signals"]["logs"] = 5
    (capture_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ClaudeCaptureChangedError, match="changed"):
        import_claude_capture("fixture-normal", config)


def test_capture_to_baseline_to_assertions_round_trip(temp_data_dir):
    config = load_config()
    good = normalize_claude_capture(load_capture_segment(FIXTURES / "normal"), config)
    regression = normalize_claude_capture(
        load_capture_segment(FIXTURES / "regression"), config
    )
    install_validated_run(good.meta, good.spans, config)
    install_validated_run(regression.meta, regression.spans, config)
    baseline = create_baseline(good.trace_id, config)

    report = run_assertions(
        regression.trace_id,
        AssertionPolicy(
            no_new_tools=True,
            no_loops=True,
            max_steps=4,
            max_tool_calls=2,
            max_cost_tokens=30,
        ),
        baseline,
        config,
    )
    assert report.passed is False
    assert RegressionReasonCode.NEW_TOOL_PATH in report.reason_codes
    assert RegressionReasonCode.LOOP_DETECTED in report.reason_codes
