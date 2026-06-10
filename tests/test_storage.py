"""
Storage tests: list_runs, load_run_meta, load_spans, resolve_trace_id, rename_run, delete_run.
Uses temp dir via MAIDA_DATA_DIR; env restored by fixture.
"""

import json

import pytest

from maida.config import load_config
from maida.events import EventType, spans_to_events
from maida.storage import (
    _validate_trace_id,
    delete_run,
    list_runs,
    load_run_meta,
    load_spans,
    rename_run,
    resolve_trace_id,
)


# ---------------------------------------------------------------------------
# _validate_trace_id
# ---------------------------------------------------------------------------


def test_validate_trace_id_accepts_32_hex_chars(temp_data_dir):
    tid = "a" * 32
    assert _validate_trace_id(tid) == tid


def test_validate_trace_id_normalizes_uppercase(temp_data_dir):
    """Uppercase trace IDs normalize to lowercase for filesystem lookup."""
    assert _validate_trace_id("A" * 32) == "a" * 32


def test_validate_trace_id_rejects_short(temp_data_dir):
    with pytest.raises(ValueError, match="invalid trace_id"):
        _validate_trace_id("abc")


def test_validate_trace_id_rejects_path_traversal(temp_data_dir):
    for bad in ["../" + "a" * 29, "a" * 16 + "/b" * 15, "a" * 16 + "\\b" * 15]:
        with pytest.raises(ValueError, match="invalid trace_id"):
            _validate_trace_id(bad)


# ---------------------------------------------------------------------------
# load_run_meta
# ---------------------------------------------------------------------------


