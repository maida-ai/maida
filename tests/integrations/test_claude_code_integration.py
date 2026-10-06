from __future__ import annotations

from tests.support.claude_code import _install_fixture as _install_fixture, _source_meta as _source_meta
from tests.support.paths import FIXTURES_ROOT

import json
from copy import deepcopy
from dataclasses import replace

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
from maida.loopdetect import compute_signature


FIXTURES = FIXTURES_ROOT / "traces" / "claude-code" / "2.1.220"
SESSIONS = {
    "normal": "fixture-normal",
    "regression": "fixture-regression",
    "log-only": "fixture-log-only",
    "malformed": "fixture-malformed",
}


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
        span for span in normalized.spans if _source_meta(span).get("source_name") == "claude_code.interaction"
    )
    llm_span = next(span for span in normalized.spans if span["name"] == "claude-haiku-test")
    tool_span = next(span for span in normalized.spans if span["name"] == "Read")
    assert llm_span["parent_span_id"] == interaction["span_id"]
    assert tool_span["parent_span_id"] == interaction["span_id"]
    assert _source_meta(tool_span)["source_span_id"] == "cccccccccccccccc"
    assert _source_meta(tool_span)["mapping_version"] == 2
    assert _source_meta(tool_span)["service_version"] == "2.1.220"
    assert _source_meta(tool_span)["source_attributes"]["file_path"] == "/workspace/README.md"

    unknown = next(
        span for span in normalized.spans if _source_meta(span).get("source_name") == "claude_code.future_signal"
    )
    assert unknown["attributes"].get("maida.tool_name") is None
    assert unknown["attributes"].get("gen_ai.operation.name") is None


def test_log_only_fallback_maps_failed_model_and_tool_calls(temp_data_dir):
    normalized = normalize_claude_capture(load_capture_segment(FIXTURES / "log-only"), load_config())
    events = spans_to_events(normalized.spans)

    assert normalized.meta["counts"] == {
        "llm_calls": 1,
        "tool_calls": 1,
        "errors": 1,
        "loop_warnings": 0,
    }
    assert normalized.meta["status"] == "running"
    assert normalized.spans[0]["status_code"] == "UNSET"
    assert next(event for event in events if event["event_type"] == "RUN_END")["payload"]["status"] == "unknown"
    tool = next(event for event in events if event["event_type"] == "TOOL_CALL")
    assert tool["name"] == "Write"
    assert tool["payload"]["status"] == "error"
    assert tool["payload"]["args"]["file_path"] == "/workspace/out.txt"


@pytest.mark.parametrize("terminal_status", ["OK", "UNSET", "ERROR"])
def test_terminal_interaction_outcome_is_independent_of_child_errors(terminal_status, temp_data_dir):
    segment = load_capture_segment(FIXTURES / "normal")
    spans = deepcopy(segment.spans)
    spans[0]["span"]["status_code"] = terminal_status
    spans[0]["span"]["status_description"] = "session aborted" if terminal_status == "ERROR" else ""
    spans[2]["span"]["status_code"] = "ERROR"
    normalized = normalize_claude_capture(replace(segment, spans=spans), load_config())

    expected = "error" if terminal_status == "ERROR" else "ok"
    assert normalized.meta["status"] == expected
    assert normalized.meta["counts"]["errors"] == (2 if terminal_status == "ERROR" else 1)
    assert normalized.spans[0]["status_code"] == ("ERROR" if terminal_status == "ERROR" else "OK")
    events = spans_to_events(normalized.spans)
    assert next(event for event in events if event["event_type"] == "RUN_END")["payload"]["status"] == expected
    assert next(event for event in events if event["event_type"] == "TOOL_CALL")["payload"]["status"] == "error"


def test_successful_later_interaction_recovers_failed_interaction(temp_data_dir):
    segment = load_capture_segment(FIXTURES / "normal")
    spans = deepcopy(segment.spans)
    spans[0]["span"]["status_code"] = "ERROR"
    later = deepcopy(spans[0])
    later["span"].update(
        span_id="dddddddddddddddd",
        start_time="2026-08-08T12:00:04Z",
        end_time="2026-08-08T12:00:05Z",
        status_code="OK",
    )
    # Input order must not decide the terminal outcome.
    normalized = normalize_claude_capture(replace(segment, spans=[later, *spans]), load_config())
    assert normalized.meta["status"] == "ok"
    assert normalized.meta["counts"]["errors"] == 1


def test_failed_nested_interaction_does_not_fail_session(temp_data_dir):
    segment = load_capture_segment(FIXTURES / "normal")
    nested = deepcopy(segment.spans[0])
    nested["span"].update(
        span_id="dddddddddddddddd",
        parent_span_id=segment.spans[0]["span"]["span_id"],
        status_code="ERROR",
    )
    normalized = normalize_claude_capture(replace(segment, spans=[*segment.spans, nested]), load_config())
    assert normalized.meta["status"] == "ok"
    assert normalized.meta["counts"]["errors"] == 1


