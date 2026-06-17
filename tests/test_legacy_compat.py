"""Compatibility tests for legacy run.json/events.jsonl storage layouts."""

import json

import pytest
from typer.testing import CliRunner

from maida.assertions import AssertionPolicy, run_assertions
from maida.baseline import create_baseline
from maida.cli import app
from maida.config import load_config
from maida.diff import compute_diff
from maida.events import EventType
from maida.storage import UnsupportedTraceFormatError, load_run_for_analysis

runner = CliRunner()


def _event(run_id, event_type, name, payload=None, *, ts="2026-01-01T00:00:00.000Z"):
    return {
        "spec_version": "0.2",
        "event_id": f"{name}-event",
        "run_id": run_id,
        "parent_id": None,
        "event_type": event_type,
        "ts": ts,
        "duration_ms": 10,
        "name": name,
        "payload": payload or {},
        "meta": {},
    }


def _write_legacy_run(config, run_id, *, spec_version="0.2", events=None):
    run_dir = config.data_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "spec_version": spec_version,
        "run_id": run_id,
        "run_name": f"legacy-{run_id}",
        "started_at": "2026-01-01T00:00:00.000Z",
        "ended_at": "2026-01-01T00:00:00.100Z",
        "duration_ms": 100,
        "status": "ok",
        "counts": {"llm_calls": 0, "tool_calls": 1, "errors": 0, "loop_warnings": 0},
    }
    (run_dir / "run.json").write_text(json.dumps(meta), encoding="utf-8")
    lines = [json.dumps(event) for event in events or []]
    (run_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return meta


def test_legacy_02_run_projects_into_structural_paths(temp_data_dir):
    config = load_config()
    run_id = "legacy-run-02"
    baseline_id = "legacy-baseline-02"
    _write_legacy_run(
        config,
        baseline_id,
        events=[
            _event(baseline_id, EventType.RUN_START.value, "baseline"),
            _event(
                baseline_id,
                EventType.TOOL_CALL.value,
                "search",
                {"tool_name": "search", "args": {}, "result": "ok", "status": "ok"},
            ),
            _event(baseline_id, EventType.RUN_END.value, "baseline", {"status": "ok"}),
        ],
    )
    _write_legacy_run(
        config,
        run_id,
        events=[
            _event(run_id, EventType.RUN_START.value, "current"),
            _event(
                run_id,
                EventType.TOOL_CALL.value,
                "search",
                {"tool_name": "search", "args": {}, "result": "ok", "status": "ok"},
            ),
            _event(
                run_id,
                EventType.TOOL_CALL.value,
                "parse",
                {"tool_name": "parse", "args": {}, "result": "ok", "status": "ok"},
            ),
            _event(run_id, EventType.RUN_END.value, "current", {"status": "ok"}),
        ],
    )

    resolved_id, meta, events = load_run_for_analysis(run_id, config)
    baseline = create_baseline(baseline_id, config)
    diff = compute_diff(run_id, baseline=baseline, config=config)
    report = run_assertions(
        run_id,
        AssertionPolicy(no_new_tools=True, max_tool_calls=3, step_tolerance=10.0),
        baseline=baseline,
        config=config,
    )

    assert resolved_id == run_id
    assert meta["run_name"] == "legacy-legacy-run-02"
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        EventType.TOOL_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.RUN_END.value,
    ]
    assert baseline["summary"]["tool_calls"] == 1
    assert diff.new_tools == ["parse"]
    assert not report.passed
    assert [result.check_name for result in report.results if not result.passed] == [
        "new_tools"
    ]


def test_unsupported_legacy_trace_fails_with_upgrade_guidance(temp_data_dir):
    config = load_config()
    run_id = "legacy-run-01"
    _write_legacy_run(
        config,
        run_id,
        spec_version="0.1",
        events=[
            _event(
                run_id,
                EventType.TOOL_CALL.value,
                "search",
                {"secret": "sk-test-DO-NOT-LEAK"},
            )
        ],
    )

    with pytest.raises(UnsupportedTraceFormatError) as excinfo:
        load_run_for_analysis(run_id, config)

    message = str(excinfo.value)
    assert "unsupported trace format" in message
    assert "0.2" in message
    assert "maida demo" in message
    assert "sk-test-DO-NOT-LEAK" not in message


def test_cli_reports_unsupported_legacy_trace_as_user_error(temp_data_dir):
    config = load_config()
    run_id = "legacy-cli-01"
    _write_legacy_run(config, run_id, spec_version="0.1")

    result = runner.invoke(
        app, ["baseline", run_id, "--out", str(temp_data_dir / "bl.json")]
    )

    assert result.exit_code == 2
    assert "unsupported trace format" in result.stderr
    assert "maida demo" in result.stderr


def test_cli_export_uses_supported_legacy_projection(temp_data_dir):
    config = load_config()
    run_id = "legacy-export-02"
    _write_legacy_run(
        config,
        run_id,
        events=[
            _event(
                run_id,
                EventType.TOOL_CALL.value,
                "search",
                {"tool_name": "search", "args": {}, "result": "ok", "status": "ok"},
            )
        ],
    )
    out = temp_data_dir / "legacy-export.json"

    result = runner.invoke(app, ["export", run_id, "--out", str(out)])

    assert result.exit_code == 0
    payload = json.loads(out.read_text())
    assert payload["run"]["run_id"] == run_id
    assert payload["events"][0]["event_type"] == EventType.TOOL_CALL.value