def test_load_run_meta_returns_meta(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    trace_id = "b" * 32
    run_dir = runs_base / trace_id
    run_dir.mkdir(parents=True)
    meta = {
        "trace_id": trace_id,
        "run_name": "test_run",
        "started_at": "2026-01-01T12:00:00.000Z",
        "status": "ok",
        "counts": {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert load_run_meta(trace_id, config) == meta


def test_load_run_meta_accepts_uppercase_trace_id(temp_data_dir):
    config = load_config()
    trace_id = "a" * 32
    run_dir = config.data_dir / "runs" / trace_id
    run_dir.mkdir(parents=True)
    meta = {
        "trace_id": trace_id,
        "run_name": "upper",
        "started_at": "2026-01-01T12:00:00.000Z",
        "status": "ok",
        "counts": {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    assert load_run_meta(trace_id.upper(), config) == meta


def test_load_run_meta_missing_raises(temp_data_dir):
    config = load_config()
    with pytest.raises(FileNotFoundError, match="No run found"):
        load_run_meta("c" * 32, config)


# ---------------------------------------------------------------------------
# load_spans
# ---------------------------------------------------------------------------


def _write_spans(run_dir, trace_id, spans):
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(s) for s in spans]
    (run_dir / "spans.jsonl").write_text("\n".join(lines), encoding="utf-8")


def test_load_spans_returns_spans(temp_data_dir):
    config = load_config()
    trace_id = "d" * 32
    run_dir = config.data_dir / "runs" / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"trace_id": trace_id, "run_name": "s", "status": "ok", "counts": {}}
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    spans = [
        {"trace_id": trace_id, "span_id": "e" * 16, "name": "span1"},
        {"trace_id": trace_id, "span_id": "f" * 16, "name": "span2"},
    ]
    _write_spans(run_dir, trace_id, spans)
    loaded = load_spans(trace_id, config)
    assert len(loaded) == 2
    assert loaded[0]["name"] == "span1"


def test_load_spans_skips_invalid_json_lines(temp_data_dir):
    config = load_config()
    trace_id = "d" * 32
    run_dir = config.data_dir / "runs" / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"trace_id": trace_id, "status": "ok", "counts": {}}
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    valid1 = json.dumps({"trace_id": trace_id, "name": "valid1"})
    valid2 = json.dumps({"trace_id": trace_id, "name": "valid2"})
    (run_dir / "spans.jsonl").write_text(
        valid1 + "\nnot valid json\n" + valid2 + "\n{broken\n", encoding="utf-8"
    )
    loaded = load_spans(trace_id, config)
    assert len(loaded) == 2
    assert loaded[0]["name"] == "valid1"
    assert loaded[1]["name"] == "valid2"


def test_load_spans_missing_file_returns_empty(temp_data_dir):
    config = load_config()
    trace_id = "d" * 32
    run_dir = config.data_dir / "runs" / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {"trace_id": trace_id, "status": "ok", "counts": {}}
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    assert load_spans(trace_id, config) == []


# ---------------------------------------------------------------------------
# resolve_trace_id prefix matching
# ---------------------------------------------------------------------------


def _write_meta(run_dir, trace_id, run_name, started_at):
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "trace_id": trace_id,
        "run_name": run_name,
        "started_at": started_at,
        "ended_at": None,
        "duration_ms": None,
        "status": "running",
        "counts": {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")


def test_resolve_trace_id_exact_match(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    trace_id = "a" * 32
    _write_meta(runs_base / trace_id, trace_id, "exact", "2026-01-01T12:00:00.000Z")
    assert resolve_trace_id(trace_id, config) == trace_id


def test_resolve_trace_id_prefix_single_match(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    trace_id = "e" * 32
    _write_meta(runs_base / trace_id, trace_id, "single", "2026-01-01T12:00:00.000Z")
    assert resolve_trace_id(trace_id[:8], config) == trace_id


def test_resolve_trace_id_prefix_multiple_returns_most_recent(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    older_id = "c2aade11" + "b" * 24
    newer_id = "c2aade11" + "c" * 24
    _write_meta(runs_base / older_id, older_id, "old", "2026-01-01T10:00:00.000Z")
    _write_meta(runs_base / newer_id, newer_id, "new", "2026-01-01T14:00:00.000Z")
    assert resolve_trace_id("c2aade11", config) == newer_id


def test_resolve_trace_id_no_match_raises(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    trace_id = "a" * 32
    _write_meta(runs_base / trace_id, trace_id, "only", "2026-01-01T12:00:00.000Z")
    with pytest.raises(FileNotFoundError, match="No run found matching"):
        resolve_trace_id("nonexistent", config)


def test_resolve_trace_id_rejects_path_traversal(temp_data_dir):
    config = load_config()
    for bad in ["../foo", "a/b", "a\\b"]:
        with pytest.raises(FileNotFoundError, match="Trace ID is required"):
            resolve_trace_id(bad, config)


def test_resolve_trace_id_empty_prefix_raises(temp_data_dir):
    config = load_config()
    with pytest.raises(FileNotFoundError, match="Trace ID is required"):
        resolve_trace_id("", config)
    with pytest.raises(FileNotFoundError, match="Trace ID is required"):
        resolve_trace_id("   ", config)


# ---------------------------------------------------------------------------
# list_runs ordering
# ---------------------------------------------------------------------------


def test_list_runs_returns_runs_ordered_by_started_at_descending(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    runs_base.mkdir(parents=True, exist_ok=True)
    ids_and_times = [
        ("e4ccff33" + "a" * 24, "2026-01-01T08:00:00.000Z"),
        ("e4ccff33" + "b" * 24, "2026-01-01T16:00:00.000Z"),
        ("e4ccff33" + "c" * 24, "2026-01-01T12:00:00.000Z"),
    ]
    for trace_id, started_at in ids_and_times:
        _write_meta(runs_base / trace_id, trace_id, "run", started_at)
    listed = list_runs(limit=10, config=config)
    assert len(listed) == 3
    assert [r["trace_id"] for r in listed] == [
        "e4ccff33" + "b" * 24,
        "e4ccff33" + "c" * 24,
        "e4ccff33" + "a" * 24,
    ]


# ---------------------------------------------------------------------------
# rename_run / delete_run
# ---------------------------------------------------------------------------


def test_rename_run_updates_meta(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    trace_id = "a" * 32
    _write_meta(runs_base / trace_id, trace_id, "before", "2026-01-01T12:00:00.000Z")
    renamed = rename_run(trace_id, "after", config)
    assert renamed["run_name"] == "after"
    assert load_run_meta(trace_id, config)["run_name"] == "after"


def test_rename_run_empty_name_raises(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    trace_id = "a" * 32
    _write_meta(runs_base / trace_id, trace_id, "test", "2026-01-01T12:00:00.000Z")
    with pytest.raises(ValueError, match="run_name must be non-empty"):
        rename_run(trace_id, "", config)


def test_delete_run_removes_directory(temp_data_dir):
    config = load_config()
    runs_base = config.data_dir / "runs"
    trace_id = "a" * 32
    run_dir = runs_base / trace_id
    _write_meta(run_dir, trace_id, "delete_me", "2026-01-01T12:00:00.000Z")
    assert run_dir.is_dir()
    delete_run(trace_id, config)
    assert not run_dir.exists()


def test_delete_run_missing_raises(temp_data_dir):
    config = load_config()
    with pytest.raises(FileNotFoundError, match="No run found"):
        delete_run("a" * 32, config)


# ---------------------------------------------------------------------------
# spans_to_events integration
# ---------------------------------------------------------------------------


def test_spans_to_events_produces_expected_event_types(temp_data_dir):
    """A root span + child tool span produces RUN_START, TOOL_CALL, RUN_END."""
    config = load_config()
    trace_id = "a" * 32
    run_dir = config.data_dir / "runs" / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "trace_id": trace_id,
        "run_name": "events_test",
        "status": "ok",
        "counts": {},
    }
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    root_span_id = "1" * 16
    child_span_id = "2" * 16
    spans = [
        {
            "trace_id": trace_id,
            "span_id": root_span_id,
            "parent_span_id": None,
            "name": "events_test",
            "start_time": "2026-01-01T12:00:00.000Z",
            "end_time": "2026-01-01T12:00:01.000Z",
            "duration_ms": 1000,
            "attributes": {"maida.run_name": "events_test"},
            "events": [],
            "status_code": "OK",
        },
        {
            "trace_id": trace_id,
            "span_id": child_span_id,
            "parent_span_id": root_span_id,
            "name": "my_tool",
            "start_time": "2026-01-01T12:00:00.500Z",
            "end_time": "2026-01-01T12:00:00.600Z",
            "duration_ms": 100,
            "attributes": {"maida.tool_name": "my_tool", "maida.status": "ok"},
            "events": [],
            "status_code": "OK",
        },
    ]
    _write_spans(run_dir, trace_id, spans)

    loaded_spans = load_spans(trace_id, config)
    events = spans_to_events(loaded_spans)
    event_types = [e["event_type"] for e in events]

    assert EventType.RUN_START.value in event_types
    assert EventType.TOOL_CALL.value in event_types
    assert EventType.RUN_END.value in event_types

    tool_events = [e for e in events if e["event_type"] == EventType.TOOL_CALL.value]
    assert len(tool_events) == 1
    assert tool_events[0]["payload"]["tool_name"] == "my_tool"


# ---------------------------------------------------------------------------
# resolve_latest_trace_id
# ---------------------------------------------------------------------------


def test_resolve_latest_trace_id_returns_most_recent(temp_data_dir):
    from maida.storage import resolve_latest_trace_id

    config = load_config()
    runs_base = config.data_dir / "runs"
    older_id = "d" * 32
    newer_id = "e" * 32
    _write_meta(runs_base / older_id, older_id, "old", "2026-01-01T10:00:00.000Z")
    _write_meta(runs_base / newer_id, newer_id, "new", "2026-01-01T14:00:00.000Z")
    assert resolve_latest_trace_id(config) == newer_id


def test_resolve_latest_trace_id_no_runs_raises(temp_data_dir):
    from maida.storage import resolve_latest_trace_id

    config = load_config()
    with pytest.raises(FileNotFoundError, match="No runs found"):
        resolve_latest_trace_id(config)


def test_resolve_latest_trace_id_missing_runs_dir_raises(temp_data_dir):
    import dataclasses

    from maida.storage import resolve_latest_trace_id

    config = dataclasses.replace(load_config(), data_dir=temp_data_dir / "nonexistent")
    with pytest.raises(FileNotFoundError, match="No runs found"):
        resolve_latest_trace_id(config)
