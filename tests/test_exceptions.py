"""
Exceptions tests.

Covers Maida exception hierarchy and init behavior used by lifecycle/guardrails.
"""

import pytest

from maida.exceptions import (
    _MaidaAbortSignal,
    GuardrailExceeded,
    LoopAbort,
    MaidaException,
)

# Backwards compatibility imports
from maida.exceptions import (
    _AgentDbgAbortSignal,
    AgentDbgGuardrailExceeded,
    AgentDbgLoopAbort,
)


def test_maida_exception_is_base_exception():
    """MaidaException is the package root error type."""

    exc = MaidaException("boom")
    assert str(exc) == "boom"
    assert isinstance(exc, Exception)


def test_guardrail_exceeded_initializer_and_attrs():
    exc = GuardrailExceeded(
        guardrail="max_llm_calls",
        threshold=10,
        actual=11,
        message="Too many LLM calls",
    )
    assert str(exc) == "Too many LLM calls"
    assert exc.guardrail == "max_llm_calls"
    assert exc.threshold == 10
    assert exc.actual == 11
    assert exc.message == "Too many LLM calls"


def test_guardrail_exceeded_accepts_numeric_thresholds():
    exc = GuardrailExceeded(
        guardrail="max_duration",
        threshold=60.5,
        actual=61.2,
        message="Ran too long",
    )
    assert exc.threshold == pytest.approx(60.5)
    assert exc.actual == pytest.approx(61.2)


def test_loop_abort_sets_guardrail_and_inherits_guardrail_base():
    exc = LoopAbort(threshold=2, actual=5, message="loop detected")

    assert exc.guardrail == "stop_on_loop"
    assert exc.threshold == 2
    assert exc.actual == 5
    assert exc.message == "loop detected"
    assert str(exc) == "loop detected"
    assert isinstance(exc, GuardrailExceeded)
    assert isinstance(exc, MaidaException)


def test_maida_abort_signal_wraps_cause_and_str_matches_cause():
    cause = GuardrailExceeded(
        guardrail="max_tool_calls",
        threshold=3,
        actual=4,
        message="tool budget",
    )
    sig = _MaidaAbortSignal(cause)

    assert sig.cause is cause
    assert str(sig) == str(cause)
    assert isinstance(sig, BaseException)
    assert not isinstance(sig, Exception)


def test_exception_hierarchy():
    guardrail_ex = GuardrailExceeded("g", 1, 2, "m")
    loop_ex = LoopAbort(1, 2, "m")

    assert issubclass(GuardrailExceeded, MaidaException)
    assert issubclass(LoopAbort, GuardrailExceeded)

    assert isinstance(guardrail_ex, MaidaException)
    assert isinstance(loop_ex, GuardrailExceeded)


########################
# Backward compatibility


def test_abort_signal_is_deprecated():
    """Lifecycle/integrations import _AgentDbgAbortSignal"""
    assert issubclass(_AgentDbgAbortSignal, _MaidaAbortSignal)
    with pytest.warns(DeprecationWarning):
        _AgentDbgAbortSignal("cause")


def test_guardrail_exceeded_is_deprecated():
    """Lifecycle/integrations import AgentDbgGuardrailExceeded"""
    assert issubclass(AgentDbgGuardrailExceeded, GuardrailExceeded)
    with pytest.warns(DeprecationWarning):
        AgentDbgGuardrailExceeded("g", 1, 2, "m")


def test_loop_abort_is_deprecated():
    """Lifecycle/integrations import AgentDbgLoopAbort"""
    assert issubclass(AgentDbgLoopAbort, LoopAbort)
    assert issubclass(AgentDbgLoopAbort, GuardrailExceeded)
    with pytest.warns(DeprecationWarning):
        AgentDbgLoopAbort(1, 2, "m")
