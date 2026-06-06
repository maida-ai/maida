"""
CLI tests using Typer CliRunner.
Every test uses temp dir via MAIDA_DATA_DIR (fixture restores env).
Covers: list, export, view, baseline, assert, diff commands.
"""

import json
import socket
import threading
import time

import pytest
from typer.testing import CliRunner

from maida.cli import _wait_for_port, app
from maida.config import load_config
from maida.events import EventType, new_event
from maida.storage import append_event, create_run, finalize_run

runner = CliRunner()


def _make_run(config, *, name="test_run", events=None, status="ok"):
    """Helper: create a run, append events, finalize, return run_id."""
    run = create_run(name, config)
    run_id = run["run_id"]
    counts = {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0}
    for ev_type, ev_name, payload in events or []:
        ev = new_event(ev_type, run_id, ev_name, payload)
        append_event(run_id, ev, config)
        if ev_type == EventType.TOOL_CALL:
            counts["tool_calls"] += 1
        elif ev_type == EventType.LLM_CALL:
            counts["llm_calls"] += 1
        elif ev_type == EventType.ERROR:
            counts["errors"] += 1
        elif ev_type == EventType.LOOP_WARNING:
            counts["loop_warnings"] += 1
    finalize_run(run_id, status, counts, config)
    return run_id


@pytest.fixture
def empty_data_dir(temp_data_dir):
    """Empty data dir with MAIDA_DATA_DIR set (env restored after test)."""
    return temp_data_dir


def test_list_empty_dir_exit_zero(empty_data_dir):
    """maida list on empty dir exits code 0."""
    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0


def test_export_missing_run_exit_two(empty_data_dir):
    """maida export missing_run --out <tmpfile> exits code 2."""
    tmpfile = empty_data_dir / "out.json"
    result = runner.invoke(app, ["export", "missing_run", "--out", str(tmpfile)])
    assert result.exit_code == 2


def test_export_accepts_run_id_prefix(empty_data_dir):
    """maida export with run_id prefix resolves to full run and writes correct JSON."""
    from maida.config import load_config

    config = load_config()
    run_id = "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11"
    run_dir = config.data_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "spec_version": "0.1",
                "run_id": run_id,
                "run_name": "prefix_test",
                "started_at": "2026-01-01T00:00:00.000Z",
                "ended_at": None,
                "duration_ms": 0,
                "status": "ok",
                "counts": {
                    "llm_calls": 0,
                    "tool_calls": 0,
                    "errors": 0,
                    "loop_warnings": 0,
                },
                "last_event_ts": None,
            }
        )
    )
    (run_dir / "events.jsonl").write_text("")

    prefix = run_id[:8]
    tmpfile = empty_data_dir / "exported.json"
    result = runner.invoke(app, ["export", prefix, "--out", str(tmpfile)])
    assert result.exit_code == 0
    data = json.loads(tmpfile.read_text())
    assert data["run"]["run_id"] == run_id
    assert data["run"]["run_name"] == "prefix_test"
    assert "events" in data


def test_export_success_path_writes_run_and_events(empty_data_dir):
    """maida export with real run (create_run + append_event) exits 0 and writes run + events."""
    from maida.config import load_config
    from maida.events import EventType, new_event
    from maida.storage import append_event, create_run
    from tests.conftest import get_latest_run_id

    config = load_config()
    create_run("export_success_run", config)
    run_id = get_latest_run_id(config)
    ev = new_event(
        EventType.TOOL_CALL, run_id, "test_tool", {"tool_name": "test_tool", "args": {}}
    )
    append_event(run_id, ev, config)

    tmpfile = empty_data_dir / "export_success.json"
    result = runner.invoke(app, ["export", run_id, "--out", str(tmpfile)])
    assert result.exit_code == 0
    data = json.loads(tmpfile.read_text())
    assert data["spec_version"] == "0.1"
    assert data["run"]["run_id"] == run_id
    assert data["run"]["run_name"] == "export_success_run"
    assert len(data["events"]) == 1
    assert data["events"][0].get("event_type") == EventType.TOOL_CALL.value


