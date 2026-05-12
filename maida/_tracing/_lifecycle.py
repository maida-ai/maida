"""
Run lifecycle: _run_context context manager, trace decorator, traced_run.
Depends: maida.config, maida.events, maida.exceptions, maida.guardrails, maida.storage, _redact, _context.
"""

import asyncio
import sys
import traceback
from types import TracebackType
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Generator, ParamSpec, TypeVar

from maida.config import load_config
from maida.constants import default_counts
from maida.events import EventType, new_event
from maida.exceptions import GuardrailExceeded, _MaidaAbortSignal
from maida.guardrails import GuardrailParams, merge_guardrail_params
from maida.storage import append_event, create_run, finalize_run

from maida._tracing._context import (
    _append_event_and_check_guardrails,
    _config_var,
    _counts_var,
    _event_count_var,
    _event_window_var,
    _guardrail_params_var,
    _loop_emitted_var,
    _resolve_run_name,
    _run_end_payload,
    _run_id_var,
    _run_start_payload_for_event,
    _started_at_var,
)
from maida._tracing._redact import _redact_and_truncate
from maida._integration_utils import _invoke_run_enter, _invoke_run_exit


P = ParamSpec("P")
R = TypeVar("R")


def _error_payload(exc: BaseException) -> dict[str, Any]:
    """Build ERROR payload."""
    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
        "stack": traceback.format_exc(),
    }


def _guardrail_error_payload(exc: GuardrailExceeded) -> dict[str, Any]:
    """Build ERROR payload for guardrail abort (includes guardrail, threshold, actual)."""
    return {
        "error_type": type(exc).__name__,
        "message": exc.message,
        "stack": traceback.format_exc(),
        "guardrail": exc.guardrail,
        "threshold": exc.threshold,
        "actual": exc.actual,
    }


@contextmanager
def _run_context(
    name: str | None = None,
    func: Callable[..., Any] | None = None,
    guardrail_params: GuardrailParams | None = None,
) -> Generator[None, None, None]:
    """
    Context manager that factors the common run lifecycle.
    If a run is already active, yields without creating a new run.
    Otherwise: load config, create run, set context vars, emit RUN_START,
    then on success emit RUN_END "ok" and finalize; on exception emit ERROR,
    RUN_END "error", finalize, and reraise. Reset context vars in finally.
    """
    existing_run_id = _run_id_var.get()
    if existing_run_id is not None:
        # Nested context: reuse the existing run.  If the caller supplied
        # guardrail_params (e.g. traced_run(stop_on_loop=True) inside @trace),
        # apply them for the duration of this block so they aren't silently
        # discarded.
        if guardrail_params is not None:
            token_nested = _guardrail_params_var.set(guardrail_params)
            try:
                yield
            finally:
                _guardrail_params_var.reset(token_nested)
        else:
            yield
        return

    config = load_config()
    params = guardrail_params if guardrail_params is not None else config.guardrails
    run_name = _resolve_run_name(name, func)
    meta = create_run(run_name, config)
    run_id = meta["run_id"]
    started_at = meta["started_at"]
    counts = default_counts()

    token_run = _run_id_var.set(run_id)
    token_counts = _counts_var.set(counts)
    token_config = _config_var.set(config)
    token_window = _event_window_var.set([])
    token_emitted = _loop_emitted_var.set(set())
    token_guardrail = _guardrail_params_var.set(params)
    token_started_at = _started_at_var.set(started_at)
    token_event_count = _event_count_var.set(0)
    exc_info: tuple[
        type[BaseException] | None, BaseException | None, TracebackType | None
    ] = (
        None,
        None,
        None,
    )

    def _finish_run(status: str) -> None:
        _invoke_run_exit(run_id, *exc_info)
        payload_end = _run_end_payload(status, counts, started_at)
        ev_end = new_event(EventType.RUN_END, run_id, "run_end", payload_end)
        append_event(run_id, ev_end, config)
        finalize_run(run_id, status, counts, config)

    try:
        payload = _run_start_payload_for_event(run_name, config)
        ev = new_event(EventType.RUN_START, run_id, run_name, payload)
        _append_event_and_check_guardrails(run_id, ev, config, counts)
        _invoke_run_enter()
        yield
    except _MaidaAbortSignal as signal:
        exc_info = sys.exc_info()
        cause = signal.cause
        err_payload = _redact_and_truncate(_guardrail_error_payload(cause), config)
        err_ev = new_event(EventType.ERROR, run_id, type(cause).__name__, err_payload)
        append_event(run_id, err_ev, config)
        counts["errors"] = counts.get("errors", 0) + 1
        _finish_run("error")
        raise cause from signal
    except GuardrailExceeded as e:
        exc_info = sys.exc_info()
        err_payload = _redact_and_truncate(_guardrail_error_payload(e), config)
        err_ev = new_event(EventType.ERROR, run_id, type(e).__name__, err_payload)
        append_event(run_id, err_ev, config)
        counts["errors"] = counts.get("errors", 0) + 1
        _finish_run("error")
        raise
    except Exception as e:
        exc_info = sys.exc_info()
        err_payload = _redact_and_truncate(_error_payload(e), config)
        err_ev = new_event(EventType.ERROR, run_id, type(e).__name__, err_payload)
        _append_event_and_check_guardrails(run_id, err_ev, config, counts)
        counts["errors"] = counts.get("errors", 0) + 1
        _finish_run("error")
        raise
    else:
        _finish_run("ok")
    finally:
        _run_id_var.reset(token_run)
        _counts_var.reset(token_counts)
        _config_var.reset(token_config)
        _event_window_var.reset(token_window)
        _loop_emitted_var.reset(token_emitted)
        _guardrail_params_var.reset(token_guardrail)
        _started_at_var.reset(token_started_at)
        _event_count_var.reset(token_event_count)


