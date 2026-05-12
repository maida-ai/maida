"""
OpenAI Agents SDK tracing integration.

Import to activate:

```python
from maida import trace
from maida.integrations import openai_agents  # registers hooks


@trace
def run():
    # ... OpenAI Agents SDK code ...
    pass
```

The adapter listens to OpenAI Agents tracing spans and translates completed
generation, function, and handoff spans into AgentDbg `record_*` calls.
"""

from typing import Any

from maida import has_active_run, record_llm_call, record_tool_call
from maida.exceptions import GuardrailExceeded, _MaidaAbortSignal
from maida.integrations._error import MissingOptionalDependencyError

try:
    import agents.tracing as agents_tracing
    from agents.tracing import add_trace_processor
    from agents.tracing.processor_interface import TracingProcessor
    from agents.tracing.span_data import (
        FunctionSpanData,
        GenerationSpanData,
        HandoffSpanData,
    )
except ImportError as e:
    raise MissingOptionalDependencyError(
        "OpenAI Agents integration requires optional deps. "
        "Install with `pip install maida[openai]`."
    ) from e

_PROCESSOR_ATTR = "_maida_openai_agents_processor"


def _span_error_to_maida_error(span_error: Any) -> dict[str, Any] | str | None:
    """Normalize the SDK span error shape to Maida's error payload contract."""
    if span_error is None:
        return None
    if isinstance(span_error, dict):
        details = span_error.get("data")
        return {
            "error_type": span_error.get("error_type", "OpenAIAgentsSpanError"),
            "message": span_error.get("message", ""),
            "details": details,
            "stack": span_error.get("stack"),
        }
    if isinstance(span_error, BaseException):
        return span_error
    if isinstance(span_error, str):
        return {
            "error_type": "OpenAIAgentsSpanError",
            "message": span_error,
            "details": None,
            "stack": None,
        }
    return {
        "error_type": "OpenAIAgentsSpanError",
        "message": str(span_error),
        "details": None,
        "stack": None,
    }


def _status_from_span_error(span_error: Any) -> str:
    return "error" if span_error is not None else "ok"


def _base_meta(span: Any, span_type: str) -> dict[str, Any]:
    """Collect framework-specific details under meta.openai_agents.*."""
    openai_meta: dict[str, Any] = {
        "span_type": span_type,
        "trace_id": getattr(span, "trace_id", None),
        "span_id": getattr(span, "span_id", None),
        "parent_id": getattr(span, "parent_id", None),
        "started_at": getattr(span, "started_at", None),
        "ended_at": getattr(span, "ended_at", None),
    }
    trace_metadata = getattr(span, "trace_metadata", None)
    if trace_metadata is not None:
        openai_meta["trace_metadata"] = trace_metadata
    return {"framework": "openai_agents", "openai_agents": openai_meta}


class AgentDbgOpenAIAgentsTracingProcessor(TracingProcessor):
    """Translate completed OpenAI Agents spans into Maida recorders.

    When a guardrail fires, the processor raises ``_MaidaAbortSignal``
    (a ``BaseException``) so it bypasses the SDK's ``except Exception``
    handler and propagates to the ``traced_run`` / ``@trace`` context
    manager, which records ERROR + RUN_END and re-raises the wrapped
    ``GuardrailExceeded``.

    As a defensive fallback the exception is also stored on
    ``abort_exception`` so callers can check ``raise_if_aborted()`` after
    ``Runner.run()`` if needed.
    """

    def __init__(self) -> None:
        self._abort_exception: GuardrailExceeded | None = None

    @property
    def abort_exception(self) -> GuardrailExceeded | None:
        """The guardrail exception if one fired during the last run, or None."""
        return self._abort_exception

    def raise_if_aborted(self) -> None:
        """Re-raise the guardrail exception if one was captured during the last run."""
        if self._abort_exception is not None:
            raise self._abort_exception

    def reset(self) -> None:
        """Clear abort state so the processor can be reused for a new run."""
        self._abort_exception = None

    def on_trace_start(self, trace: Any) -> None:
        self._abort_exception = None

    def on_trace_end(self, trace: Any) -> None:
        return None

    def on_span_start(self, span: Any) -> None:
        if self._abort_exception is not None:
            raise _MaidaAbortSignal(self._abort_exception)

    def on_span_end(self, span: Any) -> None:
        if self._abort_exception is not None:
            raise _MaidaAbortSignal(self._abort_exception)

        if not has_active_run():
            return

        span_data = getattr(span, "span_data", None)
        span_error = getattr(span, "error", None)
        status = _status_from_span_error(span_error)
        error = _span_error_to_maida_error(span_error)

        try:
            if isinstance(span_data, GenerationSpanData):
                meta = _base_meta(span, "generation")
                if span_data.model_config is not None:
                    meta["openai_agents"]["model_config"] = span_data.model_config
                record_llm_call(
                    model=span_data.model or "unknown",
                    prompt=span_data.input,
                    response=span_data.output,
                    usage=span_data.usage,
                    meta=meta,
                    provider="openai",
                    status=status,
                    error=error,
                )
                return

            if isinstance(span_data, FunctionSpanData):
                meta = _base_meta(span, "function")
                if span_data.mcp_data is not None:
                    meta["openai_agents"]["mcp_data"] = span_data.mcp_data
                record_tool_call(
                    name=span_data.name or "unknown",
                    args=span_data.input,
                    result=span_data.output,
                    meta=meta,
                    status=status,
                    error=error,
                )
                return

            if isinstance(span_data, HandoffSpanData):
                meta = _base_meta(span, "handoff")
                meta["openai_agents"]["handoff"] = {
                    "from_agent": span_data.from_agent,
                    "to_agent": span_data.to_agent,
                }
                record_tool_call(
                    name="handoff",
                    args=None,
                    result=None,
                    meta=meta,
                    status=status,
                    error=error,
                )
        except GuardrailExceeded as e:
            self._abort_exception = e
            raise _MaidaAbortSignal(e) from e

    def shutdown(self) -> None:
        return None

    def force_flush(self) -> None:
        return None


def _register_processor() -> AgentDbgOpenAIAgentsTracingProcessor:
    """Register the adapter once per process, even across module reloads."""
    existing = getattr(agents_tracing, _PROCESSOR_ATTR, None)
    if existing is not None:
        return existing

    processor = AgentDbgOpenAIAgentsTracingProcessor()
    add_trace_processor(processor)
    setattr(agents_tracing, _PROCESSOR_ATTR, processor)
    return processor


PROCESSOR = _register_processor()

__all__ = ["AgentDbgOpenAIAgentsTracingProcessor", "PROCESSOR"]
