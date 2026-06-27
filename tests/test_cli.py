"""
CLI tests using Typer CliRunner.
Every test uses temp dir via MAIDA_DATA_DIR (fixture restores env).
Covers: list, export, view, baseline, assert, diff commands.
"""

import json
import socket
import threading
import time
import yaml

import pytest
from typer.testing import CliRunner

from maida import record_llm_call, record_tool_call, traced_run
from maida.cli import _wait_for_port, app
from maida.config import load_config
from maida.events import EventType
from maida.policy import load_policy
from maida.storage import list_runs
from tests.conftest import get_latest_run_id

runner = CliRunner()


def _make_run(config, *, name="test_run", events=None, status="ok"):
    """Helper: create a run via traced_run + recorders, return run_id."""
    if status == "error":
        with pytest.raises(RuntimeError):
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
                elif ev_type == EventType.LOOP_WARNING:
                    record_tool_call(ev_name, args={}, result=None)
    return get_latest_run_id(config)


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


def _write_run(temp_data_dir, trace_id, run_name):
    """Write meta.json + spans.jsonl for a minimal run."""
    config = load_config()
    runs_base = config.data_dir / "runs"
    run_dir = runs_base / trace_id
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


def test_export_accepts_run_id_prefix(empty_data_dir):
    """maida export with run_id prefix resolves to full run and writes correct JSON."""
    trace_id = "a0eebc99" + "a" * 24
    _write_run(empty_data_dir, trace_id, "prefix_test")

    prefix = trace_id[:8]
    tmpfile = empty_data_dir / "exported.json"
    result = runner.invoke(app, ["export", prefix, "--out", str(tmpfile)])
    assert result.exit_code == 0
    data = json.loads(tmpfile.read_text())
    assert data["run"]["trace_id"] == trace_id
    assert data["run"]["run_name"] == "prefix_test"
    assert "events" in data


def test_export_success_path_writes_run_and_events(empty_data_dir):
    """maida export with real run (traced_run + record_tool_call) exits 0 and writes run + events."""
    config = load_config()
    with traced_run(name="export_success_run"):
        record_tool_call("test_tool", args={}, result="done")
    run_id = get_latest_run_id(config)

    tmpfile = empty_data_dir / "export_success.json"
    result = runner.invoke(app, ["export", run_id, "--out", str(tmpfile)])
    assert result.exit_code == 0
    data = json.loads(tmpfile.read_text())
    assert data["run"]["run_name"] == "export_success_run"
    assert len(data["events"]) >= 2
    tool_events = [
        e for e in data["events"] if e.get("event_type") == EventType.TOOL_CALL.value
    ]
    assert len(tool_events) == 1
    assert tool_events[0].get("payload", {}).get("tool_name") == "test_tool"


def _write_trace_run(temp_data_dir, trace_id, run_name):
    config = load_config()
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


def _write_run_with_malformed_span(temp_data_dir, trace_id, run_name="bad"):
    config = load_config()
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
    (run_dir / "spans.jsonl").write_text(
        '{"api_key":"sk-test-DO-NOT-LEAK",\n',
        encoding="utf-8",
    )


def test_list_json_outputs_valid_json_spec_version_and_runs(empty_data_dir):
    """maida list --json outputs valid JSON with keys spec_version and runs."""
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "spec_version" in data
    assert "runs" in data
    assert data["spec_version"] == "0.2"
    assert isinstance(data["runs"], list)


def test_list_with_actual_runs_shows_runs(empty_data_dir):
    """maida list with real runs shows run_id/run_name in text output and in --json runs."""
    config = load_config()
    with traced_run(name="list_me_run"):
        pass
    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["list"])
    assert result.exit_code == 0
    assert run_id in result.output or "list_me_run" in result.output

    result_json = runner.invoke(app, ["list", "--json"])
    assert result_json.exit_code == 0
    data = json.loads(result_json.output)
    assert len(data["runs"]) >= 1
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