def trace(
    f: Callable[P, R] | str | None = None,
    *,
    name: str | None = None,
    stop_on_loop: bool | None = None,
    stop_on_loop_min_repetitions: int | None = None,
    max_llm_calls: int | None = None,
    max_tool_calls: int | None = None,
    max_events: int | None = None,
    max_duration_s: float | None = None,
) -> Callable[P, R] | Callable[[Callable[P, R]], Callable[P, R]]:
    """
    Decorator that starts a new run (RUN_START / RUN_END, ERROR on exception)
    when no run is active; otherwise runs the function in the existing run without
    creating a new run or emitting extra run events.

    Usage: @trace, @trace(), @trace("run name"), @trace(name="run name").
    Run name precedence: AGENTDBG_RUN_NAME env, then explicit name, then default (entrypoint - timestamp).
    Guardrail kwargs (stop_on_loop, max_llm_calls, etc.) override config; see SPEC §13.
    """

    def decorator(func: Callable[P, R], explicit: str | None = None) -> Callable[P, R]:
        _name = explicit if explicit is not None else name
        config = load_config()
        base = config.guardrails
        kw: dict[str, Any] = {}
        if stop_on_loop is not None:
            kw["stop_on_loop"] = stop_on_loop
        if stop_on_loop_min_repetitions is not None:
            kw["stop_on_loop_min_repetitions"] = stop_on_loop_min_repetitions
        if max_llm_calls is not None:
            kw["max_llm_calls"] = max_llm_calls
        if max_tool_calls is not None:
            kw["max_tool_calls"] = max_tool_calls
        if max_events is not None:
            kw["max_events"] = max_events
        if max_duration_s is not None:
            kw["max_duration_s"] = max_duration_s
        params = merge_guardrail_params(base, **kw)

        if asyncio.iscoroutinefunction(func):

            @wraps(func)
            async def async_inner(*args: P.args, **kwargs: P.kwargs) -> R:
                with _run_context(name=_name, func=func, guardrail_params=params):
                    return await func(*args, **kwargs)

            return async_inner  # type: ignore[return-value]

        @wraps(func)
        def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            with _run_context(name=_name, func=func, guardrail_params=params):
                return func(*args, **kwargs)

        return inner

    if f is not None and not callable(f):
        # @trace("name") -> f is the name string
        return lambda func: decorator(func, explicit=str(f))  # type: ignore[return-value]
    if f is None:
        return decorator  # type: ignore[return-value]
    return decorator(f)  # type: ignore[return-value]


@contextmanager
def traced_run(
    name: str | None = None,
    *,
    stop_on_loop: bool | None = None,
    stop_on_loop_min_repetitions: int | None = None,
    max_llm_calls: int | None = None,
    max_tool_calls: int | None = None,
    max_events: int | None = None,
    max_duration_s: float | None = None,
) -> Generator[None, None, None]:
    """
    Context manager that starts a new run (RUN_START / RUN_END / ERROR on exception)
    when no run is active; otherwise runs the block in the existing run without
    creating a new run. Run name precedence: AGENTDBG_RUN_NAME env, then name, then default.
    Guardrail kwargs override config; see SPEC §13.
    """
    config = load_config()
    kw: dict[str, Any] = {}
    if stop_on_loop is not None:
        kw["stop_on_loop"] = stop_on_loop
    if stop_on_loop_min_repetitions is not None:
        kw["stop_on_loop_min_repetitions"] = stop_on_loop_min_repetitions
    if max_llm_calls is not None:
        kw["max_llm_calls"] = max_llm_calls
    if max_tool_calls is not None:
        kw["max_tool_calls"] = max_tool_calls
    if max_events is not None:
        kw["max_events"] = max_events
    if max_duration_s is not None:
        kw["max_duration_s"] = max_duration_s
    params = merge_guardrail_params(config.guardrails, **kw)
    with _run_context(name=name, func=None, guardrail_params=params):
        yield
