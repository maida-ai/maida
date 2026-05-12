"""
Context vars, implicit-run state, _ensure_run, and atexit finalization.
Depends: maida.config, maida.constants, maida.events, maida.guardrails, maida.storage, _redact.
"""

import atexit
import os
import sys
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Callable

from maida.config import MaidaConfig, load_config
from maida.constants import default_counts
from maida.events import EventType, new_event, utc_now_iso_ms_z
from maida.guardrails import GuardrailParams, check_after_event
from maida.storage import append_event, create_run, finalize_run

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

# Implicit run: stored so atexit can finalize (RUN_END + run.json status).
_implicit_run_id: str | None = None
_implicit_counts: dict | None = None
_implicit_config: MaidaConfig | None = None
_implicit_started_at: str | None = None
_implicit_event_window: list[dict] = []
_implicit_loop_emitted: set[str] = set()


def has_active_run() -> bool:
    """Return True only when an explicit traced run is active in this context."""
    return (
        _run_id_var.get() is not None
        and _counts_var.get() is not None
        and _config_var.get() is not None
    )


def _entrypoint(func: Callable[..., Any]) -> str:
    """Human-friendly entrypoint string: path/to/file.py:function_name (relative to cwd when possible)."""
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
    """Local timestamp for default run names, e.g. 2025-02-18 14:12."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _resolve_run_name(
    explicit_name: str | None,
    func: Callable[..., Any] | None,
) -> str:
    """
    Resolve run name by precedence: MAIDA_RUN_NAME env, explicit name, default (entrypoint + timestamp).
    """
    env_name = os.environ.get("MAIDA_RUN_NAME", "").strip()
    if env_name:
        return env_name
    if explicit_name:
        return explicit_name
    if func is not None:
        return f"{_entrypoint(func)} - {_default_run_name_timestamp()}"
    return f"run - {_default_run_name_timestamp()}"


def _run_start_payload(run_name: str | None) -> dict[str, Any]:
    """Build RUN_START payload (argv not yet redacted)."""
    return {
        "run_name": run_name,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": sys.platform,
        "cwd": os.getcwd(),
        "argv": list(sys.argv),
    }


def _run_start_payload_for_event(
    run_name: str | None, config: MaidaConfig
) -> dict[str, Any]:
    """Build RUN_START payload with argv values redacted per redact_keys, then apply full redaction/truncation."""
    payload = _run_start_payload(run_name)
    payload["argv"] = _redact_argv(payload["argv"], config)
    return _redact_and_truncate(payload, config)


def _append_event_and_check_guardrails(
    run_id: str, event: dict, config: MaidaConfig, counts: dict
) -> None:
    """
    Append event to storage, then if in an explicit run with guardrails, increment
    event count and run guardrail checks. Raises GuardrailExceeded when a limit is exceeded.
    """
    append_event(run_id, event, config)
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


def _run_end_payload(status: str, counts: dict, started_at: str) -> dict[str, Any]:
    """Build RUN_END payload; duration_ms from started_at to now."""
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


def _finalize_implicit_run() -> None:
    """Atexit hook: write RUN_END and finalize run.json for the implicit run, if any."""
    global _implicit_run_id, _implicit_counts, _implicit_config, _implicit_started_at
    global _implicit_event_window, _implicit_loop_emitted
    if (
        _implicit_run_id is None
        or _implicit_config is None
        or _implicit_started_at is None
    ):
        return
    run_id = _implicit_run_id
    counts = _implicit_counts or default_counts()
    config = _implicit_config
    started_at = _implicit_started_at
    _implicit_run_id = None
    _implicit_counts = None
    _implicit_config = None
    _implicit_started_at = None
    _implicit_event_window = []
    _implicit_loop_emitted = set()
    try:
        payload = _run_end_payload("ok", counts, started_at)
        ev = new_event(EventType.RUN_END, run_id, "run_end", payload)
        append_event(run_id, ev, config)
        finalize_run(run_id, "ok", counts, config)
    except Exception:
        pass


atexit.register(_finalize_implicit_run)


def _ensure_run() -> tuple[str, dict, MaidaConfig, list[dict], set[str]] | None:
    """
    Return (run_id, counts, config, event_window, loop_emitted) for the current run, or None if no run.
    If MAIDA_IMPLICIT_RUN=1 and no run is active, create an implicit run (once per process)
    and return it. Implicit run never sets contextvars, so it does not hijack subsequent
    traced runs or leave a "current run" for the rest of the process.
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
        run_name = _resolve_run_name("implicit", None)
        meta = create_run(run_name, config)
        run_id = meta["run_id"]
        counts = default_counts()
        started_at = meta["started_at"]
        _implicit_run_id = run_id
        _implicit_counts = counts
        _implicit_config = config
        _implicit_started_at = started_at
        _implicit_event_window = []
        _implicit_loop_emitted = set()
        payload = _run_start_payload_for_event(run_name, config)
        ev = new_event(EventType.RUN_START, run_id, run_name, payload)
        append_event(run_id, ev, config)
        return (run_id, counts, config, _implicit_event_window, _implicit_loop_emitted)
    return None
