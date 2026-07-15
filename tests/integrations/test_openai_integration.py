"""
Deterministic tests for the OpenAI Agents SDK integration.

The real `openai-agents` package is optional. These tests install a fake
`agents.tracing` surface in `sys.modules`, then assert the adapter registers on
import and translates spans into Maida events.
"""

import importlib
import secrets
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from maida import trace
from maida.baseline import extract_run_metrics
from maida.config import load_config
from maida.constants import REDACTED_MARKER, TRUNCATED_MARKER
from maida.events import EventType
from maida.integrations._error import MissingOptionalDependencyError
from maida.storage import list_runs, load_run_for_analysis
from tests.conftest import get_latest_run_id


def _drop_openai_agents_integration_modules() -> None:
    sys.modules.pop("maida.integrations.openai_agents", None)
    sys.modules.pop("maida.integrations", None)


def _make_fake_agents_modules() -> dict[str, ModuleType]:
    agents_module = ModuleType("agents")
    agents_module.__path__ = []  # type: ignore[attr-defined]

    tracing_module = ModuleType("agents.tracing")
    span_data_module = ModuleType("agents.tracing.span_data")
    processor_module = ModuleType("agents.tracing.processor_interface")

    class TracingProcessor:
        def on_trace_start(self, trace):
            return None

        def on_trace_end(self, trace):
            return None

        def on_span_start(self, span):
            return None

        def on_span_end(self, span):
            return None

        def shutdown(self):
            return None

        def force_flush(self):
            return None

    class GenerationSpanData:
        def __init__(
            self,
            input=None,
            output=None,
            model=None,
            model_config=None,
            usage=None,
        ):
            self.input = input
            self.output = output
            self.model = model
            self.model_config = model_config
            self.usage = usage

    class FunctionSpanData:
        def __init__(self, name, input=None, output=None, mcp_data=None):
            self.name = name
            self.input = input
            self.output = output
            self.mcp_data = mcp_data

    class HandoffSpanData:
        def __init__(self, from_agent=None, to_agent=None):
            self.from_agent = from_agent
            self.to_agent = to_agent

    tracing_module._processors = []

    def add_trace_processor(processor):
        tracing_module._processors.append(processor)

    def emit_span(span):
        for processor in list(tracing_module._processors):
            processor.on_span_end(span)

    tracing_module.add_trace_processor = add_trace_processor
    tracing_module.emit_span = emit_span
    tracing_module.TracingProcessor = TracingProcessor

    span_data_module.GenerationSpanData = GenerationSpanData
    span_data_module.FunctionSpanData = FunctionSpanData
    span_data_module.HandoffSpanData = HandoffSpanData
    processor_module.TracingProcessor = TracingProcessor

    agents_module.tracing = tracing_module

    return {
        "agents": agents_module,
        "agents.tracing": tracing_module,
        "agents.tracing.span_data": span_data_module,
        "agents.tracing.processor_interface": processor_module,
    }


def _fake_span(span_data, *, error=None, parent_id=None):
    return SimpleNamespace(
        span_data=span_data,
        error=error,
        trace_id="trace_1234567890abcdef1234567890abcd",
        span_id="span_123",
        parent_id=parent_id,
        trace_metadata={"source": "test"},
        started_at="2026-03-08T12:00:00.000Z",
        ended_at="2026-03-08T12:00:00.100Z",
    )


@pytest.fixture(autouse=True)
def clear_openai_integration_imports():
    _drop_openai_agents_integration_modules()
    yield
    _drop_openai_agents_integration_modules()


@pytest.fixture
def openai_agents_module():
    fake_modules = _make_fake_agents_modules()
    with patch.dict(sys.modules, fake_modules):
        import maida.integrations.openai_agents as openai_agents

        yield (
            openai_agents,
            fake_modules["agents.tracing"],
            fake_modules["agents.tracing.span_data"],
        )


