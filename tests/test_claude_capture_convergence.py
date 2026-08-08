"""Integrated equivalence coverage for Claude OTel and hook capture paths."""

from __future__ import annotations

import json

from fastapi.testclient import TestClient
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord, ResourceLogs, ScopeLogs
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from typer.testing import CliRunner

from maida.baseline import create_baseline, extract_run_metrics, save_baseline
from maida.capture.claude_code import create_claude_code_app
from maida.capture.claude_hook import capture_claude_hook
from maida.cli import app
from maida.config import load_config
from maida.integrations.claude_code import import_claude_capture
from maida.storage import load_run_for_analysis


def _kv(key: str, value: object) -> KeyValue:
    encoded = AnyValue()
    if isinstance(value, bool):
        encoded.bool_value = value
    elif isinstance(value, int):
        encoded.int_value = value
    else:
        encoded.string_value = str(value)
    return KeyValue(key=key, value=encoded)


def _capture_otel_tool(session_id: str) -> None:
    attributes = [
        _kv("session.id", session_id),
        _kv("event.name", "tool_result"),
        _kv("event.sequence", 1),
        _kv("event.timestamp", "2026-08-08T12:00:00Z"),
        _kv("tool_name", "Read"),
        _kv("tool_use_id", "toolu_equivalent"),
        _kv("success", True),
        _kv("duration_ms", 0),
        _kv("tool_input", json.dumps({"file_path": "/workspace/README.md"})),
    ]
    request = ExportLogsServiceRequest(
        resource_logs=[
            ResourceLogs(
                resource=Resource(
                    attributes=[
                        _kv("service.name", "claude-code"),
                        _kv("app.version", "2.1.220"),
                    ]
                ),
                scope_logs=[
                    ScopeLogs(
                        log_records=[
                            LogRecord(
                                time_unix_nano=1_754_656_800_000_000_000,
                                observed_time_unix_nano=1_754_656_800_000_000_000,
                                body=AnyValue(string_value="claude_code.tool_result"),
                                attributes=attributes,
                            )
                        ]
                    )
                ],
            )
        ]
    )
    response = TestClient(create_claude_code_app(load_config())).post(
        "/v1/logs",
        content=request.SerializeToString(),
        headers={"content-type": "application/x-protobuf"},
    )
    assert response.status_code == 200


def _capture_hook_tool(session_id: str) -> None:
    capture_claude_hook(
        {
            "session_id": session_id,
            "transcript_path": "/private/transcript.jsonl",
            "cwd": "/workspace",
            "hook_event_name": "PreToolUse",
            "tool_name": "Read",
            "tool_use_id": "toolu_equivalent",
            "tool_input": {"file_path": "/workspace/README.md"},
        },
        load_config(),
    )


def _downstream_semantics(trace_id: str) -> dict:
    _full_id, meta, events = load_run_for_analysis(trace_id, load_config())
    return {
        "metrics": extract_run_metrics(meta, events),
        "events": [
            {
                "event_type": event["event_type"],
                "name": event["name"],
                "payload": event["payload"],
            }
            for event in events
            if event["event_type"] not in {"RUN_START", "RUN_END"}
        ],
    }


def test_otel_and_hook_capture_converge_on_semantics_and_local_gate_output(
    temp_data_dir,
):
    otel_session = "integrated-otel-session"
    hook_session = "integrated-hook-session"
    _capture_otel_tool(otel_session)
    _capture_hook_tool(hook_session)

    config = load_config()
    otel = import_claude_capture(otel_session, config)
    hook = import_claude_capture(hook_session, config)
    assert _downstream_semantics(otel.trace_id) == _downstream_semantics(hook.trace_id)

    baseline_path = temp_data_dir / "equivalent-baseline.json"
    save_baseline(create_baseline(otel.trace_id, config), baseline_path)
    policy_path = temp_data_dir / "equivalent-policy.yaml"
    policy_path.write_text(
        "assert:\n"
        "  no_new_tools: true\n"
        "  no_loops: true\n"
        "  max_steps: 2\n"
        "  max_tool_calls: 1\n",
        encoding="utf-8",
    )

    runner = CliRunner()
    outputs = []
    for session_id in (otel_session, hook_session):
        result = runner.invoke(
            app,
            [
                "diff",
                "--capture",
                session_id,
                "--baseline",
                str(baseline_path),
                "--policy",
                str(policy_path),
                "--format",
                "text",
            ],
        )
        assert result.exit_code == 0, result.stderr
        assert "Claude Code capture" not in result.stdout
        assert "already imported" in result.stderr
        outputs.append(result.stdout)

    assert outputs[0] == outputs[1]
    assert "RESULT: PASSED" in outputs[0]
