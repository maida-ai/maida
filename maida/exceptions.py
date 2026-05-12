"""
AgentDbg guardrail exceptions.

Raised when a run guardrail threshold is exceeded; lifecycle records ERROR + RUN_END
and re-raises so the caller can handle the abort.

There are two types of exceptions:
- Private / internal use only: Prefixed with "_Maida"
- Public / user-facing / part of the public API: Not prefixed with anything.
  Have a descriptive name, and have no mention of "Maida" in the name.
"""

from maida._utils import deprecated


class MaidaException(Exception):
    """Base exception for all Maida exceptions."""

    pass


class _MaidaAbortSignal(MaidaException):
    """Internal BaseException used by integration handlers to bypass framework
    error handling (e.g. LangGraph's ``except Exception``).

    Not part of the public API.  The ``_run_context`` lifecycle catches this
    signal, records ERROR + RUN_END, and re-raises the wrapped
    `GuardrailExceeded` so callers see the normal exception type.
    """

    def __init__(self, cause: "GuardrailExceeded") -> None:
        super().__init__(str(cause))
        self.cause = cause


class GuardrailExceeded(MaidaException):
    """
    Raised when a guardrail limit is exceeded (stop_on_loop, max_llm_calls, etc.).

    Attributes:
        guardrail: Identifier of the guardrail that fired (e.g. "stop_on_loop", "max_llm_calls").
        threshold: Configured limit that was exceeded.
        actual: Current value that exceeded the threshold.
        message: Human-readable description.
    """

    def __init__(
        self,
        guardrail: str,
        threshold: int | float,
        actual: int | float,
        message: str,
    ) -> None:
        super().__init__(message)
        self.guardrail = guardrail
        self.threshold = threshold
        self.actual = actual
        self.message = message


class LoopAbort(GuardrailExceeded):
    """
    Raised when stop_on_loop is enabled and loop detection fires above the threshold.

    Subclass of GuardrailExceeded so callers can catch loop aborts specifically.
    """

    def __init__(
        self,
        threshold: int,
        actual: int,
        message: str,
    ) -> None:
        super().__init__(
            guardrail="stop_on_loop",
            threshold=threshold,
            actual=actual,
            message=message,
        )


########################
# Backward compatibility (deprecated names; warn on direct use only)


def _make_deprecated_exception(
    old_name: str,
    new_cls: type[BaseException],
) -> type[BaseException]:
    message = (
        f"'{old_name}' is deprecated and will be removed in a future version. "
        f"Use '{new_cls.__name__}' instead."
    )

    @deprecated(message, stacklevel=2)
    class DeprecatedException(new_cls):
        pass

    DeprecatedException.__name__ = old_name
    DeprecatedException.__qualname__ = old_name
    DeprecatedException.__module__ = __name__

    return DeprecatedException


_AgentDbgAbortSignal = _make_deprecated_exception(
    "_AgentDbgAbortSignal", _MaidaAbortSignal
)


AgentDbgGuardrailExceeded = _make_deprecated_exception(
    "AgentDbgGuardrailExceeded", GuardrailExceeded
)


# Multiple inheritance keeps legacy isinstance(exc, AgentDbgGuardrailExceeded) true
# for loop aborts (lifecycle records guardrail fields on that branch only).
AgentDbgLoopAbort = _make_deprecated_exception("AgentDbgLoopAbort", LoopAbort)
