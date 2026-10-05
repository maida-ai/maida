#!/usr/bin/env python3
"""Real native-hook smoke with a canned localhost model and user-reviewed trust.

Prepare once from an interactive terminal, review native trust using the printed
command, exit that session, then run --directory. No trust database is edited.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PINNED_CODEX_VERSION = "0.160.0"
MODEL = "gpt-5.4"


def isolated_environment(directory: Path) -> dict[str, str]:
    """Never forward credentials, production config, or Python import overrides."""
    temporary = directory / "tmp"
    runtime = directory / "runtime"
    for path in (temporary, runtime, directory / "home/.codex/tmp", directory / "home/.codex/.tmp"):
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == "posix":
            path.chmod(0o700)
    environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SystemRoot") if key in os.environ}
    environment.update(
        {
            "HOME": str(directory / "home"),
            "USERPROFILE": str(directory / "home"),
            "CODEX_HOME": str(directory / "home/.codex"),
            "MAIDA_DATA_DIR": str(directory / "data"),
            "TMPDIR": str(temporary),
            "TEMP": str(temporary),
            "TMP": str(temporary),
            "XDG_RUNTIME_DIR": str(runtime),
        }
    )
    return environment


def require_version(codex: str) -> None:
    result = subprocess.run([codex, "--version"], capture_output=True, text=True, check=True, timeout=10)
    if result.stdout.strip() != f"codex-cli {PINNED_CODEX_VERSION}":
        raise RuntimeError(f"Codex CLI {PINNED_CODEX_VERSION} is required; this smoke never installs or upgrades it")


def prepare(directory: Path, codex: str) -> dict[str, Any]:
    """Create only a fresh fixture, using actual interactive Maida setup."""
    require_version(codex)
    if directory.exists():
        raise RuntimeError("Preparation requires a new directory; existing data is never replaced")
    project = directory / "project"
    home = directory / "home/.codex"
    project.mkdir(parents=True)
    home.mkdir(parents=True, mode=0o700)
    (project / "README.md").write_text("# Deterministic local capture smoke\n", encoding="utf-8")
    (home / "config.toml").write_text(
        f'model = "{MODEL}"\nmodel_provider = "maida-smoke"\n'
        '[model_providers.maida-smoke]\nname = "Local smoke fixture"\n'
        'base_url = "http://127.0.0.1:9/v1"\nwire_api = "responses"\nrequires_openai_auth = false\n'
        "[analytics]\nenabled = false\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "--quiet", str(project)], check=True, timeout=10)
    result = subprocess.run(
        [sys.executable, "-E", "-P", "-m", "maida.cli", "init", "--agent", "codex"],
        cwd=project,
        env=isolated_environment(directory),
        check=False,
    )
    if result.returncode or not (project / ".codex/hooks.json").is_file():
        raise RuntimeError("Approve the Maida setup preview in an interactive terminal before reviewing native hooks")
    return {
        "status": "awaiting_native_trust",
        "codex_version": PINNED_CODEX_VERSION,
        "directory": str(directory),
        "review_argv": [codex, "-C", str(project)],
        "review_environment": isolated_environment(directory),
        "next_action": "Use the isolated HOME and CODEX_HOME, review project trust and /hooks, then exit without a task. Run --directory next.",
    }


def response_events(request: dict[str, Any], number: int) -> list[dict[str, Any]]:
    """Respond to a native read tool once, then terminate the real model turn."""
    response_id = f"resp_maida_smoke_{number}"
    base = {"id": response_id, "object": "response", "status": "in_progress", "model": MODEL, "output": []}
    events = [{"type": "response.created", "response": base}]
    if number == 1:
        names = {tool.get("name") for tool in request.get("tools", []) if isinstance(tool, dict)}
        tool = next((name for name in ("exec_command", "shell_command", "shell") if name in names), None)
        if tool is None:
            raise RuntimeError("Pinned Codex did not expose an expected native shell tool")
        arguments: dict[str, Any] = {"cmd": "cat README.md", "yield_time_ms": 1000, "max_output_tokens": 1000}
        if tool == "shell_command":
            arguments = {"command": "cat README.md"}
        elif tool == "shell":
            arguments = {"command": ["sh", "-c", "cat README.md"]}
        item = {
            "id": "fc_maida_read",
            "type": "function_call",
            "call_id": "call_maida_read",
            "name": tool,
            "arguments": json.dumps(arguments),
            "status": "completed",
        }
    else:
        item = {
            "id": f"msg_maida_{number}",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "Smoke complete.", "annotations": []}],
        }
    events.extend(
        [
            {"type": "response.output_item.added", "output_index": 0, "item": item},
            {"type": "response.output_item.done", "output_index": 0, "item": item},
            {
                "type": "response.completed",
                "response": {
                    **base,
                    "status": "completed",
                    "output": [item],
                    "usage": {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20},
                },
            },
        ]
    )
    return events


class CannedResponsesHandler(BaseHTTPRequestHandler):
    """Only loopback requests; neither requests nor credentials are logged."""

    request_count = 0
    failure: str | None = None

    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib API
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 4 * 1024 * 1024:
                raise ValueError("request size")
            request = json.loads(self.rfile.read(length))
            if request.get("model") != MODEL or request.get("stream") is not True:
                raise ValueError("unexpected model request")
            type(self).request_count += 1
            events = response_events(request, type(self).request_count)
            body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
        except (ValueError, RuntimeError, AttributeError):
            type(self).failure = "Local endpoint received an incompatible pinned-runtime request"
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)


class AppServer:
    def __init__(self, codex: str, directory: Path, port: int) -> None:
        environment = isolated_environment(directory)
        self.process = subprocess.Popen(
            [
                codex,
                "app-server",
                "--stdio",
                "-c",
                f'model_providers.maida-smoke.base_url="http://127.0.0.1:{port}/v1"',
            ],
            cwd=directory / "project",
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self.input = self.process.stdin
        self.output = self.process.stdout
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.identifier = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        assert self.output
        for line in self.output:
            try:
                self.messages.put(json.loads(line))
            except ValueError:
                continue
        self.messages.put(None)

    def send(self, message: dict[str, Any]) -> None:
        assert self.input
        self.input.write(json.dumps(message) + "\n")
        self.input.flush()

    def receive(self, deadline: float) -> dict[str, Any]:
        try:
            message = self.messages.get(timeout=max(0, deadline - time.monotonic()))
        except queue.Empty:
            raise RuntimeError("Pinned app-server timed out") from None
        if message is None:
            raise RuntimeError("Pinned app-server exited before completing the smoke")
        if "method" in message and "id" in message:
            # A canned fixture should need no approval. Never auto-approve an
            # unexpected action or use its request contents in diagnostics.
            raise RuntimeError("App-server requested approval; inspect the fixture interactively")
        return message

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.identifier += 1
        self.send({"id": self.identifier, "method": method, "params": params})
        deadline = time.monotonic() + 30
        while True:
            response = self.receive(deadline)
            if response.get("id") == self.identifier:
                if "error" in response:
                    raise RuntimeError(f"Pinned app-server rejected {method}; inspect version/configuration")
                return response["result"]

    def close(self) -> None:
        if self.input:
            self.input.close()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=5)
        self.reader.join(timeout=1)


def verify_trust(result: dict[str, Any]) -> None:
    entries = result.get("data", [])
    hooks = [
        hook for entry in entries for hook in entry.get("hooks", []) if "codex-hook" in (hook.get("command") or "")
    ]
    if any(entry.get("errors") for entry in entries) or not hooks:
        raise RuntimeError("Native hooks are unavailable; review project trust and /hooks using the isolated test home")
    if any(not hook.get("enabled") or hook.get("trustStatus") != "trusted" for hook in hooks):
        raise RuntimeError("Native Maida hooks need review; use /hooks in the isolated test home, then rerun")


def run_smoke(codex: str, directory: Path, *, external_sandbox: bool = False) -> dict[str, Any]:
    require_version(codex)
    if not (directory / "project/.maida/local.json").is_file():
        raise RuntimeError("Prepare the isolated fixture and complete native trust review first")
    CannedResponsesHandler.request_count = 0
    CannedResponsesHandler.failure = None
    endpoint = ThreadingHTTPServer(("127.0.0.1", 0), CannedResponsesHandler)
    thread = threading.Thread(target=endpoint.serve_forever, daemon=True)
    thread.start()
    server = AppServer(codex, directory, endpoint.server_address[1])
    try:
        server.request(
            "initialize",
            {
                "clientInfo": {"name": "maida-native-smoke", "version": "1.0.0"},
                "capabilities": {"experimentalApi": True},
            },
        )
        server.send({"method": "initialized", "params": {}})
        project = directory / "project"
        verify_trust(server.request("hooks/list", {"cwds": [str(project)]}))
        started = server.request(
            "thread/start",
            {
                "cwd": str(project),
                "model": MODEL,
                "modelProvider": "maida-smoke",
                "sandbox": "read-only",
                "approvalPolicy": "never",
                "ephemeral": True,
            },
        )
        turn_parameters = {
            "threadId": started["thread"]["id"],
            "input": [
                {"type": "text", "text": "Read README.md once with the shell tool, then finish.", "text_elements": []}
            ],
        }
        if external_sandbox:
            turn_parameters["sandboxPolicy"] = {"type": "externalSandbox", "networkAccess": "restricted"}
        server.request(
            "turn/start",
            turn_parameters,
        )
        tool_exits = []
        deadline = time.monotonic() + 45
        while True:
            event = server.receive(deadline)
            if event.get("method") == "item/completed" and event["params"]["item"]["type"] == "commandExecution":
                tool_exits.append(event["params"]["item"].get("exitCode"))
            if event.get("method") == "turn/completed":
                if event["params"]["turn"].get("status") != "completed":
                    raise RuntimeError("Native Codex turn did not complete successfully")
                break
        if CannedResponsesHandler.failure or CannedResponsesHandler.request_count != 2:
            raise RuntimeError("Canned model/tool journey did not complete as expected")
        if tool_exits != [0]:
            raise RuntimeError(
                "Native fixture read tool failed; inspect runtime sandbox/socket permissions. Use --external-sandbox only inside an already isolated container."
            )
        # Check before app-server exit: SessionEnd must not be the completion source.
        checked = subprocess.run(
            [sys.executable, "-E", "-P", "-m", "maida.cli", "check", "--agent", "codex", "--format", "json"],
            cwd=project,
            env=isolated_environment(directory),
            capture_output=True,
            text=True,
            timeout=15,
        )
        if checked.returncode:
            raise RuntimeError("Real Codex hook capture did not pass maida check while the session remained open")
        report = json.loads(checked.stdout)
        return {
            "status": "pass",
            "codex_version": PINNED_CODEX_VERSION,
            "model_requests": 2,
            "session_open_at_check": True,
            "sandbox_coverage": "external container isolation; native nested sandbox unverified"
            if external_sandbox
            else "native read-only sandbox",
            "report": report,
        }
    finally:
        server.close()
        endpoint.shutdown()
        endpoint.server_close()
        thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", default="codex")
    parser.add_argument(
        "--external-sandbox",
        action="store_true",
        help="Use only inside an already isolated container; native nested sandbox coverage remains unverified",
    )
    choice = parser.add_mutually_exclusive_group(required=True)
    choice.add_argument("--prepare-directory", type=Path)
    choice.add_argument("--directory", type=Path)
    args = parser.parse_args()
    try:
        result = (
            prepare(args.prepare_directory.absolute(), args.codex)
            if args.prepare_directory
            else run_smoke(args.codex, args.directory.absolute(), external_sandbox=args.external_sandbox)
        )
        if "review_environment" in result:
            result["review_command_posix"] = shlex.join(
                [
                    "env",
                    "-i",
                    *[f"{key}={value}" for key, value in result["review_environment"].items()],
                    *result["review_argv"],
                ]
            )
        print(json.dumps(result, sort_keys=True))
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