def test_session_end_does_not_mask_aborted_top_level_interaction(temp_data_dir):
    segment = load_capture_segment(FIXTURES / "normal")
    spans = deepcopy(segment.spans)
    spans[0]["span"].update(status_code="ERROR", status_description="session aborted")
    session_end = deepcopy(segment.logs[-1])
    session_end["record"].update(event_name="claude_code.hook.session_end")
    session_end["record"]["attributes"].update(reason="other", **{"event.timestamp": "2026-08-08T12:00:10Z"})
    normalized = normalize_claude_capture(
        replace(segment, spans=spans, logs=[*segment.logs, session_end]), load_config()
    )
    assert normalized.meta["status"] == "error"
    assert normalized.spans[0]["status_description"] == "session aborted"
    assert normalized.meta["counts"]["errors"] == 1


def test_log_only_api_failure_recovers_at_session_end(temp_data_dir):
    segment = load_capture_segment(FIXTURES / "log-only")
    api_error = deepcopy(segment.logs[1])
    api_error["record"].update(event_name="claude_code.api_error")
    api_error["record"]["attributes"].update(attempt=1, error="timeout")
    session_end = deepcopy(segment.logs[-1])
    session_end["record"].update(event_name="claude_code.hook.session_end")
    session_end["record"]["attributes"].update(reason="other", **{"event.timestamp": "2026-08-08T12:10:10Z"})
    normalized = normalize_claude_capture(replace(segment, logs=[api_error, *segment.logs, session_end]), load_config())
    assert normalized.meta["status"] == "ok"
    assert normalized.meta["counts"]["errors"] == 2
    events = spans_to_events(normalized.spans)
    assert any(event["event_type"] == "LLM_CALL" and event["payload"]["status"] == "error" for event in events)
    assert any(event["event_type"] == "TOOL_CALL" and event["payload"]["status"] == "error" for event in events)


def test_import_refuses_to_silently_reuse_previous_mapping(temp_data_dir):
    _install_fixture("normal", temp_data_dir)
    config = load_config()
    normalized = normalize_claude_capture(load_capture_segment(FIXTURES / "normal"), config)
    root = normalized.spans[0]
    source = json.loads(root["attributes"]["maida.meta"])
    source["claude_code"]["mapping_version"] = 1
    root["attributes"]["maida.meta"] = json.dumps(source)
    install_validated_run(normalized.meta, normalized.spans, config)

    with pytest.raises(ClaudeCaptureChangedError, match="refusing to overwrite"):
        import_claude_capture("fixture-normal", config)


def test_regression_fixture_detects_historical_loop(temp_data_dir):
    normalized = normalize_claude_capture(load_capture_segment(FIXTURES / "regression"), load_config())
    events = spans_to_events(normalized.spans)

    assert normalized.meta["counts"]["tool_calls"] == 3
    assert normalized.meta["counts"]["loop_warnings"] == 1
    assert [event["event_type"] for event in events].count("LOOP_WARNING") == 1


@pytest.mark.parametrize(
    "commands,length",
    [
        (["pytest", "git status", "cat pyproject.toml"], 0),
        (["pytest"] * 3, 1),
        (["pytest", "git status"] * 3, 2),
    ],
)
def test_captured_bash_loop_equality_uses_sanitized_args(temp_data_dir, commands, length):
    segment = load_capture_segment(FIXTURES / "regression")
    spans = deepcopy(segment.spans[:2])
    for i, command in enumerate(commands):
        record = deepcopy(segment.spans[2])
        span = record["span"]
        span["span_id"] = f"{i + 1:016x}"
        span["start_time"] = f"2026-08-08T12:10:0{i // 2 + 1}.{200 + 500 * (i % 2):03d}000Z"
        span["end_time"] = f"2026-08-08T12:10:0{i // 2 + 1}.{400 + 500 * (i % 2):03d}000Z"
        span["attributes"].update(
            tool_use_id=f"tool-{i}",
            full_command=command,
            tool_input={"command": command, "private_value": f"secret-{i}"},
        )
        spans.append(record)
    config = replace(load_config(), redact_keys=["private_value"])
    normalized = normalize_claude_capture(replace(segment, spans=spans), config)
    events = spans_to_events(normalized.spans)
    warnings = [e for e in events if e["event_type"] == "LOOP_WARNING"]
    assert normalized.meta["counts"]["loop_warnings"] == (1 if length else 0)
    assert len(warnings) == (1 if length else 0)
    if length:
        payload = warnings[0]["payload"]
        tools = [e for e in events if e["event_type"] == "TOOL_CALL"]
        assert payload["pattern_length"] == length
        assert payload["pattern"] == " -> ".join(compute_signature(e) for e in tools[:length])
        assert all(command not in json.dumps(payload) for command in commands)
    assert all(f"secret-{i}" not in json.dumps(normalized.spans) for i in range(len(commands)))


def test_parent_cycle_is_broken_at_interaction_boundary(temp_data_dir):
    segment = load_capture_segment(FIXTURES / "normal")
    spans = deepcopy(segment.spans)
    spans[1]["span"]["parent_span_id"] = spans[2]["span"]["span_id"]
    spans[2]["span"]["parent_span_id"] = spans[1]["span"]["span_id"]
    cycled = replace(segment, spans=spans)

    normalized = normalize_claude_capture(cycled, load_config())
    repaired = [span for span in normalized.spans if _source_meta(span).get("parent_cycle_broken")]
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
        changed = json.loads((FIXTURES / "normal" / "logs.jsonl").read_text().splitlines()[-1])
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
    regression = normalize_claude_capture(load_capture_segment(FIXTURES / "regression"), config)
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
