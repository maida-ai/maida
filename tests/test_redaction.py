"""
Tests for redaction: sensitive keys in payloads are replaced with __REDACTED__.
Uses MAIDA_REDACT_KEYS and temp dir via MAIDA_DATA_DIR.
"""

import os
from unittest.mock import patch

import pytest

from pathlib import Path

from maida.constants import REDACTED_MARKER, TRUNCATED_MARKER
from maida.config import load_config, MaidaConfig
from maida.guardrails import GuardrailParams
from maida.events import EventType
from maida._tracing._redact import _redact_and_truncate
from maida.tracing import record_tool_call, trace, traced_run
from maida.events import spans_to_events
from maida.storage import list_runs, load_spans


def test_redaction_constants_unchanged():
    """Guards against accidental refactors."""
    assert REDACTED_MARKER == "__REDACTED__"
    assert TRUNCATED_MARKER == "__TRUNCATED__"


def test_max_field_truncation():
    """Strings over MAIDA_MAX_FIELD_BYTES are truncated and suffixed with __TRUNCATED__."""
    max_bytes = 100
    cfg = MaidaConfig(
        redact=True,
        redact_keys=["token"],
        max_field_bytes=max_bytes,
        loop_window=12,
        loop_repetitions=3,
        data_dir=Path("."),
        guardrails=GuardrailParams(),
    )
    short = "under limit"
    assert len(short.encode("utf-8")) <= max_bytes
    assert _redact_and_truncate(short, cfg) == short

    long_str = "x" * (max_bytes + 1)
    result = _redact_and_truncate(long_str, cfg)
    assert result.endswith(TRUNCATED_MARKER)
    assert len(result.encode("utf-8")) <= max_bytes

    # Nested dict: long value in payload is truncated
    payload = {"prompt": "a" * (max_bytes + 10), "other": "short"}
    out = _redact_and_truncate(payload, cfg)
    assert out["prompt"].endswith(TRUNCATED_MARKER)
    assert len(out["prompt"].encode("utf-8")) <= max_bytes
    assert out["other"] == "short"


@pytest.fixture
def redact_token_env():
    """Set MAIDA_REDACT_KEYS=token for the test."""
    old = os.environ.get("MAIDA_REDACT_KEYS")
    try:
        os.environ["MAIDA_REDACT_KEYS"] = "token"
        yield
    finally:
        if old is not None:
            os.environ["MAIDA_REDACT_KEYS"] = old
        elif "MAIDA_REDACT_KEYS" in os.environ:
            os.environ.pop("MAIDA_REDACT_KEYS")


@pytest.fixture
def redact_message_and_stack_env():
    """Set MAIDA_REDACT_KEYS=message,stack so ERROR payload message/stack are redacted."""
    old = os.environ.get("MAIDA_REDACT_KEYS")
    try:
        os.environ["MAIDA_REDACT_KEYS"] = "message,stack"
        yield
    finally:
        if old is not None:
            os.environ["MAIDA_REDACT_KEYS"] = old
        elif "MAIDA_REDACT_KEYS" in os.environ:
            os.environ.pop("MAIDA_REDACT_KEYS")


def test_record_tool_call_redacts_args_with_token_key(temp_data_dir, redact_token_env):
    """record_tool_call with args containing 'token' key -> value is __REDACTED__."""

    @trace
    def run_with_tool():
        record_tool_call("my_tool", args={"token": "secret-api-key", "query": "hello"})

    run_with_tool()

    config = load_config()
    runs = list_runs(limit=1, config=config)
    assert runs
    run_id = runs[0].get("run_id") or runs[0].get("trace_id")
    spans = load_spans(run_id, config)
    events = spans_to_events(spans)

    tool_events = [
        e for e in events if e.get("event_type") == EventType.TOOL_CALL.value
    ]
    assert len(tool_events) == 1
    payload = tool_events[0]["payload"]
    args = payload.get("args")
    assert isinstance(args, dict)
    assert args.get("token") == REDACTED_MARKER
    assert args.get("query") == "hello"


def test_error_event_payload_redacted_decorator(
    temp_data_dir, redact_message_and_stack_env
):
    """ERROR from @trace has message and stack redacted when redact_keys include message,stack."""

    @trace
    def run_that_raises():
        raise ValueError("API key sk-abc123 is invalid")

    with pytest.raises(ValueError, match="API key sk-abc123 is invalid"):
        run_that_raises()

    config = load_config()
    runs = list_runs(limit=1, config=config)
    assert runs
    run_id = runs[0].get("run_id") or runs[0].get("trace_id")
    spans = load_spans(run_id, config)
    events = spans_to_events(spans)

    error_events = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    assert len(error_events) == 1
    payload = error_events[0]["payload"]
    assert payload.get("message") == REDACTED_MARKER
    assert payload.get("stack") == REDACTED_MARKER
    assert payload.get("error_type") == "ValueError"


def test_error_event_payload_redacted_context_manager(
    temp_data_dir, redact_message_and_stack_env
):
    """ERROR from traced_run() has message and stack redacted when redact_keys include message,stack."""
    with pytest.raises(ValueError, match="secret in context"):
        with traced_run(name="failing_run"):
            raise ValueError("secret in context")

    config = load_config()
    runs = list_runs(limit=1, config=config)
    assert runs
    run_id = runs[0].get("run_id") or runs[0].get("trace_id")
    spans = load_spans(run_id, config)
    events = spans_to_events(spans)

    error_events = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    assert len(error_events) == 1
    payload = error_events[0]["payload"]
    assert payload.get("message") == REDACTED_MARKER
    assert payload.get("stack") == REDACTED_MARKER