def _write_trace_run(temp_data_dir, trace_id, run_name):
    config = load_config()
    run_dir = config.data_dir / "runs" / trace_id
    run_dir.mkdir(parents=True, exist_ok=True)
    meta = {
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
        "trace_id": trace_id,
        "span_id": "0" * 16,
        "parent_span_id": None,
        "name": run_name,
        "start_time": "2026-01-01T00:00:00.000Z",
        "end_time": "2026-01-01T00:00:01.000Z",
        "duration_ms": 1000,
        "attributes": {"maida.run_name": run_name},
        "events": [],
        "status_code": "OK",
    }
    (run_dir / "spans.jsonl").write_text(json.dumps(root_span) + "\n", encoding="utf-8")


def test_list_json_outputs_valid_json_spec_version_and_runs(empty_data_dir):
    """maida list --json outputs valid JSON with keys spec_version and runs."""
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "spec_version" in data
    assert "runs" in data
    assert data["spec_version"] == "0.1"
    assert isinstance(data["runs"], list)


def test_list_with_actual_runs_shows_runs(empty_data_dir):
    """maida list with real runs shows run_id/run_name in text output and in --json runs."""
    from maida.config import load_config
    from maida.storage import create_run
    from tests.conftest import get_latest_run_id

    config = load_config()
    create_run("list_me_run", config)
    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert run_id in result.output or "list_me_run" in result.output

    result_json = runner.invoke(app, ["list", "--json"])
    assert result_json.exit_code == 0
    data = json.loads(result_json.output)
    assert len(data["runs"]) >= 1
    assert data["runs"][0]["run_id"] == run_id
    assert data["runs"][0]["run_name"] == "list_me_run"


def test_list_with_trace_id_run_shows_short_trace_id(empty_data_dir):
    trace_id = "a0eebc99" + "a" * 24
    _write_trace_run(empty_data_dir, trace_id, "trace_list")

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert trace_id[:8] in result.output
    assert "trace_list" in result.output

    result_json = runner.invoke(app, ["list", "--json"])
    assert result_json.exit_code == 0
    data = json.loads(result_json.output)
    assert data["runs"][0]["trace_id"] == trace_id


def test_view_defaults_to_latest_trace_id(monkeypatch, empty_data_dir):
    trace_id = "b0eebc99" + "b" * 24
    _write_trace_run(empty_data_dir, trace_id, "trace_view")
    monkeypatch.setattr("uvicorn.run", lambda **kwargs: None)

    result = runner.invoke(app, ["view", "--no-browser", "--json", "--port", "0"])

    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["run_id"] == trace_id
    assert f"run_id={trace_id}" in data["url"]


# ---------------------------------------------------------------------------
# _wait_for_port readiness-probe tests
# ---------------------------------------------------------------------------


def test_wait_for_port_returns_true_when_port_opens():
    """_wait_for_port returns True once a TCP listener appears on the port."""
    # Bind to an ephemeral port but don't accept yet.
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

    # Start listening after a short delay (simulates server startup lag).
    def _delayed_listen() -> None:
        time.sleep(0.15)
        srv.listen(1)

    t = threading.Thread(target=_delayed_listen, daemon=True)
    t.start()

    try:
        assert _wait_for_port("127.0.0.1", port, timeout_s=3.0) is True
    finally:
        srv.close()
        t.join(timeout=2)


def test_wait_for_port_returns_false_on_timeout():
    """_wait_for_port returns False when no listener appears before timeout."""
    # Grab an ephemeral port number, then close it so nothing listens.
    tmp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tmp.bind(("127.0.0.1", 0))
    port = tmp.getsockname()[1]
    tmp.close()

    assert _wait_for_port("127.0.0.1", port, timeout_s=0.3) is False


