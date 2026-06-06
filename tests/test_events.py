"""Tests for event helpers: JSON-safety and depth limit (consistent with redaction)."""

from maida.constants import DEPTH_LIMIT, SPEC_VERSION, TRUNCATED_MARKER
from maida.events import (
    _ensure_json_safe,
    derive_event_payload,
    derive_span_type,
    span_to_event_dict,
    spans_to_events,
)


def test_json_safe_value_depth_exceeded_returns_truncated_marker():
    """When depth is exceeded, _json_safe_value returns TRUNCATED_MARKER (consistent with _redact_and_truncate)."""
    # Nest deeper than DEPTH_LIMIT so the inner value is at depth > DEPTH_LIMIT
    deep = "leaf"
    for _ in range(DEPTH_LIMIT + 1):
        deep = [deep]
    result = _ensure_json_safe(deep)
    # Navigate to the innermost element
    inner = result
    for _ in range(DEPTH_LIMIT + 1):
        assert isinstance(inner, list)
        assert len(inner) == 1
        inner = inner[0]
    assert inner == TRUNCATED_MARKER


def test_json_safe_value_at_limit_preserves_value():
    """At exactly DEPTH_LIMIT depth we still recurse; only beyond it we substitute TRUNCATED_MARKER."""
    # Nest exactly DEPTH_LIMIT levels of lists, with a string at the bottom
    inner = "ok"
    for _ in range(DEPTH_LIMIT):
        inner = [inner]
    result = _ensure_json_safe(inner)
    current = result
    for _ in range(DEPTH_LIMIT):
        assert isinstance(current, list)
        assert len(current) == 1
        current = current[0]
    assert current == "ok"


# ---------------------------------------------------------------------------
# derive_span_type
# ---------------------------------------------------------------------------


def _root_span(**overrides):
    span = {"parent_span_id": None, "name": "test-run", "attributes": {}}
    span.update(overrides)
    return span


def _child_span(**overrides):
    span = {
        "parent_span_id": "parent123",
        "span_id": "child456",
        "name": "some-operation",
        "attributes": {},
    }
    span.update(overrides)
    return span


def test_derive_span_type_root():
    assert derive_span_type(_root_span()) == "RUN_START"


def test_derive_span_type_llm_call():
    span = _child_span(attributes={"gen_ai.system": "openai"})
    assert derive_span_type(span) == "LLM_CALL"


def test_derive_span_type_tool_call():
    span = _child_span(attributes={"maida.tool_name": "get_weather"})
    assert derive_span_type(span) == "TOOL_CALL"


def test_derive_span_type_state_update():
    span = _child_span(name="state")
    assert derive_span_type(span) == "STATE_UPDATE"


def test_derive_span_type_error():
    span = _child_span(attributes={"maida.error_type": "ValueError"})
    assert derive_span_type(span) == "ERROR"


def test_derive_span_type_unknown():
    assert derive_span_type(_child_span()) == "UNKNOWN"


# ---------------------------------------------------------------------------
# derive_event_payload
# ---------------------------------------------------------------------------


def test_derive_payload_root():
    span = _root_span(
        attributes={
            "maida.run_name": "my-run",
            "maida.python_version": "3.12",
            "maida.platform": "darwin",
            "maida.cwd": "/home/project",
            "maida.argv": ["python", "run.py"],
        }
    )
    payload = derive_event_payload(span)
    assert payload["run_name"] == "my-run"
    assert payload["python_version"] == "3.12"
    assert payload["cwd"] == "/home/project"


def test_derive_payload_llm_call():
    span = _child_span(
        name="gpt-4",
        attributes={
            "gen_ai.system": "openai",
            "gen_ai.usage.input_tokens": 10,
            "gen_ai.usage.output_tokens": 20,
        },
        events=[
            {"name": "gen_ai.user.message", "attributes": {"content": "hello"}},
            {"name": "gen_ai.assistant.message", "attributes": {"content": "hi there"}},
        ],
    )
    payload = derive_event_payload(span)
    assert payload["model"] == "gpt-4"
    assert payload["prompt"] == "hello"
    assert payload["response"] == "hi there"
    assert payload["usage"]["prompt_tokens"] == 10
    assert payload["usage"]["total_tokens"] == 30


def test_derive_payload_llm_call_preserves_total_tokens_without_parts():
    span = _child_span(
        name="gpt-4",
        attributes={
            "gen_ai.system": "openai",
            "gen_ai.usage.total_tokens": 42,
        },
    )

    payload = derive_event_payload(span)

    assert payload["usage"]["prompt_tokens"] is None
    assert payload["usage"]["completion_tokens"] is None
    assert payload["usage"]["total_tokens"] == 42


