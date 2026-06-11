"""
Run context management for Maida's OTel-based tracing.

Keeps per-run state (counts, config, event window, guardrails) in context vars.
Uses OTel's trace.get_current_span() to detect active runs.

Key difference from v0.1: has_active_run() checks OTel span context rather than
the legacy custom event system.
"""

import atexit
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from maida.config import MaidaConfig, load_config
from maida.constants import default_counts
from maida.events import utc_now_iso_ms_z
from maida.guardrails import GuardrailParams, check_after_event
from maida._tracing._redact import _redact_and_truncate, _redact_argv

_run_id_var: ContextVar[str | None] = ContextVar("maida_run_id", default=None)
_counts_var: ContextVar[dict | None] = ContextVar("maida_counts", default=None)
_config_var: ContextVar[MaidaConfig | None] = ContextVar("maida_config", default=None)
_event_window_var: ContextVar[list[dict] | None] = ContextVar(
    "maida_event_window", default=None
)
_loop_emitted_var: ContextVar[set[str] | None] = ContextVar(
    "maida_loop_emitted", default=None
)
_guardrail_params_var: ContextVar[GuardrailParams | None] = ContextVar(
    "maida_guardrail_params", default=None
)
_started_at_var: ContextVar[str | None] = ContextVar("maida_started_at", default=None)
_event_count_var: ContextVar[int] = ContextVar("maida_event_count", default=0)

# Implicit run (process-level, for MAIDA_IMPLICIT_RUN=1)
_implicit_run_id: str | None = None
_implicit_counts: dict | None = None
_implicit_config: MaidaConfig | None = None
_implicit_started_at: str | None = None
_implicit_event_window: list[dict] = []
_implicit_loop_emitted: set[str] = set()
_implicit_root_span: Any = None
_implicit_otel_token: Any = None


def has_active_run() -> bool:
    """Return True when an explicit OTel span is recording in the current context."""
    span = trace.get_current_span()
    return (
        span is not None
        and span.is_recording()
        and _run_id_var.get() is not None
        and _counts_var.get() is not None
        and _config_var.get() is not None
    )


def _entrypoint(func: Any) -> str:
    try:
        code = getattr(func, "__code__", None)
        filename = code.co_filename if code else None
        if filename:
            try:
                rel = os.path.relpath(filename, os.getcwd())
            except (ValueError, OSError):
                rel = filename
            return f"{rel}:{func.__name__}"
    except Exception:
        pass
    return getattr(func, "__name__", None) or "run"


def _default_run_name_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _resolve_run_name(explicit_name: str | None, func: Any | None) -> str:
    env_name = os.environ.get("MAIDA_RUN_NAME", "").strip()
    if env_name:
        return env_name
    if explicit_name:
        return explicit_name
    if func is not None:
        return f"{_entrypoint(func)} - {_default_run_name_timestamp()}"
    return f"run - {_default_run_name_timestamp()}"


def _finalize_implicit_run() -> None:
    """Atexit hook: finalize the implicit run if one exists (legacy support)."""
    global _implicit_run_id, _implicit_counts, _implicit_config, _implicit_started_at
    global \
        _implicit_event_window, \
        _implicit_loop_emitted, \
        _implicit_root_span, \
        _implicit_otel_token
    if (
        _implicit_run_id is None
        or _implicit_config is None
        or _implicit_started_at is None
    ):
        return
    counts = _implicit_counts or default_counts()
    _implicit_run_id = None
    _implicit_counts = None
    _implicit_config = None
    _implicit_started_at = None
    _implicit_event_window = []
    _implicit_loop_emitted = set()
    try:
        if _implicit_root_span is not None:
            _implicit_root_span.set_attribute(
                "maida.llm_calls", counts.get("llm_calls", 0)
            )
            _implicit_root_span.set_attribute(
                "maida.tool_calls", counts.get("tool_calls", 0)
            )
            _implicit_root_span.set_attribute("maida.errors", counts.get("errors", 0))
            _implicit_root_span.set_attribute(
                "maida.loop_warnings", counts.get("loop_warnings", 0)
            )
            _implicit_root_span.set_status(Status(StatusCode.OK))
            _implicit_root_span.end()
        if _implicit_otel_token is not None:
            from opentelemetry import context as _ot_context

            _ot_context.detach(_implicit_otel_token)
    except Exception:
        pass
    finally:
        _implicit_root_span = None
        _implicit_otel_token = None


