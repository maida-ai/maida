"""Maida: pre-merge behavioral regression gate for AI agents."""

__path__ = __import__("pkgutil").extend_path(__path__, __name__)

from maida.exceptions import GuardrailExceeded, LoopAbort
from maida.tracing import (
    has_active_run,
    record_llm_call,
    record_state,
    record_tool_call,
    trace,
    traced_run,
)

try:
    from maida._version import version as __version__
except ImportError:
    # No version file was venerated; use dev default
    __version__ = "0.0.0dev+default"

__all__ = [
    "GuardrailExceeded",
    "LoopAbort",
    "trace",
    "traced_run",
    "has_active_run",
    "record_llm_call",
    "record_tool_call",
    "record_state",
    "__version__",
]