def test_view_opens_browser_only_after_wait_succeeds(monkeypatch, empty_data_dir):
    """webbrowser.open is called only after _wait_for_port returns True."""
    # Track call ordering.
    call_log: list[str] = []

    def fake_wait_for_port(host: str, port: int, timeout_s: float = 5.0) -> bool:
        call_log.append("wait")
        return True

    def fake_webbrowser_open(url: str, *a, **kw) -> None:
        # At the moment the browser is opened, 'wait' must already be logged.
        assert "wait" in call_log, "webbrowser.open called before readiness wait"
        call_log.append("browser")

    # Patch _wait_for_port at the module level so view_cmd picks it up.
    monkeypatch.setattr("maida.cli._wait_for_port", fake_wait_for_port)
    monkeypatch.setattr("maida.cli.webbrowser.open", fake_webbrowser_open)

    # Patch uvicorn.run so no real server starts; make it block briefly.
    def fake_uvicorn_run(**kwargs) -> None:
        time.sleep(0.1)

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    # View starts the server even with no runs (empty_data_dir); browser opens after wait.
    result = runner.invoke(app, ["view"])
    assert result.exit_code == 0
    assert call_log == ["wait", "browser"]


def test_view_server_stays_running_until_interrupt(monkeypatch, empty_data_dir):
    """View command blocks until server exits; server runs until fake uvicorn returns (simulates Ctrl+C)."""
    block_event = threading.Event()

    def fake_uvicorn_run(**kwargs):
        block_event.wait(timeout=3)

    monkeypatch.setattr("maida.cli._wait_for_port", lambda *a, **kw: True)
    monkeypatch.setattr("maida.cli.webbrowser.open", lambda *a, **kw: None)
    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    view_result = {"done": False, "exit_code": None}

    def run_view():
        r = runner.invoke(app, ["view", "--no-browser", "--port", "9199"])
        view_result["done"] = True
        view_result["exit_code"] = r.exit_code

    view_thread = threading.Thread(target=run_view)
    view_thread.start()
    time.sleep(0.4)
    assert view_thread.is_alive(), (
        "view should still be running (blocked on server join)"
    )
    block_event.set()
    view_thread.join(timeout=5)
    assert view_result["done"]
    assert view_result["exit_code"] == 0


# ---------------------------------------------------------------------------
# baseline command
# ---------------------------------------------------------------------------


def test_baseline_creates_file(empty_data_dir):
    """maida baseline RUN_ID --out <path> creates a valid baseline JSON."""
    config = load_config()
    run_id = _make_run(
        config,
        events=[(EventType.TOOL_CALL, "search", {})],
    )
    out = empty_data_dir / "bl.json"
    result = runner.invoke(app, ["baseline", run_id, "--out", str(out)])
    assert result.exit_code == 0
    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["source_run_id"] == run_id
    assert "summary" in data
    assert data["summary"]["tool_calls"] == 1


def test_baseline_missing_run_exit_two(empty_data_dir):
    result = runner.invoke(
        app, ["baseline", "missing_run", "--out", str(empty_data_dir / "bl.json")]
    )
    assert result.exit_code == 2


# ---------------------------------------------------------------------------
# assert command
# ---------------------------------------------------------------------------


def test_assert_exit_zero_on_pass(empty_data_dir):
    """assert exits 0 when all checks pass."""
    config = load_config()
    run_id = _make_run(config, events=[(EventType.TOOL_CALL, "t", {})])
    result = runner.invoke(app, ["assert", run_id, "--max-steps", "10"])
    assert result.exit_code == 0
    assert "PASSED" in result.output


def test_assert_exit_one_on_fail(empty_data_dir):
    """assert exits 1 when a check fails."""
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(5)]
    run_id = _make_run(config, events=events)
    result = runner.invoke(app, ["assert", run_id, "--max-steps", "3"])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_assert_exit_two_missing_baseline(empty_data_dir):
    """assert exits 2 when --baseline points to nonexistent file."""
    config = load_config()
    run_id = _make_run(config, events=[])
    result = runner.invoke(
        app,
        ["assert", run_id, "--baseline", str(empty_data_dir / "nope.json")],
    )
    assert result.exit_code == 2


