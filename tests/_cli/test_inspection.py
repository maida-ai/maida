"""CLI inspection behavior."""

import json
import socket
import threading
import time
import pytest
from typer.testing import CliRunner
from maida import record_tool_call, traced_run
from maida.cli import _wait_for_port, app
from maida.config import load_config
from maida.events import EventType
from tests.support.runs import get_latest_run_id
from tests.support.cli import _make_run, _write_run, _write_run_with_malformed_span, _write_trace_run


runner = CliRunner()


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
    tool_events = [e for e in data["events"] if e.get("event_type") == EventType.TOOL_CALL.value]
    assert len(tool_events) == 1
    assert tool_events[0].get("payload", {}).get("tool_name") == "test_tool"


def test_list_json_outputs_valid_json_spec_version_and_runs(empty_data_dir):
    """maida list --json outputs valid JSON with keys spec_version and runs."""
    result = runner.invoke(app, ["list", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "spec_version" in data
    assert "runs" in data
    assert data["spec_version"] == "0.2.0"
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

    monkeypatch.setattr("maida._cli.inspection._wait_for_port", fake_wait_for_port)
    monkeypatch.setattr("maida._cli.inspection.webbrowser.open", fake_webbrowser_open)

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

    monkeypatch.setattr("maida._cli.inspection._wait_for_port", lambda *a, **kw: True)
    monkeypatch.setattr("maida._cli.inspection.webbrowser.open", lambda *a, **kw: None)
    monkeypatch.setattr("uvicorn.run", fake_uvicorn_run)

    view_result = {"done": False, "exit_code": None}

    def run_view():
        r = runner.invoke(app, ["view", "--no-browser", "--port", "9199"])
        view_result["done"] = True
        view_result["exit_code"] = r.exit_code

    view_thread = threading.Thread(target=run_view)
    view_thread.start()
    time.sleep(0.4)
    assert view_thread.is_alive(), "view should still be running (blocked on server join)"
    block_event.set()
    view_thread.join(timeout=5)
    assert view_result["done"]
    assert view_result["exit_code"] == 0


def test_diff_two_runs(empty_data_dir):
    config = load_config()
    with traced_run(name="a"):
        record_tool_call("search", args={}, result=None)
    from tests.support.runs import get_latest_run_id

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
    from tests.support.runs import get_latest_run_id

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
    from tests.support.runs import get_latest_run_id

    rid = get_latest_run_id(config)

    result = runner.invoke(app, ["diff", rid])
    assert result.exit_code == 2


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
