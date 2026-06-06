"""
Run lifecycle: @trace decorator and traced_run context manager.

Both create OTel root spans (representing a whole run) when no run is active,
or reuse the existing run (via context vars) when nested.

Uses context vars for Maida-specific run state and OTel spans for telemetry export.
"""

import asyncio
import os
import sys
import traceback
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable, Generator, ParamSpec, TypeVar

from opentelemetry import trace as otel_trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from maida.config import load_config
from maida.constants import default_counts
from maida.exceptions import GuardrailExceeded, _MaidaAbortSignal
from maida.guardrails import GuardrailParams, merge_guardrail_params
from maida.events import utc_now_iso_ms_z
from maida._tracing._context import (
    _config_var,
    _counts_var,
    _event_count_var,
    _event_window_var,
    _guardrail_params_var,
    _loop_emitted_var,
    _resolve_run_name,
    _run_id_var,
    _started_at_var,
)
from maida._tracing._otel import (
    _get_tracer,
    _setup_otel,
    MAIDA_ARGV,
    MAIDA_CWD,
    MAIDA_ERROR_COUNT,
    MAIDA_ERROR_MESSAGE,
    MAIDA_ERROR_STACK,
    MAIDA_ERROR_TYPE,
    MAIDA_LLM_COUNT,
    MAIDA_LOOP_WARNING_COUNT,
    MAIDA_PLATFORM,
    MAIDA_PYTHON_VERSION,
    MAIDA_RUN_NAME,
    MAIDA_TOOL_COUNT,
)
from maida._tracing._redact import _redact_and_truncate, _redact_argv
from maida._integration_utils import _invoke_run_enter, _invoke_run_exit

P = ParamSpec("P")
R = TypeVar("R")


@contextmanager
def _run_context(
    name: str | None = None,
    func: Callable[..., Any] | None = None,
    guardrail_params: GuardrailParams | None = None,
) -> Generator[None, None, None]:
    """
    Context manager that factors the common run lifecycle.

    Creates an OTel root span (the "run") on outermost entry, sets span attributes
    for run metadata, and records errors via span status + events on the root span.
    """
    existing_run_id = _run_id_var.get()
    if existing_run_id is not None:
        if guardrail_params is not None:
            token_nested = _guardrail_params_var.set(guardrail_params)
            try:
                yield
            finally:
                _guardrail_params_var.reset(token_nested)
        else:
            yield
        return

    _setup_otel()
    config = load_config()
    params = guardrail_params if guardrail_params is not None else config.guardrails
    run_name = _resolve_run_name(name, func)
    tracer = _get_tracer()
    started_at = utc_now_iso_ms_z()

    argv_redacted = _redact_argv(list(sys.argv), config)

    root_span = tracer.start_span(
        name=run_name or "run",
        kind=SpanKind.INTERNAL,
        attributes={
            MAIDA_RUN_NAME: run_name,
            MAIDA_PYTHON_VERSION: f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            MAIDA_PLATFORM: sys.platform,
            MAIDA_CWD: os.getcwd(),
            MAIDA_ARGV: argv_redacted,
            MAIDA_LLM_COUNT: 0,
            MAIDA_TOOL_COUNT: 0,
        },
    )
    run_id = format(root_span.get_span_context().trace_id, "032x")
    counts = default_counts()

    token_run = _run_id_var.set(run_id)
    token_counts = _counts_var.set(counts)
    token_config = _config_var.set(config)
    token_window = _event_window_var.set([])
    token_emitted = _loop_emitted_var.set(set())
    token_guardrail = _guardrail_params_var.set(params)
    token_started_at = _started_at_var.set(started_at)
    token_event_count = _event_count_var.set(0)

    exc_info: tuple[type[BaseException] | None, BaseException | None, Any] = (
        None,
        None,
        None,
    )

    token_otel_span = otel_trace.use_span(root_span, end_on_exit=False)

    def _finish_run(status: str) -> None:
        _invoke_run_exit(run_id, *exc_info)
        attrs = {
            MAIDA_LLM_COUNT: counts.get("llm_calls", 0),
            MAIDA_TOOL_COUNT: counts.get("tool_calls", 0),
            MAIDA_ERROR_COUNT: counts.get("errors", 0),
            MAIDA_LOOP_WARNING_COUNT: counts.get("loop_warnings", 0),
        }
        root_span.set_attributes(attrs)
        if status == "ok":
            root_span.set_status(Status(StatusCode.OK))
        else:
            root_span.set_status(Status(StatusCode.ERROR, status))
        root_span.end()

    try:
        _invoke_run_enter()
        with token_otel_span:
            try:
                yield
            except _MaidaAbortSignal as signal:
                exc_info = sys.exc_info()
                cause = signal.cause
                err_attrs = _redact_and_truncate(
                    {
                        MAIDA_ERROR_TYPE: type(cause).__name__,
                        MAIDA_ERROR_MESSAGE: str(cause),
                        MAIDA_ERROR_STACK: traceback.format_exc(),
                    },
                    config,
                )
                root_span.add_event("exception", err_attrs)
                counts["errors"] = counts.get("errors", 0) + 1
                _finish_run("error")
                raise cause from signal
            except GuardrailExceeded as e:
                exc_info = sys.exc_info()
                err_attrs = _redact_and_truncate(
                    {
                        MAIDA_ERROR_TYPE: type(e).__name__,
                        MAIDA_ERROR_MESSAGE: e.message,
                        MAIDA_ERROR_STACK: traceback.format_exc(),
                    },
                    config,
                )
                root_span.add_event("exception", err_attrs)
                counts["errors"] = counts.get("errors", 0) + 1
                _finish_run("error")
                raise
            except Exception as e:
                exc_info = sys.exc_info()
                err_attrs = _redact_and_truncate(
                    {
                        MAIDA_ERROR_TYPE: type(e).__name__,
                        MAIDA_ERROR_MESSAGE: str(e),
                        MAIDA_ERROR_STACK: traceback.format_exc(),
                    },
                    config,
                )
                root_span.add_event("exception", err_attrs)
                counts["errors"] = counts.get("errors", 0) + 1
                _finish_run("error")
                raise
            except BaseException:
                # Catches SystemExit and KeyboardInterrupt so the OTel span
                # is ended and counts are saved before re-raising. No ERROR
                # event is added since these aren't application errors.
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
    """Decorator that starts a new OTel trace run when no run is active.

    Usage: @trace, @trace(), @trace("run name"), @trace(name="run name").
    Guardrail kwargs override config; see SPEC.
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

            return async_inner

        @wraps(func)
        def inner(*args: P.args, **kwargs: P.kwargs) -> R:
            with _run_context(name=_name, func=func, guardrail_params=params):
                return func(*args, **kwargs)

        return inner

    if f is not None and not callable(f):
        return lambda func: decorator(func, explicit=str(f))
    if f is None:
        return decorator
    return decorator(f)


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
    """Context manager that starts a new OTel trace run when no run is active."""
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