def test_derive_payload_tool_call():
    span = _child_span(
        name="get_weather",
        attributes={"maida.tool_name": "get_weather"},
        events=[
            {"name": "maida.tool.args", "attributes": {"args": '{"city": "NYC"}'}},
            {"name": "maida.tool.result", "attributes": {"result": '"sunny"'}},
        ],
    )
    payload = derive_event_payload(span)
    assert payload["tool_name"] == "get_weather"
    assert payload["args"] == {"city": "NYC"}
    assert payload["result"] == "sunny"


def test_derive_payload_state():
    span = _child_span(
        name="state",
        events=[
            {
                "name": "state",
                "attributes": {
                    "state": '{"phase": "planning"}',
                    "diff": '{"phase": ["old", "new"]}',
                },
            }
        ],
    )
    payload = derive_event_payload(span)
    assert payload["state"] == {"phase": "planning"}
    assert payload["diff"] == {"phase": ["old", "new"]}


def test_derive_payload_error():
    span = _child_span(
        attributes={
            "maida.error_type": "ValueError",
            "maida.error_message": "bad value",
        }
    )
    payload = derive_event_payload(span)
    assert payload["error_type"] == "ValueError"
    assert payload["message"] == "bad value"


# ---------------------------------------------------------------------------
# span_to_event_dict
# ---------------------------------------------------------------------------


def test_span_to_event_dict_root():
    span = _root_span(
        span_id="abc123",
        trace_id="def456",
        name="my-run",
        start_time="2025-01-01T00:00:00.000Z",
        duration_ms=1000,
        attributes={"maida.run_name": "my-run"},
    )
    ev = span_to_event_dict(span)
    assert ev["spec_version"] == SPEC_VERSION
    assert ev["event_type"] == "RUN_START"
    assert ev["run_id"] == "def456"
    assert ev["ts"] == "2025-01-01T00:00:00.000Z"
    assert ev["duration_ms"] == 1000


def test_span_to_event_dict_child():
    span = _child_span(
        name="gpt-4",
        start_time="2025-01-01T00:00:01.000Z",
        duration_ms=500,
        attributes={"gen_ai.system": "openai"},
    )
    ev = span_to_event_dict(span)
    assert ev["event_type"] == "LLM_CALL"
    assert ev["parent_id"] == "parent123"


# ---------------------------------------------------------------------------
# spans_to_events
# ---------------------------------------------------------------------------


def _span_factory(span_id, parent_id, start, **kw):
    base = {
        "span_id": span_id,
        "parent_span_id": parent_id,
        "trace_id": "trace001",
        "name": "test",
        "start_time": start,
        "end_time": start,
        "duration_ms": 0,
        "attributes": {},
        "events": [],
        "status_code": "OK",
    }
    base.update(kw)
    return base


def test_spans_to_events_basic():
    spans = [
        _span_factory(
            "root",
            None,
            "2025-01-01T00:00:00.000Z",
            name="my-run",
            end_time="2025-01-01T00:00:10.000Z",
            attributes={"maida.run_name": "my-run"},
        ),
        _span_factory(
            "c1",
            "root",
            "2025-01-01T00:00:01.000Z",
            name="gpt-4",
            end_time="2025-01-01T00:00:02.000Z",
            attributes={"gen_ai.system": "openai"},
        ),
    ]
    events = spans_to_events(spans)
    types = [e["event_type"] for e in events]
    assert types == ["RUN_START", "LLM_CALL", "RUN_END"]
    assert events[-1]["event_type"] == "RUN_END"
    assert events[-1]["payload"] == {"status": "ok"}


def test_spans_to_events_root_embedded_exception():
    spans = [
        _span_factory(
            "root",
            None,
            "2025-01-01T00:00:00.000Z",
            name="my-run",
            end_time="2025-01-01T00:00:10.000Z",
            attributes={"maida.run_name": "my-run"},
            events=[
                {
                    "name": "exception",
                    "timestamp": "2025-01-01T00:00:05.000Z",
                    "attributes": {
                        "maida.error_type": "RuntimeError",
                        "maida.error_message": "fail",
                    },
                }
            ],
        ),
    ]
    events = spans_to_events(spans)
    types = [e["event_type"] for e in events]
    assert "ERROR" in types
    err = next(e for e in events if e["event_type"] == "ERROR")
    assert err["payload"]["error_type"] == "RuntimeError"
    assert err["ts"] == "2025-01-01T00:00:05.000Z"


