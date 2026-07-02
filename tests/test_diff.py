"""Tests for maida.diff: structural run comparison."""

import json

import pytest

from maida import record_llm_call, record_tool_call, traced_run
from maida.baseline import create_baseline
from maida.config import load_config
from maida.diff import compute_diff, format_diff_text
from maida.events import EventType
from tests.conftest import get_latest_run_id


def _make_run(config, *, name="test_run", events=None):
    """Helper: create a run via traced_run + recorders, return run_id."""
    with traced_run(name=name):
        for ev_type, ev_name, payload in events or []:
            if ev_type == EventType.TOOL_CALL:
                record_tool_call(
                    ev_name, args=payload.get("args", {}), result=payload.get("result")
                )
            elif ev_type == EventType.LLM_CALL:
                record_llm_call(
                    ev_name, prompt="p", response="r", usage=payload.get("usage")
                )
    return get_latest_run_id(config)


def _write_trace_run(config, trace_id, run_name):
    run_dir = config.data_dir / "runs" / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "spec_version": "0.2",
        "trace_id": trace_id,
        "run_name": run_name,
        "started_at": "2026-01-01T00:00:00.000Z",
        "ended_at": "2026-01-01T00:00:01.000Z",
        "duration_ms": 1000,
        "status": "ok",
        "counts": {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    root_span = {
        "spec_version": "0.2",
        "trace_id": trace_id,
        "span_id": "0" * 16,
        "parent_span_id": None,
        "name": run_name,
        "kind": "INTERNAL",
        "start_time": "2026-01-01T00:00:00.000Z",
        "end_time": "2026-01-01T00:00:01.000Z",
        "duration_ms": 1000,
        "attributes": {"maida.run_name": run_name},
        "events": [],
        "status_code": "OK",
        "status_description": "",
    }
    (run_dir / "spans.jsonl").write_text(json.dumps(root_span) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tool diff
# ---------------------------------------------------------------------------


def test_diff_detects_added_tools(temp_data_dir):
    config = load_config()
    events_a = [
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "parse", {}),
        (EventType.TOOL_CALL, "salesforce", {}),
    ]
    events_b = [(EventType.TOOL_CALL, "search", {})]
    rid_a = _make_run(config, events=events_a, name="run_a")
    rid_b = _make_run(config, events=events_b, name="run_b")

    d = compute_diff(rid_a, run_b_id=rid_b, config=config)
    assert "parse" in d.new_tools
    assert "salesforce" in d.new_tools
    assert d.removed_tools == []


def test_diff_detects_removed_tools(temp_data_dir):
    config = load_config()
    events_a = [(EventType.TOOL_CALL, "search", {})]
    events_b = [
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "parse", {}),
    ]
    rid_a = _make_run(config, events=events_a, name="run_a")
    rid_b = _make_run(config, events=events_b, name="run_b")

    d = compute_diff(rid_a, run_b_id=rid_b, config=config)
    assert "parse" in d.removed_tools
    assert d.new_tools == []


# ---------------------------------------------------------------------------
# Event count diff
# ---------------------------------------------------------------------------


def test_diff_detects_changed_event_counts(temp_data_dir):
    config = load_config()
    events_a = [
        (EventType.TOOL_CALL, "t", {}),
        (EventType.TOOL_CALL, "t", {}),
        (EventType.LLM_CALL, "gpt-4", {}),
    ]
    events_b = [(EventType.TOOL_CALL, "t", {})]
    rid_a = _make_run(config, events=events_a, name="run_a")
    rid_b = _make_run(config, events=events_b, name="run_b")

    d = compute_diff(rid_a, run_b_id=rid_b, config=config)
    assert d.event_count_diff["TOOL_CALL"] == (2, 1)
    assert d.event_count_diff["LLM_CALL"] == (1, 0)


# ---------------------------------------------------------------------------
# Summary diff
# ---------------------------------------------------------------------------


def test_diff_detects_summary_changes(temp_data_dir):
    config = load_config()
    events_a = [(EventType.TOOL_CALL, "t", {}) for _ in range(5)]
    events_b = [(EventType.TOOL_CALL, "t", {})]
    rid_a = _make_run(config, events=events_a, name="run_a")
    rid_b = _make_run(config, events=events_b, name="run_b")

    d = compute_diff(rid_a, run_b_id=rid_b, config=config)
    assert "tool_calls" in d.summary_diff
    assert d.summary_diff["tool_calls"] == (5, 1)


# ---------------------------------------------------------------------------
# Identical runs
# ---------------------------------------------------------------------------


def test_diff_identical_runs_no_changes(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, "search", {})]
    rid_a = _make_run(config, events=events, name="run_a")
    rid_b = _make_run(config, events=events, name="run_b")

    d = compute_diff(rid_a, run_b_id=rid_b, config=config)
    assert d.new_tools == []
    assert d.removed_tools == []
    assert d.model_changes == {"added": [], "removed": []}
    for et, (ca, cb) in d.event_count_diff.items():
        assert ca == cb


