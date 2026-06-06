"""
Storage tests: create_run, append_event/load_events, finalize_run.
Uses temp dir via MAIDA_DATA_DIR; env restored by fixture.
"""

import json
from unittest.mock import patch

import pytest

from maida.config import load_config
from maida.events import EventType, new_event
from maida.storage import (
    _validate_trace_id,
    append_event,
    create_run,
    finalize_run,
    load_events,
    load_run_meta,
    list_runs,
    resolve_run_id,
)


def test_validate_trace_id_normalizes_uppercase(temp_data_dir):
    """Uppercase trace IDs normalize to lowercase for filesystem lookup."""
    assert _validate_trace_id("A" * 32) == "a" * 32


def test_create_run_writes_run_json_with_status_running(temp_data_dir):
    """create_run writes run.json with status 'running'."""
    config = load_config()
    meta = create_run("test_run", config)
    run_id = meta["run_id"]
    path = meta["paths"]["run_json"]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data.get("status") == "running"
    assert data.get("run_id") == run_id


def test_append_event_writes_events_jsonl_load_events_reads_back(temp_data_dir):
    """append_event writes to events.jsonl and load_events reads it back."""
    config = load_config()
    meta = create_run("test_run", config)
    run_id = meta["run_id"]
    ev = new_event(
        EventType.TOOL_CALL, run_id, "tool1", {"tool_name": "tool1", "args": {}}
    )
    append_event(run_id, ev, config)
    loaded = load_events(run_id, config)
    assert len(loaded) == 1
    assert loaded[0].get("event_type") == EventType.TOOL_CALL.value
    assert loaded[0].get("payload", {}).get("tool_name") == "tool1"


def test_finalize_run_sets_status_ok_ended_at_duration_ms(temp_data_dir):
    """finalize_run sets status 'ok' and sets ended_at and duration_ms not None."""
    config = load_config()
    meta = create_run("test_run", config)
    run_id = meta["run_id"]
    counts = {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0}
    finalize_run(run_id, "ok", counts, config)
    run_meta = load_run_meta(run_id, config)
    assert run_meta.get("status") == "ok"
    assert run_meta.get("ended_at") is not None
    assert run_meta.get("duration_ms") is not None


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


# ---------------------------------------------------------------------------
# resolve_run_id prefix matching
# ---------------------------------------------------------------------------


def _write_run_json(run_dir, run_id: str, run_name: str, started_at: str) -> None:
    """Write a minimal valid run.json into run_dir."""
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "spec_version": "0.1",
                "run_id": run_id,
                "run_name": run_name,
                "started_at": started_at,
                "ended_at": None,
                "duration_ms": None,
                "status": "running",
                "counts": {
                    "llm_calls": 0,
                    "tool_calls": 0,
                    "errors": 0,
                    "loop_warnings": 0,
                },
                "last_event_ts": None,
            }
        ),
        encoding="utf-8",
    )


def test_resolve_run_id_exact_match_returns_run_id(temp_data_dir):
    """resolve_run_id with full run_id returns that run_id."""
    config = load_config()
    runs_base = config.data_dir / "runs"
    runs_base.mkdir(parents=True, exist_ok=True)
    run_id = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    _write_run_json(runs_base / run_id, run_id, "exact", "2026-01-01T12:00:00.000Z")
    assert resolve_run_id(run_id, config) == run_id


def test_resolve_run_id_prefix_single_match_returns_full_run_id(temp_data_dir):
    """resolve_run_id with prefix matching exactly one run returns that run_id."""
    config = load_config()
    runs_base = config.data_dir / "runs"
    runs_base.mkdir(parents=True, exist_ok=True)
    run_id = "b1ffcd00-0a1c-4ef8-bb6d-6bb9bd380a11"
    _write_run_json(runs_base / run_id, run_id, "single", "2026-01-01T12:00:00.000Z")
    assert resolve_run_id("b1ffcd00", config) == run_id


def test_resolve_run_id_prefix_multiple_matches_returns_most_recent_by_started_at(
    temp_data_dir,
):
    """resolve_run_id with prefix matching multiple runs returns the most recent by started_at."""
    config = load_config()
    runs_base = config.data_dir / "runs"
    runs_base.mkdir(parents=True, exist_ok=True)
    older_id = "c2aade11-1b2d-4ef8-bb6d-6bb9bd380a11"
    newer_id = "c2aade11-2c3e-4ef8-8b6d-6bb9bd380a22"
    _write_run_json(runs_base / older_id, older_id, "old", "2026-01-01T10:00:00.000Z")
    _write_run_json(runs_base / newer_id, newer_id, "new", "2026-01-01T14:00:00.000Z")
    assert resolve_run_id("c2aade11", config) == newer_id