def test_assert_json_format(empty_data_dir):
    """assert --format json outputs valid JSON."""
    config = load_config()
    run_id = _make_run(config, events=[])
    result = runner.invoke(
        app, ["assert", run_id, "--max-steps", "10", "--format", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["passed"] is True


def test_assert_markdown_format(empty_data_dir):
    """assert --format markdown outputs markdown table."""
    config = load_config()
    run_id = _make_run(config, events=[])
    result = runner.invoke(
        app, ["assert", run_id, "--max-steps", "10", "--format", "markdown"]
    )
    assert result.exit_code == 0
    assert "Maida Assertion Report" in result.output


def test_assert_with_baseline(empty_data_dir):
    """assert with --baseline compares against baseline correctly."""
    config = load_config()
    bl_run = _make_run(
        config,
        name="baseline_run",
        events=[(EventType.TOOL_CALL, "t", {}) for _ in range(5)],
    )
    bl_path = empty_data_dir / "bl.json"
    runner.invoke(app, ["baseline", bl_run, "--out", str(bl_path)])

    check_run = _make_run(
        config,
        name="check_run",
        events=[(EventType.TOOL_CALL, "t", {}) for _ in range(5)],
    )
    result = runner.invoke(
        app,
        [
            "assert",
            check_run,
            "--baseline",
            str(bl_path),
            "--max-steps",
            "100",
            # Baseline auto-enables a duration check (default tolerance 50%).
            # In-process _make_run timing has tens-of-ms jitter on Windows
            # (fsync variability), which is unrelated to this test's intent
            # of verifying baseline-comparison plumbing. Use a generous
            # tolerance so duration jitter cannot flake the check.
            "--duration-tolerance",
            "100",
        ],
    )
    assert result.exit_code == 0


def test_assert_no_loops_flag(empty_data_dir):
    """assert --no-loops fails when loop warnings present."""
    config = load_config()
    run_id = _make_run(
        config,
        events=[(EventType.LOOP_WARNING, "loop", {"pattern": "A"})],
    )
    result = runner.invoke(app, ["assert", run_id, "--no-loops"])
    assert result.exit_code == 1


# ---------------------------------------------------------------------------
# diff command
# ---------------------------------------------------------------------------


def test_diff_two_runs(empty_data_dir):
    """diff RUN_A RUN_B produces output with run comparison header."""
    config = load_config()
    rid_a = _make_run(
        config,
        name="a",
        events=[(EventType.TOOL_CALL, "search", {})],
    )
    rid_b = _make_run(
        config,
        name="b",
        events=[(EventType.TOOL_CALL, "parse", {})],
    )
    result = runner.invoke(app, ["diff", rid_a, rid_b])
    assert result.exit_code == 0
    assert "Run comparison:" in result.output


def test_diff_with_baseline(empty_data_dir):
    """diff RUN_A --baseline <file> works."""
    config = load_config()
    bl_run = _make_run(
        config,
        name="bl",
        events=[(EventType.TOOL_CALL, "t", {})],
    )
    bl_path = empty_data_dir / "bl.json"
    runner.invoke(app, ["baseline", bl_run, "--out", str(bl_path)])

    run_id = _make_run(
        config,
        name="current",
        events=[
            (EventType.TOOL_CALL, "t", {}),
            (EventType.TOOL_CALL, "new_tool", {}),
        ],
    )
    result = runner.invoke(app, ["diff", run_id, "--baseline", str(bl_path)])
    assert result.exit_code == 0
    assert "new_tool" in result.output


def test_diff_missing_args(empty_data_dir):
    """diff with only one run and no --baseline exits with error."""
    config = load_config()
    rid = _make_run(config, events=[])
    result = runner.invoke(app, ["diff", rid])
    assert result.exit_code == 2
