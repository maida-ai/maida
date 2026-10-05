"""Pinned smoke preflight, local model transport, and native command serialization."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sysconfig
import threading
import time
import uuid
import venv
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from maida.capture_setup import bound_hook_command, hook_arguments, validate_hook_command
from maida.codex_setup import EVENTS, merged_hooks
from maida.config import load_config

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codex_capture_smoke", ROOT / "scripts/codex_capture_smoke.py")
assert SPEC and SPEC.loader
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def test_smoke_help_describes_interactive_task_exit_and_viewer_review(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["codex_capture_smoke.py", "--help"])
    with pytest.raises(SystemExit) as result:
        smoke.main()
    assert result.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--prepare-directory" in help_text
    assert "--interactive-directory" in help_text
    assert "native trust" in help_text
    assert "After exiting Codex" in help_text
    assert "--view-directory" in help_text
    assert "then run --directory" not in help_text


def test_smoke_isolates_production_credentials_and_python_paths(tmp_path, monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "PYTHONPATH",
        "PYTHONHOME",
        "CODEX_HOME",
        "CODEX_ESCALATE_SOCKET",
        "MAIDA_DATA_DIR",
        "TMPDIR",
        "XDG_RUNTIME_DIR",
    ):
        monkeypatch.setenv(key, "private-value")
    environment = smoke.isolated_environment(tmp_path)
    assert "private-value" not in environment.values()
    assert "OPENAI_API_KEY" not in environment
    assert "PYTHONPATH" not in environment
    assert "CODEX_ESCALATE_SOCKET" not in environment
    assert environment["HOME"] == str(tmp_path / "home")
    assert environment["CODEX_HOME"] == str(tmp_path / "home/.codex")
    assert environment["MAIDA_DATA_DIR"] == str(tmp_path / "data")
    assert environment["TMPDIR"] == str(tmp_path / "tmp")
    if os.name == "posix":
        assert (tmp_path / "runtime").stat().st_mode & 0o777 == 0o700


@pytest.mark.parametrize("status", ["untrusted", "modified", "managed"])
def test_smoke_requires_native_review_without_trust_shortcuts(status):
    hook = {"command": "python -m maida.cli capture codex-hook", "enabled": True, "trustStatus": status}
    with pytest.raises(RuntimeError, match="/hooks"):
        smoke.verify_trust({"data": [{"hooks": [hook]}]})
    hook["trustStatus"] = "trusted"
    smoke.verify_trust({"data": [{"hooks": [hook]}]})
    hook["enabled"] = False
    with pytest.raises(RuntimeError, match="review"):
        smoke.verify_trust({"data": [{"hooks": [hook]}]})


@pytest.mark.parametrize("result", [{"data": []}, {"data": [{"hooks": [], "errors": [{"message": "bad config"}]}]}])
def test_smoke_missing_or_invalid_hooks_are_not_success(result):
    with pytest.raises(RuntimeError, match="unavailable"):
        smoke.verify_trust(result)


def test_smoke_refuses_an_unpinned_runtime(monkeypatch):
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda *_args, **_kwargs: subprocess.CompletedProcess([], 0, "codex-cli 0.1.0")
    )
    with pytest.raises(RuntimeError, match=smoke.PINNED_CODEX_VERSION):
        smoke.require_version("codex")


def test_smoke_never_overwrites_a_prepared_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "require_version", lambda _codex: None)
    sentinel = tmp_path / "saved"
    sentinel.write_text("keep")
    with pytest.raises(RuntimeError, match="new directory"):
        smoke.prepare(tmp_path, "codex")
    assert sentinel.read_text() == "keep"


def test_canned_endpoint_drives_tool_then_completion_and_rejects_bad_request():
    smoke.CannedResponsesHandler.request_count = 0
    smoke.CannedResponsesHandler.failure = None
    server = ThreadingHTTPServer(("127.0.0.1", 0), smoke.CannedResponsesHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_address[1]}/v1/responses"
        request = {"model": smoke.MODEL, "stream": True, "tools": [{"type": "function", "name": "exec_command"}]}

        def post(body):
            with urlopen(
                Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}), timeout=5
            ) as response:
                assert response.headers["Content-Type"] == "text/event-stream"
                return [
                    json.loads(line.removeprefix("data: "))
                    for line in response.read().decode().splitlines()
                    if line.startswith("data: ")
                ]

        first = post(request)
        call = first[-1]["response"]["output"][0]
        assert call["type"] == "function_call"
        assert call["name"] == "exec_command"
        assert json.loads(call["arguments"])["cmd"] == "cat README.md"
        assert first[-1]["type"] == "response.completed"
        second = post(request)
        assert second[-1]["response"]["output"][0]["content"][0]["text"] == "Smoke complete."
        assert smoke.CannedResponsesHandler.request_count == 2
        with pytest.raises(HTTPError) as invalid:
            post({"model": "unapproved-model", "stream": True})
        assert invalid.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_canned_model_cannot_invent_an_unsupported_tool():
    with pytest.raises(RuntimeError, match="expected native shell tool"):
        smoke.response_events({"tools": [{"name": "unsupported_tool"}]}, 1)


def test_real_pinned_app_server_lists_untrusted_hooks_and_smoke_refuses_them(tmp_path):
    codex = shutil.which("codex")
    if not codex:
        pytest.skip("Pinned native Codex is not installed; transport fixtures do not establish native compatibility")
    try:
        smoke.require_version(codex)
    except RuntimeError:
        pytest.skip(f"Native acceptance requires Codex {smoke.PINNED_CODEX_VERSION}")
    project = tmp_path / "project"
    home = tmp_path / "home/.codex"
    project.mkdir()
    home.mkdir(parents=True, mode=0o700)
    (home / "config.toml").write_text(
        "[features]\nhooks = true\n[model_providers.maida-smoke]\n"
        'name = "Smoke"\nwire_api = "responses"\nrequires_openai_auth = false\n'
    )
    (home / "hooks.json").write_text(json.dumps(merged_hooks({}, bound_hook_command("codex-hook"))[0]))
    server = smoke.AppServer(codex, tmp_path, 9)
    try:
        server.request(
            "initialize",
            {
                "clientInfo": {"name": "maida-trust-negative-test", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        server.send({"method": "initialized", "params": {}})
        result = server.request("hooks/list", {"cwds": [str(project)]})
        hooks = result["data"][0]["hooks"]
        assert len(hooks) == len(EVENTS)
        assert result["data"][0]["errors"] == []
        assert all(hook["trustStatus"] == "untrusted" for hook in hooks)
        with pytest.raises(RuntimeError, match="need review"):
            smoke.verify_trust(result)
        assert not (tmp_path / "data").exists()
    finally:
        server.close()


def test_real_pinned_codex_turn_uses_canned_loopback_model_without_credentials(tmp_path):
    """Validates the model fixture, without granting hook trust or claiming capture."""
    codex = shutil.which("codex")
    if not codex:
        pytest.skip("Pinned native Codex is not installed")
    try:
        smoke.require_version(codex)
    except RuntimeError:
        pytest.skip(f"Native acceptance requires Codex {smoke.PINNED_CODEX_VERSION}")
    project = tmp_path / "project"
    home = tmp_path / "home/.codex"
    project.mkdir()
    home.mkdir(parents=True, mode=0o700)
    (project / "README.md").write_text("local deterministic fixture\n")
    (home / "config.toml").write_text(
        f'model="{smoke.MODEL}"\nmodel_provider="maida-smoke"\n[model_providers.maida-smoke]\n'
        'name="Smoke"\nwire_api="responses"\nrequires_openai_auth=false\n[analytics]\nenabled=false\n'
    )
    smoke.CannedResponsesHandler.request_count = 0
    smoke.CannedResponsesHandler.failure = None
    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), smoke.CannedResponsesHandler)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    server = smoke.AppServer(codex, tmp_path, endpoint.server_address[1])
    try:
        server.request(
            "initialize",
            {
                "clientInfo": {"name": "maida-local-model-test", "version": "1"},
                "capabilities": {"experimentalApi": True},
            },
        )
        server.send({"method": "initialized", "params": {}})
        started = server.request(
            "thread/start",
            {
                "cwd": str(project),
                "model": smoke.MODEL,
                "modelProvider": "maida-smoke",
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "ephemeral": True,
            },
        )
        server.request(
            "turn/start",
            {
                "threadId": started["thread"]["id"],
                # This test runs inside the managed container's sandbox. The
                # installed app-server's nested bubblewrap rejects its socket
                # mounts here; use the documented externally isolated policy.
                "sandboxPolicy": {"type": "externalSandbox", "networkAccess": "restricted"},
                "input": [
                    {
                        "type": "text",
                        "text": "Read README.md once with the shell tool, then finish.",
                        "text_elements": [],
                    }
                ],
            },
        )
        tools = []
        deadline = time.monotonic() + 30
        while True:
            event = server.receive(deadline)
            if event.get("method") == "item/completed" and event["params"]["item"]["type"] == "commandExecution":
                tools.append(event["params"]["item"])
            if event.get("method") == "turn/completed":
                assert event["params"]["turn"]["status"] == "completed"
                break
        assert len(tools) == 1
        assert tools[0]["exitCode"] == 0, tools[0].get("aggregatedOutput")
        assert smoke.CannedResponsesHandler.request_count == 2
        assert smoke.CannedResponsesHandler.failure is None
        assert not (tmp_path / "data").exists()
    finally:
        server.close()
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=5)


def test_bound_codex_hook_executes_native_shell_from_sensitive_environment_path(tmp_path, temp_data_dir, monkeypatch):
    """Runs on Linux/macOS sh and Windows PowerShell, with actual Python stdin."""
    # Python refuses venv paths containing the platform's PATH separator.
    # Retain the remaining shell-sensitive characters for the actual hook test.
    environment = tmp_path / "environment ' & $; ` with spaces".replace(os.pathsep, "")
    venv.EnvBuilder(with_pip=False, system_site_packages=True).create(environment)
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    location = subprocess.run(
        [str(executable), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    Path(location.stdout.strip()).joinpath("maida-smoke-dependencies.pth").write_text(
        str(ROOT) + "\n" + sysconfig.get_path("purelib") + "\n"
    )
    project = tmp_path / "project with spaces"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    (project / ".maida").mkdir()
    (project / ".maida/local.json").write_text(
        json.dumps({"version": 2, "project_id": uuid.uuid4().hex, "providers": {"codex": {"enabled": True}}})
    )
    monkeypatch.chdir(project)
    monkeypatch.setattr("maida.capture_setup.sys.executable", str(executable))
    command = bound_hook_command(receiver="codex-hook")
    assert hook_arguments(command)[0] == str(executable)
    validate_hook_command(command)
    if os.name == "nt":
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        assert powershell, "Windows compatibility must run a real native PowerShell"
        shell = [powershell, "-NoProfile", "-NonInteractive", "-Command", command]
    else:
        shell = ["/bin/sh", "-c", command]
    payload = {
        "session_id": "platform-session",
        "turn_id": "platform-turn",
        "cwd": str(project),
        "hook_event_name": "UserPromptSubmit",
        "prompt": "bounded fixture",
    }
    result = subprocess.run(shell, input=json.dumps(payload), text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout == result.stderr == ""
    receipts = list((load_config(capture=True).data_dir / "onboarding").glob("codex-*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["state"] == "active"
    events_path = next((load_config(capture=True).data_dir / "captures/codex").rglob("events.jsonl"))
    before = events_path.read_bytes()
    # An already-open native session retains its installed command. It must be
    # inert after detach without deleting evidence or changing that command.
    pointer_path = project / ".maida/local.json"
    pointer = json.loads(pointer_path.read_text())
    pointer["providers"]["codex"]["enabled"] = False
    pointer_path.write_text(json.dumps(pointer))
    payload["turn_id"] = "after-detach"
    detached = subprocess.run(shell, input=json.dumps(payload), text=True, capture_output=True, timeout=15)
    assert detached.returncode == 0
    assert detached.stdout == detached.stderr == ""
    assert events_path.read_bytes() == before
    assert list((load_config(capture=True).data_dir / "onboarding").glob("codex-*.json")) == receipts


def test_smoke_shutdown_precedes_check_and_viewer(monkeypatch, tmp_path):
    directory = tmp_path
    (directory / "project/.maida").mkdir(parents=True)
    (directory / "project/.maida/local.json").write_text("{}")
    lifecycle = []
    monkeypatch.setattr(smoke, "require_version", lambda _codex: None)
    monkeypatch.setattr(smoke, "verify_trust", lambda result: None)

    class Server:
        def __init__(self, *args):
            pass

        def request(self, method, params):
            if method == "thread/start":
                return {"thread": {"id": "fixture-thread"}}
            return {}

        def send(self, message):
            pass

        def receive(self, deadline):
            smoke.CannedResponsesHandler.request_count = 2
            if not lifecycle:
                lifecycle.append("tool")
                return {"method": "item/completed", "params": {"item": {"type": "commandExecution", "exitCode": 0}}}
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

        def close(self):
            lifecycle.append("exit")

    monkeypatch.setattr(smoke, "AppServer", Server)

    def check_and_view(directory):
        assert lifecycle[-1] == "exit"
        lifecycle.append("check and view")
        return {"report": {"run_id": "a" * 32}, "viewer": {"trace_id": "a" * 32}}

    monkeypatch.setattr(smoke, "check_and_verify_viewer", check_and_view, raising=False)
    result = smoke.run_smoke("codex", directory)
    assert result["session_open_at_check"] is False
    assert lifecycle[:3] == ["tool", "exit", "check and view"]


def test_interactive_journey_uses_real_terminal_and_checks_only_after_exit(monkeypatch, tmp_path):
    (tmp_path / "project/.maida").mkdir(parents=True)
    (tmp_path / "project/.maida/local.json").write_text("{}")
    monkeypatch.setattr(smoke, "require_version", lambda _codex: None)
    timeline = []

    def terminal(argv, **kwargs):
        assert argv[0] == "codex"
        assert "app-server" not in argv
        assert kwargs.get("capture_output", False) is False
        assert kwargs["cwd"] == tmp_path / "project"
        timeline.append("interactive exit")
        smoke.CannedResponsesHandler.request_count = 2
        return subprocess.CompletedProcess(argv, 0)

    def check_and_view(directory):
        assert timeline == ["interactive exit"]
        return {"report": {"run_id": "b" * 32}, "viewer": {"trace_id": "b" * 32}}

    monkeypatch.setattr(smoke.subprocess, "run", terminal)
    monkeypatch.setattr(smoke, "check_and_verify_viewer", check_and_view, raising=False)
    result = smoke.run_interactive_smoke("codex", tmp_path)
    assert result["interactive_journey"] is True
    assert result["session_open_at_check"] is False


def test_real_printed_viewer_serves_fixture_task_after_exited_hook_lifecycle(tmp_path):
    import sys

    directory = tmp_path / "native fixture with spaces"
    project = directory / "project"
    project.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    (project / ".maida").mkdir()
    (project / ".maida/local.json").write_text(
        json.dumps({"version": 2, "project_id": uuid.uuid4().hex, "providers": {"codex": {"enabled": True}}})
    )
    environment = smoke.isolated_environment(directory)
    for event, extra in [
        ("SessionStart", {}),
        ("UserPromptSubmit", {"prompt": smoke.PROMPT}),
        ("PreToolUse", {"tool_name": "Bash", "tool_use_id": "read", "tool_input": {"command": "cat README.md"}}),
        (
            "PostToolUse",
            {
                "tool_name": "Bash",
                "tool_use_id": "read",
                "tool_input": {},
                "tool_response": {"exit_code": 0, "output": "fixture"},
            },
        ),
        ("Stop", {"last_assistant_message": "Smoke complete."}),
        ("SessionEnd", {}),
    ]:
        payload = {
            "session_id": "actual-hook-subprocess",
            "turn_id": "turn",
            "cwd": str(project),
            "hook_event_name": event,
            **extra,
        }
        result = subprocess.run(
            [sys.executable, "-E", "-P", "-m", "maida.cli", "capture", "codex-hook"],
            input=json.dumps(payload),
            cwd=project,
            env=environment,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, result.stderr
    selected = smoke.check_and_verify_viewer(directory)
    assert selected["report"]["run_id"] == selected["viewer"]["trace_id"]
    assert selected["viewer"]["command"] == "maida view " + selected["report"]["run_id"]
    assert selected["visual_review"].startswith("pending")


def test_visual_review_runs_exact_printed_command_in_fixture_environment(tmp_path, monkeypatch):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "check" in argv:
            return subprocess.CompletedProcess(argv, 0, '{"run_id":"' + "c" * 32 + '"}', "View: maida view " + "c" * 32)
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(smoke.subprocess, "run", run)
    result = smoke.show_viewer(tmp_path)
    assert result["trace_id"] == "c" * 32
    assert calls[1][0] == ["maida", "view", "c" * 32]
    assert calls[1][1]["cwd"] == tmp_path / "project"
    assert calls[1][1]["env"]["MAIDA_DATA_DIR"] == str(tmp_path / "data")
    assert "capture_output" not in calls[1][1]