def test_import_without_optional_dependency_raises_clear_error():
    to_restore = {}
    for key in list(sys.modules.keys()):
        if key == "agents" or key.startswith("agents."):
            to_restore[key] = sys.modules.pop(key, None)

    fake_agents = ModuleType("agents")

    try:
        with patch.dict(sys.modules, {"agents": fake_agents}, clear=False):
            with pytest.raises(MissingOptionalDependencyError) as exc_info:
                import maida.integrations.openai_agents  # noqa: F401
    finally:
        sys.modules.pop("agents", None)
        for key, value in to_restore.items():
            if value is not None:
                sys.modules[key] = value

    assert "OpenAI Agents" in str(exc_info.value)
    assert "maida-ai[openai]" in str(exc_info.value)


def test_openai_integration_does_not_break_core_import():
    """Core maida import must not crash when OpenAI Agents deps are missing."""
    agents_module = ModuleType("agents")

    with patch.dict(sys.modules, {"agents": agents_module}, clear=False):
        import maida

    assert maida.__version__


def test_import_registers_processor_once(openai_agents_module):
    openai_agents, tracing_module, _ = openai_agents_module

    assert len(tracing_module._processors) == 1

    importlib.reload(openai_agents)

    assert len(tracing_module._processors) == 1


def test_generation_span_is_ignored_without_active_run(openai_agents_module):
    openai_agents, tracing_module, span_data = openai_agents_module
    span = _fake_span(
        span_data.GenerationSpanData(
            input=[{"role": "user", "content": "hello"}],
            output=[{"role": "assistant", "content": "hi"}],
            model="gpt-4o-mini",
            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        )
    )

    with patch.object(openai_agents, "record_llm_call", MagicMock()) as record_llm:
        tracing_module.emit_span(span)

    record_llm.assert_not_called()


def test_generation_span_records_llm_call_event(openai_agents_module):
    openai_agents, tracing_module, span_data = openai_agents_module

    span = _fake_span(
        span_data.GenerationSpanData(
            input=[{"role": "user", "content": "Summarize this"}],
            output=[{"role": "assistant", "content": "Summary"}],
            model="gpt-4o-mini",
            model_config={"temperature": 0.2},
            usage={
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18,
            },
        )
    )

    with patch.object(openai_agents, "has_active_run", return_value=True):
        with patch.object(openai_agents, "record_llm_call", MagicMock()) as record_llm:
            tracing_module.emit_span(span)

    record_llm.assert_called_once()
    kw = record_llm.call_args.kwargs
    assert kw["model"] == "gpt-4o-mini"
    assert kw["prompt"] == [{"role": "user", "content": "Summarize this"}]
    assert kw["response"] == [{"role": "assistant", "content": "Summary"}]
    assert kw["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }
    assert kw["provider"] == "openai"
    assert kw["status"] == "ok"
    meta = kw["meta"]
    assert meta["framework"] == "openai_agents"
    assert meta["openai_agents"]["span_type"] == "generation"
    assert meta["openai_agents"]["model_config"] == {"temperature": 0.2}


def test_openai_agents_success_path_persists_structural_signature(
    openai_agents_module, temp_data_dir
):
    """The offline success path persists the exact normalized signature."""
    _, tracing_module, span_data = openai_agents_module

    @trace(name="openai-agents-conformance-success")
    def run_success_path():
        tracing_module.emit_span(
            _fake_span(
                span_data.GenerationSpanData(
                    input=[{"role": "user", "content": "Summarize this"}],
                    output=[{"role": "assistant", "content": "Summary"}],
                    model="gpt-4o-mini",
                    model_config={"temperature": 0.0},
                    usage={
                        "prompt_tokens": 11,
                        "completion_tokens": 7,
                        "total_tokens": 18,
                    },
                )
            )
        )
        tracing_module.emit_span(
            _fake_span(
                span_data.FunctionSpanData(
                    name="search_docs",
                    input={"query": "maida"},
                    output={"hits": 2},
                )
            )
        )
        tracing_module.emit_span(
            _fake_span(
                span_data.HandoffSpanData(
                    from_agent="router_agent", to_agent="search_agent"
                )
            )
        )

    run_success_path()

    config = load_config()
    run_id = get_latest_run_id(config)
    resolved_id, meta, events = load_run_for_analysis(run_id, config)
    metrics = extract_run_metrics(meta, events)

    assert resolved_id == run_id
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        EventType.LLM_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.RUN_END.value,
    ]
    duration_ms = metrics["summary"]["duration_ms"]
    assert isinstance(duration_ms, int)
    assert duration_ms >= 0
    assert {**metrics["summary"], "duration_ms": 0} == {
        "status": "ok",
        "total_events": 3,
        "llm_calls": 1,
        "tool_calls": 2,
        "errors": 0,
        "loop_warnings": 0,
        "duration_ms": 0,
        "total_tokens": 18,
    }
    assert metrics["tool_path"] == ["handoff", "search_docs"]
    assert metrics["tool_call_sequence"] == ["search_docs", "handoff"]
    assert metrics["tool_call_counts"] == {"search_docs": 1, "handoff": 1}
    assert metrics["llm_models_used"] == ["gpt-4o-mini"]
    assert metrics["event_type_sequence"] == [
        EventType.RUN_START.value,
        EventType.LLM_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.RUN_END.value,
    ]
    assert metrics["final_status"] == "ok"
    assert events[1]["payload"]["status"] == "ok"
    assert events[2]["payload"]["tool_name"] == "search_docs"
    assert events[2]["payload"]["status"] == "ok"
    assert events[3]["payload"]["tool_name"] == "handoff"
    assert events[3]["payload"]["status"] == "ok"
    assert events[-1]["payload"] == {"status": "ok"}


