"""
Tests for LangChain integration. Skip if langchain is not installed.
Uses temp dir; no network calls. Asserts TOOL_CALL and LLM_CALL events.
"""

import logging
import sys

import pytest
from tests.conftest import get_latest_run_id

from maida import trace
from maida.config import load_config
from maida.events import EventType, spans_to_events
from maida.exceptions import (
    GuardrailExceeded,
    LoopAbort,
    _MaidaAbortSignal,
)
from maida.storage import load_spans

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
        assert "[langchain]" in msg, (
            f"message should mention extra [langchain]: {msg!r}"
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
    """With langchain installed, traced run with handler produces TOOL_CALL and LLM_CALL."""
    _traced_with_handler()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = spans_to_events(load_spans(run_id, config))

    tool_events = [
        e for e in events if e.get("event_type") == EventType.TOOL_CALL.value
    ]
    llm_events = [e for e in events if e.get("event_type") == EventType.LLM_CALL.value]

    assert len(tool_events) >= 1, "expected at least one TOOL_CALL"
    assert len(llm_events) >= 1, "expected at least one LLM_CALL"

    tool_payload = tool_events[0].get("payload", {})
    assert tool_payload.get("tool_name"), "TOOL_CALL should have tool_name"
    assert tool_payload.get("status") == "ok"

    llm_payload = llm_events[0].get("payload", {})
    assert llm_payload.get("model") is not None or "model" in llm_payload, (
        "LLM_CALL should have model"
    )


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
