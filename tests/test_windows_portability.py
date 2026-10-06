"""Exercise Windows-sensitive behavior even on a POSIX test runner."""

import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from maida import cli, capture_setup
from maida._cli import capture as capture_cli
from maida._scenario import executor as scenario


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_cli_entrypoint_handles_unrepresentable_output(monkeypatch, encoding):
    stdout = io.BytesIO()
    stderr = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(stdout, encoding=encoding))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(stderr, encoding=encoding))

    def app():
        cli.typer.echo("PASS → ✓")
        cli.typer.echo("FAIL → ✗", err=True)

    monkeypatch.setattr(cli, "app", app)
    cli.main()
    sys.stdout.flush()
    sys.stderr.flush()
    assert b"PASS" in stdout.getvalue()
    assert b"FAIL" in stderr.getvalue()
    assert sys.stdout.encoding == sys.stderr.encoding == encoding


def test_demo_runs_with_strict_ascii_redirected_streams(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "maida.cli", "demo", "--regression"],
        cwd=tmp_path,
        env={**os.environ, "MAIDA_DATA_DIR": str(tmp_path / "data"), "PYTHONIOENCODING": "ascii:strict"},
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr.decode("ascii")
    assert b"FAIL" in result.stdout


@pytest.mark.parametrize("encoding", ["ascii", "cp1252", "utf-8"])
def test_cli_json_keeps_unicode_values_on_legacy_streams(monkeypatch, encoding):
    output = io.BytesIO()
    stream = io.TextIOWrapper(output, encoding=encoding)
    monkeypatch.setattr(sys, "stdout", stream)
    payload = {"run_name": "task 😀 → café"}
    monkeypatch.setattr(cli, "app", lambda: print(json.dumps(payload, ensure_ascii=False)))
    cli.main()
    stream.flush()
    assert json.loads(output.getvalue().decode(encoding)) == payload


@pytest.mark.parametrize("invalid", [False, True])
def test_capture_hook_reads_utf8_json_from_legacy_stdin(monkeypatch, invalid):
    payload = {"session_id": "fixture", "cwd": "C:/Users/café/😀"}
    data = b"\xff" if invalid else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(data), encoding="cp1252"))
    monkeypatch.setattr(capture_cli, "installation", lambda _: None)
    received = []
    monkeypatch.setattr(capture_cli, "parse_claude_hook_json", lambda raw, _: received.append(json.loads(raw)))
    if invalid:
        with pytest.raises(cli.Exit) as caught:
            cli.capture_claude_hook_cmd()
        assert caught.value.exit_code == cli.EXIT_INTERNAL
        assert not received
    else:
        cli.capture_claude_hook_cmd()
        assert received == [payload]


def test_scenario_preserves_windows_runtime_environment_without_extra_secrets():
    runtime = {
        "SystemRoot": r"C:\Windows",
        "USERPROFILE": r"C:\Users\fixture",
        "APPDATA": r"C:\Users\fixture\AppData\Roaming",
        "LOCALAPPDATA": r"C:\Users\fixture\AppData\Local",
        "TEMP": r"C:\Users\fixture\AppData\Local\Temp",
        "TMP": r"C:\Users\fixture\AppData\Local\Temp",
    }
    filtered = scenario._filtered_environment("http://127.0.0.1:1234", {**runtime, "UNRELATED_SECRET": "private"})
    assert runtime.items() <= filtered.items()
    assert "UNRELATED_SECRET" not in filtered


def test_windows_bound_hook_quotes_interpreter_for_powershell(monkeypatch):
    executable = r"C:\Users\O'Brien\Python with spaces\python.exe"
    monkeypatch.setattr(capture_setup, "_WINDOWS", True)
    monkeypatch.setattr(capture_setup.sys, "executable", executable)
    command = capture_setup.bound_hook_command()
    assert command == "& 'C:\\Users\\O''Brien\\Python with spaces\\python.exe' -E -P -m maida.cli capture claude-hook"
    assert capture_setup.is_maida_hook_command(command)
    settings, _ = capture_setup.merged_settings({}, observer_command=command)
    hook = settings["hooks"]["SessionStart"][0]["hooks"][0]
    assert hook["shell"] == "powershell"
    assert hook["command"] == command
    assert capture_setup.merged_settings(settings, observer_command=command)[1] == []
    assert capture_setup.removed_settings(settings) == ({}, len(capture_setup.EVENTS))

    def probe(argv, **kwargs):
        assert argv == [executable, "-E", "-P", "-m", "maida.cli", "capture", "claude-hook", "--help"]
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(capture_setup.subprocess, "run", probe)
    capture_setup.validate_hook_command(command)
    for suffix in (" --extra", "; echo keep", " && echo keep"):
        assert not capture_setup.is_maida_hook_command(command + suffix)


@pytest.mark.skipif(os.name != "nt", reason="Requires native PowerShell")
def test_windows_hook_runs_through_powershell(tmp_path):
    command = capture_setup.bound_hook_command()
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command + " --help"],
        cwd=tmp_path,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert b"Usage" in result.stdout


@pytest.mark.parametrize("failure", [False, True])
def test_windows_timeout_stops_tree_and_reports_cleanup_failure(monkeypatch, failure):
    monkeypatch.setattr(scenario, "os", SimpleNamespace(name="nt"))
    killed = []
    process = SimpleNamespace(pid=123, poll=lambda: None, kill=lambda: killed.append(True))

    def taskkill(argv, **kwargs):
        assert argv == ["taskkill", "/PID", "123", "/T", "/F"]
        assert kwargs["timeout"] == 10
        return subprocess.CompletedProcess(argv, int(failure))

    monkeypatch.setattr(scenario.subprocess, "run", taskkill)
    if failure:
        with pytest.raises(OSError, match="process tree"):
            scenario._stop_process_group(process, force=True)
    else:
        scenario._stop_process_group(process, force=False)
    assert killed == [True]


@pytest.mark.skipif(os.name != "nt", reason="Requires native Windows process trees")
def test_windows_scenario_timeout_stops_children_holding_capture_pipes(tmp_path):
    started = time.monotonic()
    outcome = scenario._run_claude_process(
        [
            sys.executable,
            "-c",
            "import subprocess, sys, time; "
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(20)']); "
            "time.sleep(20)",
        ],
        cwd=tmp_path,
        env=os.environ,
        timeout_seconds=1,
    )
    assert outcome.timed_out
    assert outcome.returncode != 0
    assert time.monotonic() - started < 10