def test_function_and_handoff_spans_record_tool_call_events(openai_agents_module):
    openai_agents, tracing_module, span_data = openai_agents_module

    function_span = _fake_span(
        span_data.FunctionSpanData(
            name="search_docs",
            input={"query": "maida"},
            output={"hits": 2},
            mcp_data={"server": "docs"},
        ),
        parent_id="span_parent",
    )
    handoff_span = _fake_span(
        span_data.HandoffSpanData(from_agent="router_agent", to_agent="search_agent")
    )

    with patch.object(openai_agents, "has_active_run", return_value=True):
        with patch.object(
            openai_agents, "record_tool_call", MagicMock()
        ) as record_tool:
            tracing_module.emit_span(function_span)
            tracing_module.emit_span(handoff_span)

    assert record_tool.call_count == 2

    function_kw = record_tool.call_args_list[0].kwargs
    assert function_kw["name"] == "search_docs"
    assert function_kw["args"] == {"query": "maida"}
    assert function_kw["result"] == {"hits": 2}
    assert function_kw["status"] == "ok"
    function_meta = function_kw["meta"]
    assert function_meta["framework"] == "openai_agents"
    assert function_meta["openai_agents"]["span_type"] == "function"
    assert function_meta["openai_agents"]["mcp_data"] == {"server": "docs"}
    assert function_meta["openai_agents"]["parent_id"] == "span_parent"

    handoff_kw = record_tool.call_args_list[1].kwargs
    assert handoff_kw["name"] == "handoff"
    assert handoff_kw["args"] is None
    assert handoff_kw["result"] is None
    assert handoff_kw["status"] == "ok"
    handoff_meta = handoff_kw["meta"]
    assert handoff_meta["openai_agents"]["span_type"] == "handoff"
    assert handoff_meta["openai_agents"]["handoff"] == {
        "from_agent": "router_agent",
        "to_agent": "search_agent",
    }


def test_generation_error_records_error_status(openai_agents_module):
    openai_agents, tracing_module, span_data = openai_agents_module

    span = _fake_span(
        span_data.GenerationSpanData(
            input=[{"role": "user", "content": "fail"}],
            output=None,
            model="gpt-4o-mini",
            usage=None,
        ),
        error={"message": "model failed", "data": {"code": "boom"}},
    )

    with patch.object(openai_agents, "has_active_run", return_value=True):
        with patch.object(openai_agents, "record_llm_call", MagicMock()) as record_llm:
            tracing_module.emit_span(span)

    record_llm.assert_called_once()
    kw = record_llm.call_args.kwargs
    assert kw["status"] == "error"
    assert kw["error"]["error_type"] == "OpenAIAgentsSpanError"
    assert kw["error"]["message"] == "model failed"
    assert kw["error"]["details"] == {"code": "boom"}