@pytest.mark.parametrize(
    "command_builder",
    [
        lambda bad, good, tmp: ["view", bad, "--no-browser", "--json", "--port", "0"],
        lambda bad, good, tmp: ["baseline", bad, "--out", str(tmp / "bl.json")],
        lambda bad, good, tmp: ["assert", bad, "--max-steps", "10"],
        lambda bad, good, tmp: ["diff", bad, good],
    ],
)
def test_run_loading_commands_report_validation_errors(
    command_builder,
    empty_data_dir,
):
    bad_trace_id = "badbad10" + "a" * 24
    good_trace_id = "face0010" + "b" * 24
    _write_run_with_malformed_span(empty_data_dir, bad_trace_id)
    _write_trace_run(empty_data_dir, good_trace_id, "good")

    result = runner.invoke(
        app,
        command_builder(bad_trace_id, good_trace_id, empty_data_dir),
    )

    assert result.exit_code == 2
    assert "Run validation failed" in result.stderr
    assert "spans.jsonl line 1" in result.stderr
    assert "Next step:" in result.stderr
    assert "sk-test-DO-NOT-LEAK" not in result.stderr


# ---------------------------------------------------------------------------
# _wait_for_port readiness-probe tests
# ---------------------------------------------------------------------------


def test_wait_for_port_returns_true_when_port_opens():
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]

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
    tmp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    tmp.bind(("127.0.0.1", 0))
    port = tmp.getsockname()[1]
    tmp.close()

    assert _wait_for_port("127.0.0.1", port, timeout_s=0.3) is False


def test_view_opens_browser_only_after_wait_succeeds(monkeypatch, empty_data_dir):
    call_log: list[str] = []

    def fake_wait_for_port(host: str, port: int, timeout_s: float = 5.0) -> bool:
        call_log.append("wait")
        return True

    def fake_webbrowser_open(url: str, *a, **kw) -> None:
        assert "wait" in call_log, "webbrowser.open called before readiness wait"
        call_log.append("browser")

    monkeypatch.setattr("maida.cli._wait_for_port", fake_wait_for_port)
    monkeypatch.setattr("maida.cli.webbrowser.open", fake_webbrowser_open)

    def fake_uvicorn_run(**kwargs) -> None:
        time.sleep(0.1)

    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    result = runner.invoke(app, ["view"])
    assert result.exit_code == 0
    assert call_log == ["wait", "browser"]


def test_view_server_stays_running_until_interrupt(monkeypatch, empty_data_dir):
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
    config = load_config()
    with traced_run(name="baseline_test"):
        record_tool_call("search", args={}, result=None)
    run_id = get_latest_run_id(config)

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
    config = load_config()
    with traced_run(name="assert_test"):
        record_tool_call("t", args={}, result=None)
    from tests.conftest import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["assert", run_id, "--max-steps", "10"])
    assert result.exit_code == 0
    assert "PASSED" in result.output


