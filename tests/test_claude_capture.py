import hashlib
import json

from fastapi.testclient import TestClient
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import (
    ExportLogsServiceRequest,
)
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
    ExportTraceServiceRequest,
)
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.logs.v1.logs_pb2 import LogRecord, ResourceLogs, ScopeLogs
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import (
    ResourceSpans,
    ScopeSpans,
    Span,
    Status,
)

from maida.capture.claude_code import create_claude_code_app
from maida.config import load_config


def _kv(key: str, value: object) -> KeyValue:
    any_value = AnyValue()
    if isinstance(value, bool):
        any_value.bool_value = value
    elif isinstance(value, int):
        any_value.int_value = value
    elif isinstance(value, float):
        any_value.double_value = value
    else:
        any_value.string_value = str(value)
    return KeyValue(key=key, value=any_value)


def _logs_request(
    *,
    session_id: str = "session/with traversal/../chars",
    event_name: str = "claude_code.api_request",
    sequence: int = 1,
    input_tokens: int = 12,
    extra: list[KeyValue] | None = None,
) -> ExportLogsServiceRequest:
    attrs = [
        _kv("session.id", session_id),
        _kv("event.name", event_name.removeprefix("claude_code.")),
        _kv("event.sequence", sequence),
        _kv("event.timestamp", "2026-08-08T12:00:00Z"),
    ]
    if event_name == "claude_code.api_request":
        attrs += [
            _kv("model", "claude-test"),
            _kv("duration_ms", 25),
            _kv("input_tokens", input_tokens),
            _kv("output_tokens", 4),
            _kv("cache_read_tokens", 0),
            _kv("cache_creation_tokens", 0),
            _kv("cost_usd", 0.001),
        ]
    attrs += extra or []
    record = LogRecord(
        time_unix_nano=1_754_656_800_000_000_000,
        observed_time_unix_nano=1_754_656_800_000_000_001,
        body=AnyValue(string_value=event_name),
        attributes=attrs,
        trace_id=bytes.fromhex("11" * 16),
        span_id=bytes.fromhex("22" * 8),
    )
    return ExportLogsServiceRequest(
        resource_logs=[
            ResourceLogs(
                resource=Resource(attributes=[_kv("service.name", "claude-code")]),
                scope_logs=[ScopeLogs(log_records=[record])],
            )
        ]
    )


def _traces_request(
    *, session_id: str = "trace-session", duration_ms: int = 20
) -> ExportTraceServiceRequest:
    start = 1_754_656_800_000_000_000
    span = Span(
        trace_id=bytes.fromhex("33" * 16),
        span_id=bytes.fromhex("44" * 8),
        name="claude_code.llm_request",
        kind=Span.SPAN_KIND_CLIENT,
        start_time_unix_nano=start,
        end_time_unix_nano=start + duration_ms * 1_000_000,
        attributes=[
            _kv("session.id", session_id),
            _kv("span.type", "claude_code.llm_request"),
            _kv("model", "claude-test"),
            _kv("duration_ms", duration_ms),
            _kv("input_tokens", 8),
            _kv("output_tokens", 3),
            _kv("success", True),
        ],
        status=Status(code=Status.STATUS_CODE_OK),
    )
    return ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                resource=Resource(attributes=[_kv("service.name", "claude-code")]),
                scope_spans=[ScopeSpans(spans=[span])],
            )
        ]
    )


def _capture_dir(temp_data_dir, session_id: str):
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    return temp_data_dir / "captures" / "claude-code" / digest / "0001"


def _post(client: TestClient, path: str, message) -> object:
    return client.post(
        path,
        content=message.SerializeToString(),
        headers={"content-type": "application/x-protobuf"},
    )


def test_health_and_log_capture_redacts_nested_content_but_keeps_tokens(temp_data_dir):
    client = TestClient(create_claude_code_app(load_config()))
    assert client.get("/healthz").json() == {"status": "ok"}

    request = _logs_request(
        extra=[
            _kv(
                "tool_input",
                json.dumps(
                    {
                        "path": "/tmp/a.py",
                        "nested": {"api_key": "sk-secret", "value": "ok"},
                    }
                ),
            )
        ]
    )
    response = _post(client, "/v1/logs", request)
    assert response.status_code == 200

    capture_dir = _capture_dir(temp_data_dir, "session/with traversal/../chars")
    assert capture_dir.is_dir()
    manifest = json.loads((capture_dir / "manifest.json").read_text())
    assert manifest["source"] == "claude-code"
    assert manifest["segment"] == "0001"
    assert "session/with" not in json.dumps(manifest)

    records = [
        json.loads(line)
        for line in (capture_dir / "logs.jsonl").read_text().splitlines()
    ]
    assert len(records) == 1
    attrs = records[0]["record"]["attributes"]
    assert attrs["input_tokens"] == 12
    assert attrs["output_tokens"] == 4
    nested = json.loads(attrs["tool_input"])
    assert nested["nested"] == {"api_key": "__REDACTED__", "value": "ok"}
    assert attrs["session.id"] == manifest["session_hash"]


