#!/usr/bin/env python3
"""Offline real-Claude smoke for the Claude Code OTLP capture path."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import uvicorn

from maida.capture.claude_code import create_claude_code_app
from maida.config import load_config

PINNED_CLAUDE_VERSION = "2.1.220"
SESSION_ID = "06106d0c-8a68-4ac7-a5d8-3448ff8e0243"


def _sse(events: list[dict[str, Any]]) -> bytes:
    chunks = []
    for event in events:
        chunks.append(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n")
    return "".join(chunks).encode("utf-8")


def _message_start(message_id: str) -> dict[str, Any]:
    return {
        "type": "message_start",
        "message": {
            "id": message_id,
            "type": "message",
            "role": "assistant",
            "model": "claude-test",
            "content": [],
            "stop_reason": None,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 9,
                "output_tokens": 1,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        },
    }


def _tool_response(read_path: Path) -> bytes:
    tool_input = json.dumps({"file_path": str(read_path)}, separators=(",", ":"))
    return _sse(
        [
            _message_start("msg_smoke_tool"),
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "tool_use",
                    "id": "toolu_smoke_read",
                    "name": "Read",
                    "input": {},
                },
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": tool_input},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                "usage": {"output_tokens": 8},
            },
            {"type": "message_stop"},
        ]
    )


def _final_response() -> bytes:
    return _sse(
        [
            _message_start("msg_smoke_final"),
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            },
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Smoke complete."},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 4},
            },
            {"type": "message_stop"},
        ]
    )


class _CannedAnthropicHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    request_count = 0
    read_path = Path("README.md")

    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
        if not self.path.startswith("/v1/messages"):
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("content-length", "0"))
        except ValueError:
            self.send_error(400)
            return
        request_body = self.rfile.read(length)
        try:
            request = json.loads(request_body)
        except json.JSONDecodeError:
            self.send_error(400)
            return
        if request.get("model") != "claude-test" or request.get("stream") is not True:
            self.send_error(400)
            return

        type(self).request_count += 1
        if type(self).request_count == 1:
            response = _tool_response(type(self).read_path)
        else:
            response = _final_response()
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("cache-control", "no-cache")
        self.send_header("connection", "close")
        self.send_header("content-length", str(len(response)))
        self.send_header("request-id", f"req_smoke_{type(self).request_count}")
        self.end_headers()
        self.wfile.write(response)


def _receiver(data_dir: Path) -> tuple[uvicorn.Server, threading.Thread, int]:
    config = replace(load_config(), data_dir=data_dir)
    app = create_claude_code_app(config)
    server = uvicorn.Server(
        uvicorn.Config(app=app, log_level="error", access_log=False)
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = listener.getsockname()[1]
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        raise RuntimeError("capture receiver did not start")
    return server, thread, port


def _filtered_environment(
    *, home: Path, anthropic_port: int, capture_port: int
) -> dict[str, str]:
    environment = {
        key: os.environ[key]
        for key in ("PATH", "LANG", "LC_ALL", "TMPDIR")
        if key in os.environ
    }
    environment.update(
        {
            "HOME": str(home),
            "ANTHROPIC_API_KEY": "sk-ant-api03-local-smoke-not-a-secret",
            "ANTHROPIC_BASE_URL": f"http://127.0.0.1:{anthropic_port}",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
            "CLAUDE_CODE_PROPAGATE_TRACEPARENT": "1",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_ENDPOINT": f"http://127.0.0.1:{capture_port}",
            "OTEL_LOGS_EXPORT_INTERVAL": "100",
            "OTEL_TRACES_EXPORT_INTERVAL": "100",
            "OTEL_LOG_USER_PROMPTS": "0",
            "OTEL_LOG_ASSISTANT_RESPONSES": "0",
            "OTEL_LOG_TOOL_DETAILS": "1",
            "OTEL_LOG_TOOL_CONTENT": "0",
        }
    )
    return environment


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _assert_capture(data_dir: Path) -> dict[str, Any]:
    manifests = list(
        (data_dir / "captures" / "claude-code").glob("*/0001/manifest.json")
    )
    if len(manifests) != 1:
        raise RuntimeError("expected exactly one captured Claude session")
    capture_dir = manifests[0].parent
    logs = _jsonl(capture_dir / "logs.jsonl")
    spans = _jsonl(capture_dir / "spans.jsonl")
    event_names = {record["record"]["event_name"] for record in logs}
    span_names = {record["span"]["name"] for record in spans}
    required_events = {"claude_code.api_request", "claude_code.tool_result"}
    required_spans = {"claude_code.llm_request", "claude_code.tool"}
    if not required_events.issubset(event_names):
        raise RuntimeError("Claude smoke did not export required log signals")
    if not required_spans.issubset(span_names):
        raise RuntimeError("Claude smoke did not export required trace signals")
    api_records = [
        record
        for record in logs
        if record["record"]["event_name"] == "claude_code.api_request"
    ]
    if not any(
        isinstance(record["record"]["attributes"].get("input_tokens"), int)
        and record["record"]["attributes"]["input_tokens"] > 0
        for record in api_records
    ):
        raise RuntimeError("Claude smoke did not retain numeric token counters")
    manifest = json.loads((capture_dir / "manifest.json").read_text(encoding="utf-8"))
    return {
        "status": "pass",
        "claude_version": PINNED_CLAUDE_VERSION,
        "session_hash": manifest["session_hash"][:12],
        "log_records": len(logs),
        "spans": len(spans),
        "required_signals": "present",
    }


def run_smoke(claude: str) -> dict[str, Any]:
    version = subprocess.run(
        [claude, "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.strip()
    if not version.startswith(PINNED_CLAUDE_VERSION):
        raise RuntimeError(
            f"Claude Code {PINNED_CLAUDE_VERSION} is required for this smoke"
        )

    with tempfile.TemporaryDirectory(prefix="maida-claude-smoke-") as temporary:
        root = Path(temporary)
        project = root / "project"
        home = root / "home"
        data_dir = root / "maida"
        project.mkdir()
        home.mkdir()
        read_path = project / "README.md"
        read_path.write_text("# Offline smoke fixture\n", encoding="utf-8")
        _CannedAnthropicHandler.read_path = read_path
        _CannedAnthropicHandler.request_count = 0
        anthropic = ThreadingHTTPServer(("127.0.0.1", 0), _CannedAnthropicHandler)
        anthropic_thread = threading.Thread(target=anthropic.serve_forever, daemon=True)
        anthropic_thread.start()
        receiver, receiver_thread, capture_port = _receiver(data_dir)
        try:
            environment = _filtered_environment(
                home=home,
                anthropic_port=anthropic.server_address[1],
                capture_port=capture_port,
            )
            command = [
                claude,
                "--bare",
                "--print",
                "--model",
                "claude-test",
                "--session-id",
                SESSION_ID,
                "--no-session-persistence",
                "--setting-sources",
                "project",
                "--strict-mcp-config",
                "--mcp-config",
                '{"mcpServers":{}}',
                "--tools",
                "Read",
                "--allowedTools",
                "Read",
                "--permission-mode",
                "dontAsk",
                "--max-budget-usd",
                "0.01",
                "Read README.md, then reply with a short confirmation.",
            ]
            completed = subprocess.run(
                command,
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.returncode != 0:
                raise RuntimeError("offline Claude Code invocation failed")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    return _assert_capture(data_dir)
                except (FileNotFoundError, RuntimeError):
                    time.sleep(0.05)
            return _assert_capture(data_dir)
        finally:
            receiver.should_exit = True
            receiver_thread.join(timeout=5)
            anthropic.shutdown()
            anthropic.server_close()
            anthropic_thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claude", default="claude")
    args = parser.parse_args()
    try:
        print(json.dumps(run_smoke(args.claude), sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "fail", "error": str(exc)}), file=os.sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
