"""
Public re-export shim for the _tracing module.

This module provides the public API for Maida tracing:
- trace: Decorator that starts a new run as an OTel trace.
- traced_run: Context manager that starts a new OTel trace.
- has_active_run: Check if a trace is active in the current context.
- record_llm_call: Record an LLM call as an OTel GenAI span.
- record_tool_call: Record a tool call as an OTel span.
- record_state: Record a state update as an event on the current span.
"""

from maida._tracing import (
    trace,
    traced_run,
    has_active_run,
    record_llm_call,
    record_tool_call,
    record_state,
)

__all__ = [
    "trace",
    "traced_run",
    "has_active_run",
    "record_llm_call",
    "record_tool_call",
    "record_state",
]