def test_run_start_argv_redacted(temp_data_dir):
    """RUN_START keeps argv but redacts only sensitive option values, e.g. --api-key=secret -> --api-key=__REDACTED__."""
    with patch("sys.argv", ["test_script.py", "--api-key=sk-secret-1234", "--verbose"]):

        @trace
        def run_quiet():
            pass

        run_quiet()

    config = load_config()
    runs = list_runs(limit=1, config=config)
    assert runs
    run_id = runs[0].get("run_id") or runs[0].get("trace_id")
    spans = load_spans(run_id, config)
    events = spans_to_events(spans)

    run_start_events = [
        e for e in events if e.get("event_type") == EventType.RUN_START.value
    ]
    assert len(run_start_events) == 1
    payload = run_start_events[0]["payload"]
    argv = payload.get("argv")
    assert isinstance(argv, list)
    assert argv == ["test_script.py", f"--api-key={REDACTED_MARKER}", "--verbose"]


def _redact_cfg(keys: list[str]) -> MaidaConfig:
    """Minimal config with redaction enabled and given redact_keys."""
    return MaidaConfig(
        redact=True,
        redact_keys=keys,
        max_field_bytes=1000,
        loop_window=12,
        loop_repetitions=3,
        data_dir=Path("."),
        guardrails=GuardrailParams(),
    )


def test_redact_nested_dict():
    """Nested dicts: sensitive keys at any depth are redacted; structure and non-matching keys preserved."""
    cfg = _redact_cfg(["token", "api_key"])
    payload = {
        "query": "hello",
        "headers": {
            "Authorization": "Bearer xyz",
            "token": "secret-123",
            "X-Request-Id": "req-1",
        },
        "body": {
            "user": "alice",
            "credentials": {
                "api_key": "sk-live-abc",
                "region": "us-east-1",
            },
        },
    }
    out = _redact_and_truncate(payload, cfg)
    assert out["query"] == "hello"
    assert out["headers"]["Authorization"] == "Bearer xyz"
    assert out["headers"]["token"] == REDACTED_MARKER
    assert out["headers"]["X-Request-Id"] == "req-1"
    assert out["body"]["user"] == "alice"
    assert out["body"]["credentials"]["api_key"] == REDACTED_MARKER
    assert out["body"]["credentials"]["region"] == "us-east-1"


def test_redact_case_insensitive():
    """Key matching is case-insensitive: API_KEY and Token are redacted when redact_keys include api_key, token."""
    cfg = _redact_cfg(["api_key", "token"])
    payload = {
        "API_KEY": "sk-secret",
        "Token": "bearer-xyz",
        "normal_key": "keep",
    }
    out = _redact_and_truncate(payload, cfg)
    assert out["API_KEY"] == REDACTED_MARKER
    assert out["Token"] == REDACTED_MARKER
    assert out["normal_key"] == "keep"


def test_redact_substring_match():
    """Key matching is substring: my_api_key_here is redacted when redact_keys include api_key."""
    cfg = _redact_cfg(["api_key"])
    payload = {
        "my_api_key_here": "sk-xxx",
        "api_key": "sk-yyy",
        "prefix_api_key_suffix": "sk-zzz",
        "other": "unchanged",
    }
    out = _redact_and_truncate(payload, cfg)
    assert out["my_api_key_here"] == REDACTED_MARKER
    assert out["api_key"] == REDACTED_MARKER
    assert out["prefix_api_key_suffix"] == REDACTED_MARKER
    assert out["other"] == "unchanged"


def test_exception_message_secret_not_in_events_jsonl(
    temp_data_dir, redact_message_and_stack_env
):
    """Secret in exception message must NOT appear anywhere in spans.jsonl file content."""
    secret = "sk-leaked-api-key-xyz789"
    assert secret not in REDACTED_MARKER

    @trace
    def run_that_leaks():
        raise ValueError(f"Auth failed: API key {secret} is invalid")

    with pytest.raises(ValueError):
        run_that_leaks()

    config = load_config()
    runs = list_runs(limit=1, config=config)
    assert runs
    run_id = runs[0].get("run_id") or runs[0].get("trace_id")
    spans_path = config.data_dir / "runs" / run_id / "spans.jsonl"
    raw_content = spans_path.read_text(encoding="utf-8")

    assert secret not in raw_content, (
        f"Secret {secret!r} must not appear in spans.jsonl"
    )


def test_argv_api_key_not_in_spans_jsonl(temp_data_dir):
    """argv containing --api-key=... must NOT appear in spans.jsonl (value redacted or omitted)."""
    secret = "sk-secret-1234"

    with patch("sys.argv", ["main.py", f"--api-key={secret}", "--verbose"]):

        @trace
        def run_quiet():
            pass

        run_quiet()

    config = load_config()
    runs = list_runs(limit=1, config=config)
    assert runs
    run_id = runs[0].get("run_id") or runs[0].get("trace_id")
    spans_path = config.data_dir / "runs" / run_id / "spans.jsonl"
    raw_content = spans_path.read_text(encoding="utf-8")

    assert secret not in raw_content, (
        f"API key value {secret!r} must not appear in spans.jsonl"
    )
