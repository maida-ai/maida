"""Tests for maida.baseline: create, save, load, and metric extraction."""

import pytest

from maida import record_llm_call, record_tool_call, traced_run
from maida.baseline import (
    _BASELINE_SCHEMA_VERSION,
    create_baseline,
    load_baseline,
    save_baseline,
)
from maida.config import load_config
from maida.events import EventType
from tests.conftest import get_latest_run_id


def _make_run(config, *, name="test_run", events=None, status="ok"):
    """Helper: create a run via traced_run + recorders, return run_id."""
    import pytest as _pt

    if status == "error":
        with _pt.raises(RuntimeError):
            with traced_run(name=name):
                for ev_type, ev_name, payload in events or []:
                    if ev_type == EventType.TOOL_CALL:
                        record_tool_call(
                            ev_name,
                            args=payload.get("args", {}),
                            result=payload.get("result"),
                        )
                    elif ev_type == EventType.LLM_CALL:
                        record_llm_call(
                            ev_name,
                            prompt="p",
                            response="r",
                            usage=payload.get("usage"),
                        )
                    elif ev_type == EventType.ERROR:
                        record_tool_call(
                            ev_name,
                            args={},
                            result=None,
                            status="error",
                            error=ValueError(payload.get("message", "err")),
                        )
                    elif ev_type == EventType.LOOP_WARNING:
                        record_tool_call(ev_name, args={}, result=None)
                raise RuntimeError("simulated error")
    else:
        with traced_run(name=name):
            for ev_type, ev_name, payload in events or []:
                if ev_type == EventType.TOOL_CALL:
                    record_tool_call(
                        ev_name,
                        args=payload.get("args", {}),
                        result=payload.get("result"),
                    )
                elif ev_type == EventType.LLM_CALL:
                    record_llm_call(
                        ev_name, prompt="p", response="r", usage=payload.get("usage")
                    )
                elif ev_type == EventType.ERROR:
                    record_tool_call(
                        ev_name,
                        args={},
                        result=None,
                        status="error",
                        error=ValueError(payload.get("message", "err")),
                    )
                elif ev_type == EventType.LOOP_WARNING:
                    record_tool_call(ev_name, args={}, result=None)
    return get_latest_run_id(config)


# ---------------------------------------------------------------------------
# Schema correctness
# ---------------------------------------------------------------------------


def test_create_baseline_schema_keys(temp_data_dir):
    config = load_config()
    run_id = _make_run(config)
    bl = create_baseline(run_id, config)

    required = {
        "schema_version",
        "created_at",
        "source_run_id",
        "source_run_name",
        "summary",
        "tool_path",
        "tool_call_counts",
        "llm_models_used",
        "event_type_sequence",
        "guardrail_events",
        "final_status",
    }
    assert required.issubset(bl.keys())
    assert bl["schema_version"] == _BASELINE_SCHEMA_VERSION
    assert bl["source_run_id"] == run_id
    assert bl["source_run_name"] == "test_run"


def test_create_baseline_summary_fields(temp_data_dir):
    config = load_config()
    events = [
        (
            EventType.LLM_CALL,
            "gpt-4",
            {
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 50,
                    "total_tokens": 100,
                }
            },
        ),
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "parse", {}),
    ]
    run_id = _make_run(config, events=events)
    bl = create_baseline(run_id, config)
    s = bl["summary"]

    assert s["llm_calls"] == 1
    assert s["tool_calls"] == 2
    assert s["total_events"] == 3
    assert s["total_tokens"] == 100
    assert s["errors"] == 0
    assert s["loop_warnings"] == 0
    assert s["status"] == "ok"


# ---------------------------------------------------------------------------
# Roundtrip persistence
# ---------------------------------------------------------------------------


def test_save_load_roundtrip(temp_data_dir):
    config = load_config()
    run_id = _make_run(config)
    bl = create_baseline(run_id, config)

    path = temp_data_dir / "bl.json"
    save_baseline(bl, path)
    loaded = load_baseline(path)

    assert loaded == bl


def test_load_baseline_file_not_found(temp_data_dir):
    with pytest.raises(FileNotFoundError):
        load_baseline(temp_data_dir / "nonexistent.json")


def test_save_baseline_creates_parent_dirs(temp_data_dir):
    config = load_config()
    run_id = _make_run(config)
    bl = create_baseline(run_id, config)

    nested = temp_data_dir / "a" / "b" / "c" / "bl.json"
    save_baseline(bl, nested)
    assert nested.is_file()
    loaded = load_baseline(nested)
    assert loaded["source_run_id"] == run_id


