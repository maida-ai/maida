"""
Tests for LangChain integration. Skip if langchain is not installed.
Uses temp dir; no network calls. Asserts TOOL_CALL and LLM_CALL events.
"""

import logging
import secrets
import sys
from types import SimpleNamespace

import pytest
from tests.conftest import get_latest_run_id

from maida import trace
from maida.baseline import extract_run_metrics
from maida.config import load_config
from maida.constants import REDACTED_MARKER, TRUNCATED_MARKER
from maida.events import EventType, spans_to_events
from maida.exceptions import (
    GuardrailExceeded,
    LoopAbort,
    _MaidaAbortSignal,
)
from maida.storage import list_runs, load_run_for_analysis, load_spans

try:
    from maida.integrations.langchain import LangChainCallbackHandler

    LANGCHAIN_MISSING = False
except ImportError:
    LANGCHAIN_MISSING = True


def test_langchain_integration_raises_clear_error_when_deps_missing():
    """When optional deps are missing, integration raises a clear error (no None, no NoneType)."""

    # Simulate missing langchain_core: access to .callbacks raises ImportError
    class FakeLangChainCore:
        def __getattr__(self, name: str):
            raise ImportError("No module named 'langchain_core.callbacks'")

    to_restore = {}
    for key in list(sys.modules.keys()):
        if key == "langchain_core" or key.startswith("langchain_core."):
            to_restore[key] = sys.modules.pop(key, None)
    for key in ("maida.integrations.langchain", "maida.integrations"):
        if key in sys.modules:
            to_restore[key] = sys.modules.pop(key)

    try:
        sys.modules["langchain_core"] = FakeLangChainCore()
        with pytest.raises(ImportError) as exc_info:
            from maida.integrations import LangChainCallbackHandler  # noqa: F401
        msg = str(exc_info.value)
        assert "langchain" in msg.lower(), f"message should mention langchain: {msg!r}"
        assert "pip install" in msg.lower(), (
            f"message should mention pip install: {msg!r}"
        )
        assert 'pip install "maida-ai[langchain]"' in msg, (
            f"message should name the public package extra: {msg!r}"
        )
    finally:
        for key in (
            "langchain_core",
            "maida.integrations.langchain",
            "maida.integrations",
        ):
            sys.modules.pop(key, None)
        sys.modules.update(to_restore)


def test_langchain_integration_does_not_break_core_import():
    """Core maida import must not crash when LangChain optional deps are missing."""

    class FakeLangChainCore:
        def __getattr__(self, name: str):
            raise ImportError("No module named 'langchain_core.callbacks'")

    to_restore = {}
    for key in list(sys.modules.keys()):
        if key == "langchain_core" or key.startswith("langchain_core."):
            to_restore[key] = sys.modules.pop(key, None)

    try:
        sys.modules["langchain_core"] = FakeLangChainCore()
        import maida  # noqa: F401

        assert maida.__version__
    finally:
        sys.modules.pop("langchain_core", None)
        for k, v in to_restore.items():
            if v is not None:
                sys.modules[k] = v