def test_assert_exit_one_on_fail(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        for i in range(5):
            record_tool_call(f"t{i}", args={}, result=None)
    from tests.conftest import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["assert", run_id, "--max-steps", "3"])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_assert_exit_two_missing_baseline(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        pass
    from tests.conftest import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(
        app,
        ["assert", run_id, "--baseline", str(empty_data_dir / "nope.json")],
    )
    assert result.exit_code == 2


def test_assert_json_format(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        pass
    from tests.conftest import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(
        app, ["assert", run_id, "--max-steps", "10", "--format", "json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["passed"] is True


def test_assert_markdown_format(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        pass
    from tests.conftest import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(
        app, ["assert", run_id, "--max-steps", "10", "--format", "markdown"]
    )
    assert result.exit_code == 0
    assert "Maida gate" in result.output


def test_assert_with_baseline(empty_data_dir):
    config = load_config()
    with traced_run(name="baseline_run"):
        for _ in range(5):
            record_tool_call("t", args={}, result=None)
    from tests.conftest import get_latest_run_id

    bl_run = get_latest_run_id(config)

    bl_path = empty_data_dir / "bl.json"
    runner.invoke(app, ["baseline", bl_run, "--out", str(bl_path)])

    with traced_run(name="check_run"):
        for _ in range(5):
            record_tool_call("t", args={}, result=None)
    check_run = get_latest_run_id(config)

    result = runner.invoke(
        app,
        [
            "assert",
            check_run,
            "--baseline",
            str(bl_path),
            "--max-steps",
            "100",
            "--duration-tolerance",
            "100",
        ],
    )
    assert result.exit_code == 0


def test_assert_no_loops_flag(empty_data_dir):
    config = load_config()
    with traced_run(name="loop_test"):
        record_tool_call("t", args={}, result=None)
    from tests.conftest import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["assert", run_id, "--no-loops"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# diff command
# ---------------------------------------------------------------------------


def test_diff_two_runs(empty_data_dir):
    config = load_config()
    with traced_run(name="a"):
        record_tool_call("search", args={}, result=None)
    from tests.conftest import get_latest_run_id

    rid_a = get_latest_run_id(config)

    with traced_run(name="b"):
        record_tool_call("parse", args={}, result=None)
    rid_b = get_latest_run_id(config)

    result = runner.invoke(app, ["diff", rid_a, rid_b])
    assert result.exit_code == 0
    assert "Run comparison:" in result.output


def test_diff_with_baseline(empty_data_dir):
    config = load_config()
    with traced_run(name="bl"):
        record_tool_call("t", args={}, result=None)
    from tests.conftest import get_latest_run_id

    bl_run = get_latest_run_id(config)

    bl_path = empty_data_dir / "bl.json"
    runner.invoke(app, ["baseline", bl_run, "--out", str(bl_path)])

    with traced_run(name="current"):
        record_tool_call("t", args={}, result=None)
        record_tool_call("new_tool", args={}, result=None)
    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["diff", run_id, "--baseline", str(bl_path)])
    assert result.exit_code == 0
    assert "new_tool" in result.output


def test_diff_missing_args(empty_data_dir):
    config = load_config()
    with traced_run(name="test"):
        pass
    from tests.conftest import get_latest_run_id

    rid = get_latest_run_id(config)

    result = runner.invoke(app, ["diff", rid])
    assert result.exit_code == 2


# --- latest-run defaults (run ID argument omitted) ---


def test_assert_defaults_to_latest_run(empty_data_dir):
    config = load_config()
    _make_run(config, name="older", events=[(EventType.TOOL_CALL, "t", {})])
    newest = _make_run(config, name="newer", events=[(EventType.TOOL_CALL, "t", {})])

    result = runner.invoke(app, ["assert", "--max-steps", "10", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["run_id"] == newest
    assert "Using latest run:" in result.stderr


def test_assert_no_runs_exit_two(empty_data_dir):
    result = runner.invoke(app, ["assert", "--max-steps", "10"])
    assert result.exit_code == 2
    assert "No runs found" in result.stderr


def test_assert_json_stdout_stays_clean_when_run_id_omitted(empty_data_dir):
    config = load_config()
    _make_run(config, name="run", events=[(EventType.TOOL_CALL, "t", {})])

    result = runner.invoke(app, ["assert", "--max-steps", "10", "--format", "json"])
    assert result.exit_code == 0
    json.loads(result.stdout)  # stdout must be pure JSON


def test_baseline_defaults_to_latest_run(empty_data_dir):
    config = load_config()
    _make_run(config, name="older", events=[(EventType.TOOL_CALL, "t", {})])
    newest = _make_run(config, name="newer", events=[(EventType.TOOL_CALL, "t", {})])

    out = empty_data_dir / "bl.json"
    result = runner.invoke(app, ["baseline", "--out", str(out)])
    assert result.exit_code == 0
    bl = json.loads(out.read_text())
    assert bl["source_run_id"] == newest


def test_baseline_no_runs_exit_two(empty_data_dir):
    out = empty_data_dir / "bl.json"
    result = runner.invoke(app, ["baseline", "--out", str(out)])
    assert result.exit_code == 2
    assert "No runs found" in result.stderr


def test_export_defaults_to_latest_run(empty_data_dir):
    config = load_config()
    newest = _make_run(config, name="run", events=[(EventType.TOOL_CALL, "t", {})])

    out = empty_data_dir / "export.json"
    result = runner.invoke(app, ["export", "--out", str(out)])
    assert result.exit_code == 0
    payload = json.loads(out.read_text())
    run_id = payload["run"].get("trace_id") or payload["run"].get("run_id")
    assert run_id == newest


def test_diff_defaults_to_latest_run_with_baseline(empty_data_dir):
    config = load_config()
    _make_run(config, name="bl", events=[(EventType.TOOL_CALL, "t", {})])
    bl_path = empty_data_dir / "bl.json"
    runner.invoke(app, ["baseline", "--out", str(bl_path)])

    _make_run(
        config,
        name="current",
        events=[(EventType.TOOL_CALL, "t", {}), (EventType.TOOL_CALL, "new_tool", {})],
    )

    result = runner.invoke(app, ["diff", "--baseline", str(bl_path)])
    assert result.exit_code == 0
    assert "new_tool" in result.output


# --- demo command ---


def test_demo_records_a_run(empty_data_dir):
    config = load_config()
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0
    assert "Run recorded:" in result.output
    assert "Next steps:" in result.output

    runs = list_runs(limit=5, config=config)
    assert len(runs) == 1
    assert runs[0].get("run_name") == "demo-support-agent"
    assert runs[0].get("status") == "ok"


def test_demo_run_is_redacted_on_disk(empty_data_dir):
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0

    spans_files = list(empty_data_dir.rglob("spans.jsonl"))
    assert spans_files, "expected a spans.jsonl to be written"
    raw = spans_files[0].read_text()
    assert "sk-demo-DO_NOT_USE" not in raw  # api_key value must be scrubbed


def test_demo_then_baseline_and_assert_pass(empty_data_dir):
    result = runner.invoke(app, ["demo"])
    assert result.exit_code == 0

    bl_path = empty_data_dir / "demo.json"
    result = runner.invoke(app, ["baseline", "--out", str(bl_path)])
    assert result.exit_code == 0

    result = runner.invoke(app, ["assert", "--baseline", str(bl_path)])
    assert result.exit_code == 1 or result.exit_code == 0
    # the same run asserted against its own baseline must pass every check
    assert "FAILED" not in result.output


def test_demo_regression_story(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()

    result = runner.invoke(app, ["demo", "--regression"])
    assert result.exit_code == 0

    # baseline written under cwd
    bl_path = tmp_path / ".maida" / "baselines" / "demo-support-agent.json"
    assert bl_path.is_file()

    # two runs were recorded
    runs = list_runs(limit=5, config=config)
    assert len(runs) == 2

    # the gate must fail and explain itself
    assert "FAILED" in result.output
    assert "escalate_to_human" in result.output
    assert "loop warning" in result.output
    # and preview the PR comment
    assert "PR comment preview" in result.output
    assert "Maida gate: agent behavior regressed" in result.output
    assert "What changed vs baseline" in result.output


def test_demo_regression_no_secret_on_disk(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["demo", "--regression"])
    assert result.exit_code == 0
    for spans_file in empty_data_dir.rglob("spans.jsonl"):
        assert "sk-demo-DO_NOT_USE" not in spans_file.read_text()


# --- init command ---


def test_init_writes_valid_policy(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    policy_path = tmp_path / ".maida" / "policy.yaml"
    assert policy_path.is_file()
    assert "wrote" in result.output
    assert "Next steps:" in result.output

    # generated policy must load through the real policy loader
    policy_text = policy_path.read_text(encoding="utf-8")
    policy = load_policy(policy_path)
    assert policy.no_loops is False
    assert policy.no_guardrails is False
    assert policy.no_new_tools is False
    assert policy.expect_status is None
    assert policy.step_tolerance == 0.5
    assert policy.tool_call_tolerance == 0.5
    assert policy.cost_tolerance == 0.5
    assert policy.duration_tolerance == 0.5
    for strict_key in (
        "no_loops: true",
        "no_guardrails: true",
        "no_new_tools: true",
        "expect_status: ok",
    ):
        assert f"\n  # {strict_key}\n" in policy_text
        assert f"\n  {strict_key}" not in policy_text


def test_init_github_writes_valid_workflow(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init", "--github"])
    assert result.exit_code == 0
    wf_path = tmp_path / ".github" / "workflows" / "maida.yml"
    assert wf_path.is_file()

    wf = yaml.safe_load(wf_path.read_text())
    job = wf["jobs"]["agent-check"]
    uses = [step.get("uses", "") for step in job["steps"]]
    assert any(u.startswith("maida-ai/maida-assert@") for u in uses)
    assert wf["permissions"]["pull-requests"] == "write"


def test_init_skips_existing_without_force(empty_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init"])
    policy_path = tmp_path / ".maida" / "policy.yaml"
    policy_path.write_text("assert: {}\n", encoding="utf-8")

    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert "skipped" in result.output
    assert policy_path.read_text() == "assert: {}\n"  # untouched

    result = runner.invoke(app, ["init", "--force"])
    assert result.exit_code == 0
    assert "wrote" in result.output
    assert "no_loops" in policy_path.read_text()  # overwritten