def test_spans_to_events_root_embedded_state():
    spans = [
        _span_factory(
            "root",
            None,
            "2025-01-01T00:00:00.000Z",
            name="my-run",
            end_time="2025-01-01T00:00:10.000Z",
            attributes={"maida.run_name": "my-run"},
            events=[
                {
                    "name": "state",
                    "timestamp": "2025-01-01T00:00:03.000Z",
                    "attributes": {
                        "state": '{"phase": "planning"}',
                    },
                }
            ],
        ),
    ]
    events = spans_to_events(spans)
    types = [e["event_type"] for e in events]
    assert "STATE_UPDATE" in types
    st = next(e for e in events if e["event_type"] == "STATE_UPDATE")
    assert st["payload"]["state"] == {"phase": "planning"}


def test_spans_to_events_root_embedded_loop_warning():
    spans = [
        _span_factory(
            "root",
            None,
            "2025-01-01T00:00:00.000Z",
            name="my-run",
            end_time="2025-01-01T00:00:10.000Z",
            attributes={"maida.run_name": "my-run"},
            events=[
                {
                    "name": "maida.loop.warning",
                    "timestamp": "2025-01-01T00:00:07.000Z",
                    "attributes": {
                        "message": "loop detected",
                        "iterations": "5",
                    },
                }
            ],
        ),
    ]
    events = spans_to_events(spans)
    types = [e["event_type"] for e in events]
    assert "LOOP_WARNING" in types
    lw = next(e for e in events if e["event_type"] == "LOOP_WARNING")
    assert lw["payload"]["message"] == "loop detected"
    assert lw["payload"]["iterations"] == "5"


def test_spans_to_events_chronological_sort():
    spans = [
        _span_factory(
            "root",
            None,
            "2025-01-01T00:00:00.000Z",
            name="run",
            end_time="2025-01-01T00:00:50.000Z",
            attributes={"maida.run_name": "run"},
            events=[
                {
                    "name": "state",
                    "timestamp": "2025-01-01T00:00:20.000Z",
                    "attributes": {"state": '"mid-run"'},
                },
                {
                    "name": "exception",
                    "timestamp": "2025-01-01T00:00:40.000Z",
                    "attributes": {
                        "maida.error_type": "Error",
                        "maida.error_message": "late error",
                    },
                },
            ],
        ),
        _span_factory(
            "c1",
            "root",
            "2025-01-01T00:00:10.000Z",
            name="first_call",
            end_time="2025-01-01T00:00:15.000Z",
            attributes={"gen_ai.system": "openai"},
        ),
        _span_factory(
            "c2",
            "root",
            "2025-01-01T00:00:30.000Z",
            name="second_call",
            end_time="2025-01-01T00:00:35.000Z",
            attributes={"gen_ai.system": "openai"},
        ),
    ]
    events = spans_to_events(spans)
    ts_list = [(e["event_type"], e["ts"]) for e in events]
    assert ts_list == [
        ("RUN_START", "2025-01-01T00:00:00.000Z"),
        ("LLM_CALL", "2025-01-01T00:00:10.000Z"),
        ("STATE_UPDATE", "2025-01-01T00:00:20.000Z"),
        ("LLM_CALL", "2025-01-01T00:00:30.000Z"),
        ("ERROR", "2025-01-01T00:00:40.000Z"),
        ("RUN_END", "2025-01-01T00:00:50.000Z"),
    ]


def test_spans_to_events_empty():
    assert spans_to_events([]) == []


def test_spans_to_events_no_root():
    spans = [
        _span_factory(
            "c1",
            "missing-root",
            "2025-01-01T00:00:01.000Z",
            attributes={"gen_ai.system": "openai"},
        ),
    ]
    events = spans_to_events(spans)
    types = [e["event_type"] for e in events]
    assert types == ["LLM_CALL"]


def test_spans_to_events_root_status_error():
    spans = [
        _span_factory(
            "root",
            None,
            "2025-01-01T00:00:00.000Z",
            name="fail-run",
            end_time="2025-01-01T00:00:10.000Z",
            attributes={"maida.run_name": "fail-run"},
            status_code="ERROR",
        ),
    ]
    events = spans_to_events(spans)
    run_end = next(e for e in events if e["event_type"] == "RUN_END")
    assert run_end["payload"]["status"] == "error"