@trace
def _traced_with_handler():
    """Run one tool and one LLM via handler so events are recorded."""
    handler = LangChainCallbackHandler()
    config = {"callbacks": [handler]}

    from langchain_core.language_models.fake import FakeListLLM
    from langchain_core.tools import tool

    @tool
    def test_tool(x: str) -> str:
        """Test tool for integration."""
        return f"ok:{x}"

    llm = FakeListLLM(responses=["fake response"])
    test_tool.invoke({"x": "hello"}, config=config)
    llm.invoke("prompt", config=config)


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_handler_emits_tool_call_and_llm_call(temp_data_dir):
    """The offline success path persists the exact normalized structural signature."""
    _traced_with_handler()

    config = load_config()
    run_id = get_latest_run_id(config)
    resolved_id, meta, events = load_run_for_analysis(run_id, config)
    metrics = extract_run_metrics(meta, events)

    assert resolved_id == run_id
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        EventType.TOOL_CALL.value,
        EventType.LLM_CALL.value,
        EventType.RUN_END.value,
    ]
    duration_ms = metrics["summary"]["duration_ms"]
    assert isinstance(duration_ms, int)
    assert duration_ms >= 0
    assert {**metrics["summary"], "duration_ms": 0} == {
        "status": "ok",
        "total_events": 2,
        "llm_calls": 1,
        "tool_calls": 1,
        "errors": 0,
        "loop_warnings": 0,
        "duration_ms": 0,
        "total_tokens": 0,
    }
    assert metrics["tool_path"] == ["test_tool"]
    assert metrics["tool_call_sequence"] == ["test_tool"]
    assert metrics["tool_call_counts"] == {"test_tool": 1}
    assert metrics["llm_models_used"] == ["FakeListLLM"]
    assert metrics["event_type_sequence"] == [
        EventType.RUN_START.value,
        EventType.TOOL_CALL.value,
        EventType.LLM_CALL.value,
        EventType.RUN_END.value,
    ]
    assert metrics["final_status"] == "ok"

    tool_payload = events[1]["payload"]
    assert tool_payload["tool_name"] == "test_tool"
    assert tool_payload["status"] == "ok"
    assert events[2]["payload"]["model"] == "FakeListLLM"
    assert events[2]["payload"]["status"] == "ok"
    assert events[-1]["payload"] == {"status": "ok"}


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_handler_llm_error_is_normalized_on_call(temp_data_dir):
    """An LLM failure stays on LLM_CALL and does not invent a duplicate ERROR."""
    handler = LangChainCallbackHandler()

    @trace
    def _run():
        handler.on_llm_start(
            {"id": ["langchain", "ChatOffline"]},
            ["fixed prompt"],
            run_id="llm-error-1",
        )
        handler.on_llm_error(
            ValueError("simulated model failure"),
            run_id="llm-error-1",
        )

    _run()

    config = load_config()
    run_id = get_latest_run_id(config)
    _, meta, events = load_run_for_analysis(run_id, config)

    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        EventType.LLM_CALL.value,
        EventType.RUN_END.value,
    ]
    llm_payload = events[1]["payload"]
    assert llm_payload["model"] == "ChatOffline"
    assert llm_payload["prompt"] == "fixed prompt"
    assert llm_payload["response"] is None
    assert llm_payload["status"] == "error"
    assert llm_payload["error"]["error_type"] == "ValueError"
    assert llm_payload["error"]["message"] == "simulated model failure"
    assert meta["counts"] == {
        "llm_calls": 1,
        "tool_calls": 0,
        "errors": 0,
        "loop_warnings": 0,
    }
    assert meta["status"] == "ok"
    assert events[-1]["payload"] == {"status": "ok"}


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_handler_calls_outside_run_do_not_create_or_contaminate_run(
    temp_data_dir, monkeypatch
):
    """Callbacks outside a run are no-ops and cannot leak into a later run."""
    monkeypatch.delenv("MAIDA_IMPLICIT_RUN", raising=False)
    handler = LangChainCallbackHandler()

    handler.on_tool_start({"name": "outside_tool"}, "{}", run_id="outside-tool")
    handler.on_tool_end("ignored", run_id="outside-tool")
    handler.on_llm_start({"id": ["OutsideModel"]}, ["ignored"], run_id="outside-llm")
    handler.on_llm_error(ValueError("ignored"), run_id="outside-llm")

    config = load_config()
    assert list_runs(limit=10, config=config) == []

    @trace
    def _run():
        handler.on_tool_start({"name": "inside_tool"}, "{}", run_id="inside-tool")
        handler.on_tool_end("ok", run_id="inside-tool")

    _run()

    runs = list_runs(limit=10, config=config)
    assert len(runs) == 1
    run_id = runs[0]["trace_id"]
    _, meta, events = load_run_for_analysis(run_id, config)
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        EventType.TOOL_CALL.value,
        EventType.RUN_END.value,
    ]
    assert events[1]["payload"]["tool_name"] == "inside_tool"
    assert meta["counts"] == {
        "llm_calls": 0,
        "tool_calls": 1,
        "errors": 0,
        "loop_warnings": 0,
    }


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_handler_tool_error_emits_error_status(temp_data_dir):
    """Simulate tool error callback; record_tool_call is called with status=error."""
    handler = LangChainCallbackHandler()

    @trace
    def _run():
        handler.on_tool_start(
            {"name": "failing_tool"},
            '{"key": "value"}',
            run_id="00000000-0000-0000-0000-000000000001",
        )
        handler.on_tool_error(
            ValueError("simulated failure"),
            run_id="00000000-0000-0000-0000-000000000001",
        )

    _run()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = spans_to_events(load_spans(run_id, config))
    error_tools = [
        e
        for e in events
        if e.get("event_type") == EventType.TOOL_CALL.value
        and (e.get("payload") or {}).get("status") == "error"
    ]

    assert len(error_tools) >= 1, "expected at least one TOOL_CALL with status=error"
    err = error_tools[0].get("payload", {}).get("error")
    assert err is not None and isinstance(err, dict), (
        "error should be structured object"
    )
    assert err.get("error_type") == "ValueError"
    assert "simulated failure" in str(err.get("message", ""))


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_payloads_are_sanitized_before_persistence(
    temp_data_dir, monkeypatch
):
    """Adapter payloads use Maida's redaction/truncation storage boundary."""
    secret = secrets.token_hex(24)
    oversized = "public-" + ("x" * 200)
    monkeypatch.setenv("MAIDA_REDACT_KEYS", "api_key,message,stack")
    monkeypatch.setenv("MAIDA_MAX_FIELD_BYTES", "80")
    handler = LangChainCallbackHandler()

    @trace(name="langchain privacy")
    def _run():
        handler.on_chat_model_start(
            {"id": ["langchain", "PrivateChatModel"]},
            [
                [
                    SimpleNamespace(
                        type="human", content={"api_key": secret, "text": oversized}
                    )
                ]
            ],
            run_id="llm-private",
        )
        handler.on_llm_end(
            SimpleNamespace(
                generations=[
                    [SimpleNamespace(text={"api_key": secret, "text": oversized})]
                ],
                llm_output=None,
            ),
            run_id="llm-private",
        )
        handler.on_tool_start(
            {"name": "private_tool"},
            f'{{"api_key": "{secret}", "query": "{oversized}"}}',
            run_id="tool-private",
        )
        handler.on_tool_error(ValueError(secret), run_id="tool-private")

    _run()

    config = load_config()
    run_id = get_latest_run_id(config)
    raw = (config.data_dir / "runs" / run_id / "spans.jsonl").read_text(
        encoding="utf-8"
    )
    assert secret not in raw

    _, _, events = load_run_for_analysis(run_id, config)
    llm = next(event for event in events if event["event_type"] == "LLM_CALL")
    tool = next(event for event in events if event["event_type"] == "TOOL_CALL")
    assert REDACTED_MARKER in llm["payload"]["prompt"]
    assert REDACTED_MARKER in llm["payload"]["response"]
    assert TRUNCATED_MARKER in llm["payload"]["prompt"]
    assert TRUNCATED_MARKER in llm["payload"]["response"]
    assert tool["payload"]["args"]["api_key"] == REDACTED_MARKER
    assert tool["payload"]["args"]["query"].endswith(TRUNCATED_MARKER)
    assert tool["payload"]["error"]["message"] == REDACTED_MARKER