def test_resolve_run_id_no_match_raises_file_not_found(temp_data_dir):
    """resolve_run_id with no matching run raises FileNotFoundError."""
    config = load_config()
    runs_base = config.data_dir / "runs"
    runs_base.mkdir(parents=True, exist_ok=True)
    run_id = "d3bbef22-2c3e-7hi1-ee9g-9ee2eg613d44"
    _write_run_json(runs_base / run_id, run_id, "only", "2026-01-01T12:00:00.000Z")
    with pytest.raises(FileNotFoundError, match="No run found matching"):
        resolve_run_id("nonexistent", config)


def test_resolve_run_id_rejects_path_traversal(temp_data_dir):
    """resolve_run_id rejects prefix containing .. or path separators."""
    config = load_config()
    runs_base = config.data_dir / "runs"
    runs_base.mkdir(parents=True, exist_ok=True)
    for bad in ["../foo", "a/b", "a\\b"]:
        with pytest.raises(FileNotFoundError, match="Run ID is required"):
            resolve_run_id(bad, config)


def test_resolve_run_id_empty_prefix_raises(temp_data_dir):
    """resolve_run_id with empty or whitespace prefix raises FileNotFoundError."""
    config = load_config()
    with pytest.raises(FileNotFoundError, match="Run ID is required"):
        resolve_run_id("", config)
    with pytest.raises(FileNotFoundError, match="Run ID is required"):
        resolve_run_id("   ", config)


# ---------------------------------------------------------------------------
# list_runs ordering
# ---------------------------------------------------------------------------


def test_list_runs_returns_runs_ordered_by_started_at_descending(temp_data_dir):
    """list_runs returns runs ordered by started_at descending (most recent first)."""
    config = load_config()
    runs_base = config.data_dir / "runs"
    runs_base.mkdir(parents=True, exist_ok=True)
    ids_and_times = [
        ("e4ccff33-3d4f-4ef8-bb6d-6bb9bd380a11", "2026-01-01T08:00:00.000Z"),
        ("e4ccff33-4e5f-4ef8-8b6d-7cc0ce491b22", "2026-01-01T16:00:00.000Z"),
        ("e4ccff33-5f6a-4ef8-9b6d-8dd1df502c33", "2026-01-01T12:00:00.000Z"),
    ]
    for run_id, started_at in ids_and_times:
        _write_run_json(runs_base / run_id, run_id, "run", started_at)
    listed = list_runs(limit=10, config=config)
    assert len(listed) == 3
    assert [r["run_id"] for r in listed] == [
        "e4ccff33-4e5f-4ef8-8b6d-7cc0ce491b22",
        "e4ccff33-5f6a-4ef8-9b6d-8dd1df502c33",
        "e4ccff33-3d4f-4ef8-bb6d-6bb9bd380a11",
    ]


# ---------------------------------------------------------------------------
# load_events corrupt JSONL
# ---------------------------------------------------------------------------


def test_load_events_skips_invalid_json_lines(temp_data_dir):
    """load_events skips corrupt/invalid JSON lines and returns only valid events."""
    config = load_config()
    meta = create_run("corrupt_test", config)
    run_id = meta["run_id"]
    events_path = config.data_dir / "runs" / run_id / "events.jsonl"
    valid1 = json.dumps({"event_type": "RUN_START", "run_id": run_id, "payload": {}})
    valid2 = json.dumps({"event_type": "RUN_END", "run_id": run_id, "payload": {}})
    events_path.write_text(
        valid1 + "\nnot valid json\n" + valid2 + "\n{broken\n", encoding="utf-8"
    )
    loaded = load_events(run_id, config)
    assert len(loaded) == 2
    assert loaded[0].get("event_type") == "RUN_START"
    assert loaded[1].get("event_type") == "RUN_END"


def test_load_events_logs_warning_for_corrupt_jsonl_lines(temp_data_dir):
    """load_events logs a warning for each skipped corrupt JSONL line."""
    config = load_config()
    meta = create_run("corrupt_warn_test", config)
    run_id = meta["run_id"]
    events_path = config.data_dir / "runs" / run_id / "events.jsonl"
    # Line 1: valid, line 2: invalid, line 3: invalid
    events_path.write_text(
        '{"event_type": "RUN_START"}\nnot json\n{unclosed\n',
        encoding="utf-8",
    )
    with patch("maida.storage.logger") as mock_logger:
        loaded = load_events(run_id, config)
    assert len(loaded) == 1
    assert loaded[0].get("event_type") == "RUN_START"
    assert mock_logger.warning.call_count == 2
    # First corrupt line at line_no 2
    call1 = mock_logger.warning.call_args_list[0]
    assert (
        call1[0][0] == "load_events: skipping corrupt JSONL line run_id=%s line=%s: %s"
    )
    assert call1[0][1] == run_id
    assert call1[0][2] == 2
    assert isinstance(call1[0][3], json.JSONDecodeError)
    # Second corrupt line at line_no 3
    call2 = mock_logger.warning.call_args_list[1]
    assert call2[0][1] == run_id
    assert call2[0][2] == 3
    assert isinstance(call2[0][3], json.JSONDecodeError)