# ---------------------------------------------------------------------------
# Diff against baseline dict
# ---------------------------------------------------------------------------


def test_diff_against_baseline(temp_data_dir):
    config = load_config()
    bl_events = [(EventType.TOOL_CALL, "search", {})]
    bl_rid = _make_run(config, events=bl_events, name="baseline")
    bl = create_baseline(bl_rid, config)

    run_events = [
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "new_api", {}),
    ]
    run_id = _make_run(config, events=run_events, name="current")

    d = compute_diff(run_id, baseline=bl, config=config)
    assert "new_api" in d.new_tools
    assert d.run_b_id == bl_rid


def test_old_baseline_tool_path_order_is_not_treated_as_exact_sequence(temp_data_dir):
    config = load_config()
    run_id = _make_run(
        config,
        name="current",
        events=[
            (EventType.TOOL_CALL, "search", {}),
            (EventType.TOOL_CALL, "parse", {}),
        ],
    )
    baseline = {
        "source_run_id": "old-baseline",
        "summary": {},
        "tool_path": ["parse", "search"],
        "tool_call_counts": {"parse": 1, "search": 1},
        "llm_models_used": [],
        "event_type_sequence": [],
        "guardrail_events": [],
        "final_status": "ok",
    }

    d = compute_diff(run_id, baseline=baseline, config=config)
    text = format_diff_text(d)

    assert d.reordered_tools is False
    assert d.new_tools == []
    assert d.removed_tools == []
    assert "Tool path:" not in text
    assert "Tool call changes:" not in text


def test_old_baseline_without_call_counts_does_not_fake_repeated_tool(temp_data_dir):
    config = load_config()
    run_id = _make_run(
        config,
        name="current",
        events=[(EventType.TOOL_CALL, "bash", {}) for _ in range(4)],
    )
    baseline = {
        "source_run_id": "old-baseline",
        "summary": {},
        "tool_path": ["bash"],
        "llm_models_used": [],
        "event_type_sequence": [],
        "guardrail_events": [],
        "final_status": "ok",
    }

    d = compute_diff(run_id, baseline=baseline, config=config)
    text = format_diff_text(d)

    assert d.repeated_tools == {}
    assert "~ bash repeated" not in text


def test_baseline_with_null_tool_path_is_treated_as_empty(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, name="current", events=[])
    baseline = {
        "source_run_id": "external-baseline",
        "summary": {},
        "tool_path": None,
        "tool_call_counts": {},
        "llm_models_used": [],
        "event_type_sequence": [],
        "guardrail_events": [],
        "final_status": "ok",
    }

    d = compute_diff(run_id, baseline=baseline, config=config)

    assert d.baseline_tool_sequence == []
    assert d.removed_tools == []


def test_baseline_status_is_canonicalized_for_diff(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, name="current-ok", events=[])
    baseline = {
        "source_run_id": "inconsistent-baseline",
        "summary": {"status": "ok"},
        "tool_path": [],
        "tool_call_counts": {},
        "llm_models_used": [],
        "event_type_sequence": [],
        "guardrail_events": [],
        "final_status": "error",
    }

    d = compute_diff(run_id, baseline=baseline, config=config)

    assert d.summary_diff["status"] == ("ok", "error")
    assert d.terminal_status_diff == ("ok", "error")


def test_diff_tracks_guardrail_and_terminal_status_changes(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, name="current-ok", events=[])
    baseline = {
        "source_run_id": "old-error-baseline",
        "summary": {"status": "error"},
        "tool_path": [],
        "tool_call_counts": {},
        "llm_models_used": [],
        "event_type_sequence": [],
        "guardrail_events": [{"event_type": "ERROR"}],
        "final_status": "error",
    }

    d = compute_diff(run_id, baseline=baseline, config=config)

    assert d.guardrail_event_diff == (0, 1)
    assert d.terminal_status_diff == ("ok", "error")


def test_diff_tool_path_reports_step_delta_and_tool_changes(temp_data_dir):
    config = load_config()
    bl_rid = _make_run(
        config,
        name="baseline",
        events=[
            (EventType.TOOL_CALL, "search", {}),
            (EventType.TOOL_CALL, "parse", {}),
        ],
    )
    baseline = create_baseline(bl_rid, config)

    run_id = _make_run(
        config,
        name="current",
        events=[
            (EventType.TOOL_CALL, "search", {}),
            (EventType.TOOL_CALL, "parse", {}),
            (EventType.TOOL_CALL, "enrich", {}),
            (EventType.TOOL_CALL, "enrich", {}),
        ],
    )

    d = compute_diff(run_id, baseline=baseline, config=config)
    text = format_diff_text(d)

    assert "step_count:" in text
    assert "baseline: search -> parse" in text
    assert "current: search -> parse -> enrich -> enrich" in text
    assert "+ enrich (new)" in text
    assert "~ enrich repeated: 0 -> 2 calls" in text