@pytest.mark.parametrize(
    ("span_kind", "event_type", "message"),
    [
        ("generation", EventType.LLM_CALL.value, "model failed"),
        ("function", EventType.TOOL_CALL.value, "tool failed"),
    ],
)
def test_openai_agents_errors_persist_on_normalized_calls(
    openai_agents_module,
    temp_data_dir,
    span_kind,
    event_type,
    message,
):
    """Contained SDK failures stay on normalized calls without duplicate ERROR."""
    _, tracing_module, span_data = openai_agents_module
    if span_kind == "generation":
        data = span_data.GenerationSpanData(
            input=[{"role": "user", "content": "fail"}],
            output=None,
            model="gpt-4o-mini",
        )
    else:
        data = span_data.FunctionSpanData(
            name="search_docs",
            input={"query": "maida"},
            output=None,
        )
    span = _fake_span(
        data,
        error={
            "error_type": "OpenAIAgentsSpanError",
            "message": message,
            "data": {"code": "boom"},
        },
    )

    @trace(name=f"openai-agents-{span_kind}-error")
    def run_failed_call():
        tracing_module.emit_span(span)

    run_failed_call()

    config = load_config()
    run_id = get_latest_run_id(config)
    _, meta, events = load_run_for_analysis(run_id, config)

    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        event_type,
        EventType.RUN_END.value,
    ]
    payload = events[1]["payload"]
    assert payload["status"] == "error"
    assert payload["error"] == {
        "error_type": "OpenAIAgentsSpanError",
        "message": message,
        "stack": None,
    }
    assert meta["status"] == "ok"
    assert meta["counts"]["errors"] == 0
    assert events[-1]["payload"] == {"status": "ok"}


def test_openai_agents_payloads_are_sanitized_before_persistence(
    openai_agents_module, temp_data_dir, monkeypatch
):
    """Adapter payloads use Maida's redaction/truncation storage boundary."""
    openai_agents, _, span_data = openai_agents_module
    secret = secrets.token_hex(24)
    oversized = "public-" + ("x" * 200)
    monkeypatch.setenv("MAIDA_REDACT_KEYS", "api_key,message,stack")
    monkeypatch.setenv("MAIDA_MAX_FIELD_BYTES", "80")
    processor = openai_agents.OpenAIAgentsTracingProcessor()

    @trace(name="openai agents privacy")
    def _run():
        processor.on_span_end(
            _fake_span(
                span_data.GenerationSpanData(
                    input={"api_key": secret, "text": oversized},
                    output={"api_key": secret, "text": oversized},
                    model="gpt-private",
                )
            )
        )
        processor.on_span_end(
            _fake_span(
                span_data.FunctionSpanData(
                    name="private_tool",
                    input={"api_key": secret, "query": oversized},
                    output=None,
                ),
                error={
                    "message": secret,
                    "data": {"api_key": secret, "diagnostic": oversized},
                    "stack": secret,
                },
            )
        )

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


def test_calls_outside_run_do_not_create_or_contaminate_run(
    openai_agents_module, temp_data_dir, monkeypatch
):
    _, tracing_module, span_data = openai_agents_module
    monkeypatch.delenv("MAIDA_IMPLICIT_RUN", raising=False)

    outside_span = _fake_span(
        span_data.FunctionSpanData(
            name="outside_tool",
            input={"query": "maida"},
            output={"hits": 2},
        )
    )
    tracing_module.emit_span(outside_span)

    config = load_config()
    assert list_runs(limit=10, config=config) == []

    @trace(name="openai-agents-inside-run")
    def run_inside_call():
        tracing_module.emit_span(
            _fake_span(
                span_data.FunctionSpanData(
                    name="inside_tool",
                    input={"query": "maida"},
                    output={"hits": 1},
                )
            )
        )

    run_inside_call()

    runs = list_runs(limit=10, config=config)
    assert len(runs) == 1
    _, meta, events = load_run_for_analysis(runs[0]["trace_id"], config)
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