# ---------------------------------------------------------------------------
# Tool path, counts, event sequence
# ---------------------------------------------------------------------------


def test_tool_path_ordered_unique(temp_data_dir):
    config = load_config()
    events = [
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "parse", {}),
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "format", {}),
    ]
    run_id = _make_run(config, events=events)
    bl = create_baseline(run_id, config)

    assert bl["tool_path"] == sorted(["search", "parse", "format"])
    assert bl["tool_call_counts"] == {"search": 2, "parse": 1, "format": 1}


def test_event_type_sequence(temp_data_dir):
    config = load_config()
    events = [
        (EventType.LLM_CALL, "gpt-4", {}),
        (EventType.TOOL_CALL, "search", {}),
        (EventType.LLM_CALL, "gpt-4", {}),
    ]
    run_id = _make_run(config, events=events)
    bl = create_baseline(run_id, config)

    assert bl["event_type_sequence"] == [
        "RUN_START",
        "LLM_CALL",
        "TOOL_CALL",
        "LLM_CALL",
        "RUN_END",
    ]


def test_llm_models_used_unique(temp_data_dir):
    config = load_config()
    events = [
        (EventType.LLM_CALL, "gpt-4", {}),
        (EventType.LLM_CALL, "gpt-3.5-turbo", {}),
        (EventType.LLM_CALL, "gpt-4", {}),
    ]
    run_id = _make_run(config, events=events)
    bl = create_baseline(run_id, config)

    assert bl["llm_models_used"] == sorted(["gpt-4", "gpt-3.5-turbo"])


# ---------------------------------------------------------------------------
# Token extraction
# ---------------------------------------------------------------------------


def test_total_tokens_summed_from_llm_calls(temp_data_dir):
    config = load_config()
    events = [
        (
            EventType.LLM_CALL,
            "gpt-4",
            {
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 100,
                    "total_tokens": 200,
                }
            },
        ),
        (
            EventType.LLM_CALL,
            "gpt-4",
            {
                "usage": {
                    "prompt_tokens": 75,
                    "completion_tokens": 75,
                    "total_tokens": 150,
                }
            },
        ),
    ]
    run_id = _make_run(config, events=events)
    bl = create_baseline(run_id, config)

    assert bl["summary"]["total_tokens"] == 350


def test_total_tokens_preserved_when_only_total_usage_is_recorded(temp_data_dir):
    config = load_config()
    events = [
        (
            EventType.LLM_CALL,
            "gpt-test",
            {"usage": {"total_tokens": 7}},
        ),
    ]
    run_id = _make_run(config, events=events)
    bl = create_baseline(run_id, config)

    assert bl["summary"]["total_tokens"] == 7


def test_total_tokens_zero_when_no_usage(temp_data_dir):
    config = load_config()
    events = [
        (EventType.LLM_CALL, "gpt-4", {}),
        (EventType.LLM_CALL, "gpt-4", {"usage": None}),
    ]
    run_id = _make_run(config, events=events)
    bl = create_baseline(run_id, config)

    assert bl["summary"]["total_tokens"] == 0


# ---------------------------------------------------------------------------
# Edge cases: empty runs
# ---------------------------------------------------------------------------


def test_empty_run_no_events(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    bl = create_baseline(run_id, config)

    assert bl["tool_path"] == []
    assert bl["tool_call_counts"] == {}
    assert bl["llm_models_used"] == []
    assert bl["event_type_sequence"] == ["RUN_START", "RUN_END"]
    assert bl["summary"]["total_events"] == 0
    assert bl["summary"]["total_tokens"] == 0


def test_run_with_only_tool_calls(temp_data_dir):
    config = load_config()
    events = [
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "search", {}),
    ]
    run_id = _make_run(config, events=events)
    bl = create_baseline(run_id, config)

    assert bl["summary"]["llm_calls"] == 0
    assert bl["summary"]["tool_calls"] == 2
    assert bl["llm_models_used"] == []
    assert bl["summary"]["total_tokens"] == 0


# ---------------------------------------------------------------------------
# Guardrail events
# ---------------------------------------------------------------------------


def test_guardrail_events_captured(temp_data_dir):
    config = load_config()
    events = [
        (EventType.ERROR, "guardrail_error", {"message": "something broke"}),
    ]
    run_id = _make_run(config, events=events, status="error")
    bl = create_baseline(run_id, config)

    assert bl["guardrail_events"] == []