def test_diff_tool_path_identifies_removed_and_reordered_calls(temp_data_dir):
    config = load_config()
    bl_rid = _make_run(
        config,
        name="baseline",
        events=[
            (EventType.TOOL_CALL, "search", {}),
            (EventType.TOOL_CALL, "parse", {}),
            (EventType.TOOL_CALL, "summarize", {}),
        ],
    )
    baseline = create_baseline(bl_rid, config)

    run_id = _make_run(
        config,
        name="current",
        events=[
            (EventType.TOOL_CALL, "parse", {}),
            (EventType.TOOL_CALL, "search", {}),
        ],
    )

    d = compute_diff(run_id, baseline=baseline, config=config)
    text = format_diff_text(d)

    assert "- summarize (removed)" in text
    assert "order changed for shared tool calls" in text


def test_diff_tool_path_truncates_long_sequences(temp_data_dir):
    from maida.diff import format_diff_markdown

    config = load_config()
    bl_rid = _make_run(
        config,
        name="baseline",
        events=[(EventType.TOOL_CALL, f"tool_{i}", {}) for i in range(16)],
    )
    baseline = create_baseline(bl_rid, config)

    run_id = _make_run(
        config,
        name="current",
        events=[(EventType.TOOL_CALL, f"tool_{i}", {}) for i in range(20)],
    )

    d = compute_diff(run_id, baseline=baseline, config=config)
    md = format_diff_markdown(d)

    assert "... (8 more) ..." in md
    assert "tool_19" in md
    assert "tool_8 -> tool_9 -> tool_10" not in md


def test_diff_returns_resolved_left_trace_id(temp_data_dir):
    config = load_config()
    run_a = "a0eebc99" + "a" * 24
    run_b = "b0eebc99" + "b" * 24
    _write_trace_run(config, run_a, "run_a")
    _write_trace_run(config, run_b, "run_b")

    d = compute_diff(run_a[:8], run_b_id=run_b[:8], config=config)

    assert d.run_a_id == run_a
    assert d.run_b_id == run_b


# ---------------------------------------------------------------------------
# Model changes
# ---------------------------------------------------------------------------


def test_diff_model_changes(temp_data_dir):
    config = load_config()
    events_a = [(EventType.LLM_CALL, "gpt-4", {})]
    events_b = [(EventType.LLM_CALL, "gpt-3.5-turbo", {})]
    rid_a = _make_run(config, events=events_a, name="run_a")
    rid_b = _make_run(config, events=events_b, name="run_b")

    d = compute_diff(rid_a, run_b_id=rid_b, config=config)
    assert "gpt-4" in d.model_changes["added"]
    assert "gpt-3.5-turbo" in d.model_changes["removed"]


# ---------------------------------------------------------------------------
# Formatter
# ---------------------------------------------------------------------------


def test_format_diff_text_output(temp_data_dir):
    config = load_config()
    events_a = [(EventType.TOOL_CALL, "search", {})]
    events_b = [(EventType.TOOL_CALL, "parse", {})]
    rid_a = _make_run(config, events=events_a, name="run_a")
    rid_b = _make_run(config, events=events_b, name="run_b")

    d = compute_diff(rid_a, run_b_id=rid_b, config=config)
    text = format_diff_text(d)
    assert "Run comparison:" in text
    assert rid_a[:8] in text


# ---------------------------------------------------------------------------
# Error: neither run_b nor baseline
# ---------------------------------------------------------------------------


def test_compute_diff_raises_without_target(temp_data_dir):
    config = load_config()
    rid = _make_run(config, events=[])
    with pytest.raises(ValueError, match="Either run_b_id or baseline"):
        compute_diff(rid, config=config)


# ---------------------------------------------------------------------------
# format_diff_markdown
# ---------------------------------------------------------------------------


def test_format_diff_markdown_includes_changes(temp_data_dir):
    from maida.diff import format_diff_markdown

    config = load_config()
    bl_run = _make_run(
        config,
        name="bl",
        events=[
            (EventType.TOOL_CALL, "search", {}),
            (EventType.LLM_CALL, "model-a", {"usage": {"total_tokens": 10}}),
        ],
    )
    baseline = create_baseline(bl_run, config)

    run_id = _make_run(
        config,
        name="current",
        events=[
            (EventType.TOOL_CALL, "search", {}),
            (EventType.TOOL_CALL, "escalate", {}),
            (EventType.LLM_CALL, "model-b", {"usage": {"total_tokens": 50}}),
        ],
    )
    diff = compute_diff(run_id, baseline=baseline, config=config)
    md = format_diff_markdown(diff)
    assert "### Top behavior changes" in md
    assert "| Behavior | Baseline | Current | Change |" in md
    assert "➕ `escalate`" in md
    assert "**Model changes:**" in md
    assert "➕ `model-b`" in md
    assert "➖ `model-a`" in md


def test_format_diff_markdown_empty_when_identical(temp_data_dir):
    from maida.diff import format_diff_markdown

    from maida.diff import RunDiff

    diff = RunDiff(run_a_id="a" * 32, run_b_id="b" * 32)
    assert format_diff_markdown(diff) == ""