def test_guardrail_exception_captured_on_abort_exception(openai_agents_module):
    """When a guardrail fires in on_span_end, the exception is stored and
    _MaidaAbortSignal (BaseException) is raised to bypass framework error handling."""
    openai_agents, tracing_module, span_data = openai_agents_module
    from maida.exceptions import LoopAbort, _MaidaAbortSignal

    processor = openai_agents.PROCESSOR
    assert processor.abort_exception is None

    exc = LoopAbort(threshold=3, actual=3, message="stop_on_loop test")

    span = _fake_span(
        span_data.GenerationSpanData(input="hello", output="world", model="gpt-4o-mini")
    )

    def fake_record_llm_call(**kwargs):
        raise exc

    with patch.object(openai_agents, "has_active_run", return_value=True):
        with patch.object(
            openai_agents, "record_llm_call", side_effect=fake_record_llm_call
        ):
            with pytest.raises(_MaidaAbortSignal) as sig_info:
                tracing_module.emit_span(span)

    assert isinstance(sig_info.value.cause, LoopAbort)
    assert processor.abort_exception is exc
    with pytest.raises(LoopAbort):
        processor.raise_if_aborted()


def test_abort_exception_resets_on_new_trace(openai_agents_module):
    """on_trace_start resets abort_exception so a reused processor is clean."""
    openai_agents, _, _ = openai_agents_module
    from maida.exceptions import LoopAbort

    processor = openai_agents.PROCESSOR
    processor._abort_exception = LoopAbort(threshold=3, actual=3, message="old")

    processor.on_trace_start(SimpleNamespace(trace_id="new_trace"))
    assert processor.abort_exception is None


# ---------------------------------------------------------------------------
# End-to-end LOOP_WARNING deduplication regression test
# ---------------------------------------------------------------------------


def test_loop_warning_dedup_with_openai_agents_adapter(
    openai_agents_module, temp_data_dir
):
    """When stop_on_loop fires inside the OpenAI Agents adapter, the
    _MaidaAbortSignal (BaseException) bypasses the SDK's except Exception
    and propagates to _run_context, which records ERROR + RUN_END and
    re-raises LoopAbort.  The loop stops immediately."""
    openai_agents, tracing_module, span_data = openai_agents_module
    from maida.exceptions import LoopAbort

    processor = openai_agents.PROCESSOR

    iterations_completed = 0

    @trace(
        name="openai-agents-dedup-regression",
        stop_on_loop=True,
        stop_on_loop_min_repetitions=3,
    )
    def run_openai_agents_looping():
        nonlocal iterations_completed
        processor.on_trace_start(SimpleNamespace(trace_id="trace_dedup_test"))
        for _ in range(10):
            tracing_module.emit_span(
                _fake_span(
                    span_data.GenerationSpanData(
                        input=[{"role": "user", "content": "Find sales"}],
                        output=[{"role": "assistant", "content": "search again"}],
                        model="gpt-4o-mini",
                        usage={
                            "prompt_tokens": 6,
                            "completion_tokens": 6,
                            "total_tokens": 12,
                        },
                    )
                )
            )
            tracing_module.emit_span(
                _fake_span(
                    span_data.FunctionSpanData(
                        name="search",
                        input={"query": "quarterly sales"},
                        output={"data": "Q1: 1.2M"},
                    )
                )
            )
            iterations_completed += 1

    with pytest.raises(LoopAbort):
        run_openai_agents_looping()

    assert iterations_completed < 10, (
        f"loop should have been stopped by guardrail, but completed "
        f"{iterations_completed}/10 iterations"
    )

    config = load_config()
    run_id = get_latest_run_id(config)
    _, run_meta, events = load_run_for_analysis(run_id, config)

    loop_warnings = [
        e for e in events if e.get("event_type") == EventType.LOOP_WARNING.value
    ]
    patterns = {e["payload"]["pattern"] for e in loop_warnings}
    assert len(loop_warnings) == len(patterns), (
        f"each distinct pattern should emit exactly one LOOP_WARNING; "
        f"got {len(loop_warnings)} warnings for {len(patterns)} patterns: {patterns}"
    )
    assert len(loop_warnings) >= 1
    assert run_meta["counts"]["loop_warnings"] == len(loop_warnings)

    errors = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    assert len(errors) == 1
    assert errors[0]["payload"]["error_type"] == "LoopAbort"
    assert [event["event_type"] for event in events[-3:]] == [
        EventType.LOOP_WARNING.value,
        EventType.ERROR.value,
        EventType.RUN_END.value,
    ]
    assert run_meta["status"] == "error"
    assert events[-1]["payload"] == {"status": "error"}