def _simulate_langchain_handle_event(handler, event_name: str, *args, **kwargs) -> None:
    """Simulate LangChain's handle_event: call the callback, re-raise if raise_error."""
    try:
        getattr(handler, event_name)(*args, **kwargs)
    except Exception:
        if handler.raise_error:
            raise
        logging.warning("Error in callback (swallowed by framework)")


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_handler_guardrail_propagates_via_raise_error(temp_data_dir):
    """stop_on_loop guardrail sets raise_error=True so LangChain propagates the abort."""
    handler = LangChainCallbackHandler()
    assert handler.raise_error is False, "raise_error should default to False"

    iterations_completed = 0

    @trace(stop_on_loop=True, stop_on_loop_min_repetitions=3)
    def _run():
        nonlocal iterations_completed
        for i in range(20):
            _simulate_langchain_handle_event(
                handler,
                "on_tool_start",
                {"name": "search"},
                '{"q": "pricing"}',
                run_id=f"tool-{i}",
            )
            _simulate_langchain_handle_event(
                handler,
                "on_tool_end",
                "no results",
                run_id=f"tool-{i}",
            )
            _simulate_langchain_handle_event(
                handler,
                "on_llm_start",
                {"id": ["ChatFake"]},
                ["Try again"],
                run_id=f"llm-{i}",
            )
            _simulate_langchain_handle_event(
                handler,
                "on_llm_end",
                None,
                run_id=f"llm-{i}",
            )
            iterations_completed += 1

    with pytest.raises(LoopAbort):
        _run()

    assert iterations_completed < 20, (
        f"guardrail should have stopped the loop early, but completed {iterations_completed}/20"
    )
    assert handler.raise_error is True, "raise_error should be True after guardrail"
    assert handler.abort_exception is not None
    assert isinstance(handler.abort_exception, GuardrailExceeded)

    config = load_config()
    run_id = get_latest_run_id(config)
    events = spans_to_events(load_spans(run_id, config))

    event_types = [e.get("event_type") for e in events]
    assert "LOOP_WARNING" in event_types, "trace should contain LOOP_WARNING"
    assert "ERROR" in event_types, "trace should contain ERROR"
    assert event_types[-1] == "RUN_END", "last event should be RUN_END"
    run_end_payload = events[-1].get("payload", {})
    assert run_end_payload.get("status") == "error"

    loop_warnings = [e for e in events if e.get("event_type") == "LOOP_WARNING"]
    patterns = {e["payload"]["pattern"] for e in loop_warnings}
    assert len(loop_warnings) == len(patterns), (
        f"each pattern should produce at most one LOOP_WARNING, "
        f"got {len(loop_warnings)} warnings for {len(patterns)} patterns"
    )


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_handler_resets_via_reset_method(temp_data_dir):
    """A reused handler resets raise_error and abort_exception via reset()."""
    handler = LangChainCallbackHandler()
    handler.raise_error = True
    handler._abort_exception = LoopAbort(threshold=3, actual=3, message="old")

    handler.reset()

    assert handler.raise_error is False, "raise_error should reset after reset()"
    assert handler.abort_exception is None, "abort_exception should reset after reset()"

    @trace
    def _run():
        _simulate_langchain_handle_event(
            handler,
            "on_llm_start",
            {"id": ["ChatFake"]},
            ["hello"],
            run_id="new-run-llm-0",
        )
        _simulate_langchain_handle_event(
            handler,
            "on_llm_end",
            None,
            run_id="new-run-llm-0",
        )

    _run()

    assert handler.raise_error is False
    assert handler.abort_exception is None


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_handler_blocks_after_abort(temp_data_dir):
    """Once a guardrail fires, subsequent on_llm_start / on_tool_start
    raise _MaidaAbortSignal (BaseException) so it bypasses framework-level
    ``except Exception`` handlers and propagates out."""
    handler = LangChainCallbackHandler()
    handler._abort_exception = LoopAbort(threshold=3, actual=3, message="loop")

    with pytest.raises(_MaidaAbortSignal) as exc_info:
        handler.on_llm_start({"id": ["ChatFake"]}, ["hello"], run_id="llm-1")
    assert isinstance(exc_info.value.cause, LoopAbort)
    assert handler.raise_error is True

    handler.raise_error = False
    with pytest.raises(_MaidaAbortSignal):
        handler.on_chat_model_start({"id": ["ChatFake"]}, [[]], run_id="llm-2")
    assert handler.raise_error is True

    handler.raise_error = False
    with pytest.raises(_MaidaAbortSignal):
        handler.on_tool_start({"name": "t"}, "{}", run_id="tool-1")
    assert handler.raise_error is True


@pytest.mark.skipif(LANGCHAIN_MISSING, reason="langchain_core not installed")
def test_langchain_handler_on_llm_error_propagates_guardrail(temp_data_dir):
    """GuardrailExceeded raised inside on_llm_error wraps in
    _MaidaAbortSignal so it bypasses framework error handling and
    propagates to _run_context which unwraps it to LoopAbort."""
    handler = LangChainCallbackHandler()

    @trace(stop_on_loop=True, stop_on_loop_min_repetitions=3)
    def _run():
        for i in range(6):
            _simulate_langchain_handle_event(
                handler,
                "on_llm_start",
                {"id": ["ChatFake"]},
                ["prompt"],
                run_id=f"llm-{i}",
            )
            _simulate_langchain_handle_event(
                handler,
                "on_llm_error",
                ValueError("model failed"),
                run_id=f"llm-{i}",
            )

    with pytest.raises(LoopAbort):
        _run()

    assert handler.raise_error is True
    assert handler.abort_exception is not None