def test_trace_capture_persists_source_shape(temp_data_dir):
    client = TestClient(create_claude_code_app(load_config()))
    response = _post(client, "/v1/traces", _traces_request())
    assert response.status_code == 200

    capture_dir = _capture_dir(temp_data_dir, "trace-session")
    records = [
        json.loads(line)
        for line in (capture_dir / "spans.jsonl").read_text().splitlines()
    ]
    assert len(records) == 1
    assert records[0]["span"]["name"] == "claude_code.llm_request"
    assert records[0]["span"]["trace_id"] == "33" * 16
    assert records[0]["span"]["duration_ms"] == 20
    assert records[0]["span"]["attributes"]["input_tokens"] == 8


def test_complete_batch_is_validated_before_any_write(temp_data_dir):
    request = _logs_request()
    invalid = request.resource_logs[0].scope_logs[0].log_records.add()
    invalid.CopyFrom(request.resource_logs[0].scope_logs[0].log_records[0])
    for attribute in invalid.attributes:
        if attribute.key == "input_tokens":
            attribute.value.int_value = -1

    response = _post(
        TestClient(create_claude_code_app(load_config())), "/v1/logs", request
    )
    assert response.status_code == 400
    assert not (temp_data_dir / "captures").exists()


def test_exact_retry_is_deduplicated_and_conflicting_identity_is_rejected(
    temp_data_dir,
):
    client = TestClient(create_claude_code_app(load_config()))
    request = _logs_request()
    assert _post(client, "/v1/logs", request).status_code == 200
    retry = _post(client, "/v1/logs", request)
    assert retry.status_code == 200

    conflict = _logs_request(input_tokens=13)
    response = _post(client, "/v1/logs", conflict)
    assert response.status_code == 409
    capture_dir = _capture_dir(temp_data_dir, "session/with traversal/../chars")
    assert len((capture_dir / "logs.jsonl").read_text().splitlines()) == 1


def test_rejects_wrong_content_type_oversized_and_invalid_ids(temp_data_dir):
    client = TestClient(create_claude_code_app(load_config(), max_request_bytes=10))
    body = _logs_request().SerializeToString()
    assert client.post("/v1/logs", content=body).status_code == 415
    assert (
        client.post(
            "/v1/logs",
            content=body,
            headers={"content-type": "application/x-protobuf"},
        ).status_code
        == 413
    )

    request = _traces_request()
    request.resource_spans[0].scope_spans[0].spans[0].trace_id = b"short"
    response = _post(
        TestClient(create_claude_code_app(load_config())), "/v1/traces", request
    )
    assert response.status_code == 400
    assert not (temp_data_dir / "captures").exists()


def test_unknown_additive_log_record_is_preserved(temp_data_dir):
    request = _logs_request(event_name="claude_code.future_signal")
    response = _post(
        TestClient(create_claude_code_app(load_config())), "/v1/logs", request
    )
    assert response.status_code == 200
    capture_dir = _capture_dir(temp_data_dir, "session/with traversal/../chars")
    record = json.loads((capture_dir / "logs.jsonl").read_text().splitlines()[0])
    assert record["record"]["event_name"] == "claude_code.future_signal"


def test_known_numeric_log_fields_accept_claude_string_encoding(temp_data_dir):
    request = _logs_request()
    for attribute in request.resource_logs[0].scope_logs[0].log_records[0].attributes:
        if attribute.key in {"duration_ms", "input_tokens", "output_tokens"}:
            attribute.value.string_value = str(attribute.value.int_value)

    response = _post(
        TestClient(create_claude_code_app(load_config())), "/v1/logs", request
    )
    assert response.status_code == 200
    capture_dir = _capture_dir(temp_data_dir, "session/with traversal/../chars")
    record = json.loads((capture_dir / "logs.jsonl").read_text().splitlines()[0])
    assert record["record"]["attributes"]["input_tokens"] == 12
    assert record["record"]["attributes"]["duration_ms"] == 25