atexit.register(_finalize_implicit_run)


def _append_event_and_check_guardrails(
    run_id: str, event: dict, config: MaidaConfig, counts: dict
) -> None:
    """
    Append event to storage (if storage backend supports it), then check guardrails.

    In the OTel-based system, spans are the primary storage unit. This function
    maintains backward compat by tracking the event count for guardrail checks.
    """
    params = _guardrail_params_var.get()
    if params is None:
        return
    count = _event_count_var.get()
    count += 1
    _event_count_var.set(count)
    started_at = _started_at_var.get()
    if started_at is None:
        return
    check_after_event(event, counts, count, started_at, params, now_iso=None)


def _run_end_payload(status: str, counts: dict, started_at: str) -> dict[str, dict]:
    """Build a backward-compatible event-like payload for RUN_END. Used by lifecycle."""
    now = utc_now_iso_ms_z()
    try:
        start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
        duration_ms = max(0, int((end_dt - start_dt).total_seconds() * 1000))
    except (ValueError, TypeError):
        duration_ms = 0
    return {
        "status": status,
        "summary": {
            "llm_calls": counts.get("llm_calls", 0),
            "tool_calls": counts.get("tool_calls", 0),
            "errors": counts.get("errors", 0),
            "duration_ms": duration_ms,
        },
    }


def _run_start_payload_for_event(run_name: str | None, config: MaidaConfig) -> dict:
    """Build a backward-compatible event-like payload for RUN_START. Used by lifecycle."""
    payload = {
        "run_name": run_name,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
        "cwd": os.getcwd(),
        "argv": _redact_argv(list(sys.argv), config),
    }
    return _redact_and_truncate(payload, config)


def _ensure_run() -> tuple[str, dict, MaidaConfig, list[dict], set[str]] | None:
    """
    Return (run_id, counts, config, event_window, loop_emitted) for the current run.

    If MAIDA_IMPLICIT_RUN=1 and no run is active, create an implicit run (once per process).
    """
    global _implicit_run_id, _implicit_counts, _implicit_config, _implicit_started_at
    global _implicit_event_window, _implicit_loop_emitted

    run_id = _run_id_var.get()
    if run_id is not None:
        counts = _counts_var.get()
        config = _config_var.get()
        if counts is not None and config is not None:
            window = _event_window_var.get()
            if window is None:
                window = []
                _event_window_var.set(window)
            emitted = _loop_emitted_var.get()
            if emitted is None:
                emitted = set()
                _loop_emitted_var.set(emitted)
            return (run_id, counts, config, window, emitted)

    if os.environ.get("MAIDA_IMPLICIT_RUN", "").strip() == "1":
        if (
            _implicit_run_id is not None
            and _implicit_counts is not None
            and _implicit_config is not None
        ):
            return (
                _implicit_run_id,
                _implicit_counts,
                _implicit_config,
                _implicit_event_window,
                _implicit_loop_emitted,
            )
        config = load_config()
        from maida._tracing._otel import _setup_otel, _get_tracer

        _setup_otel()
        tracer = _get_tracer()
        run_name = _resolve_run_name("implicit", None)
        counts = default_counts()
        started_at = (
            datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )
        # Create a root OTel span and activate it so recorder child spans
        # are nested under this trace rather than becoming orphaned traces.
        from opentelemetry import context as _ot_context
        from opentelemetry.trace.propagation import set_span_in_context

        global _implicit_root_span, _implicit_otel_token
        _implicit_root_span = tracer.start_span(
            name=run_name or "run",
            kind=trace.SpanKind.INTERNAL,
            attributes={"maida.run_name": run_name},
        )
        _implicit_otel_token = _ot_context.attach(
            set_span_in_context(_implicit_root_span)
        )
        sc = _implicit_root_span.get_span_context()
        run_id = format(sc.trace_id, "032x")
        _implicit_run_id = run_id
        _implicit_counts = counts
        _implicit_config = config
        _implicit_started_at = started_at
        _implicit_event_window = []
        _implicit_loop_emitted = set()
        return (run_id, counts, config, _implicit_event_window, _implicit_loop_emitted)

    return None
