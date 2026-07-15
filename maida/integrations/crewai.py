"""
CrewAI execution-hooks integration: records LLM and tool calls into the active Maida run.

Import to activate: `from maida.integrations import crewai as maida_crewai`
Then use `with maida.traced_run(...): crew.kickoff()` or `@maida.trace` around flow.kickoff().

Hook ordering caveat: CrewAI runs hooks in registration order. If another before-hook returns False
and blocks execution, our before-hook may never run for that call, so we cannot capture it.
"""

import time
import traceback
from types import TracebackType
from typing import Any

from maida._integration_utils import register_run_enter, register_run_exit
from maida._tracing._context import _ensure_run
from maida.exceptions import GuardrailExceeded, _MaidaAbortSignal
from maida.integrations._error import MissingOptionalDependencyError
from maida.tracing import record_llm_call, record_tool_call

try:
    from crewai.hooks import (
        register_after_llm_call_hook,
        register_after_tool_call_hook,
        register_before_llm_call_hook,
        register_before_tool_call_hook,
    )
except ImportError as e:
    raise MissingOptionalDependencyError(
        "CrewAI integration requires optional deps. Install with `pip install maida-ai[crewai]`."
    ) from e


# Register CrewAI hooks once per process (idempotent). Set on first run_enter.
_crewai_hooks_registered = False

# Per-run pending: run_id -> { key: entry }. Keys are stable for before/after matching.
# LLM: we use a stack per (run_id, executor_id, iterations) so after_hook pops the matching before.
_pending_llm: dict[
    str, dict[tuple[int, int, int], dict[str, Any]]
] = {}  # run_id -> {(exec_id, it, seq): entry}
_llm_stack: dict[
    tuple[str, int, int], list[tuple[int, int, int]]
] = {}  # (run_id, exec_id, it) -> [keys]
_llm_next_seq: dict[
    tuple[str, int, int], int
] = {}  # (run_id, exec_id, it) -> next sequence number

_pending_tool: dict[
    str, dict[tuple[str, int], dict[str, Any]]
] = {}  # run_id -> {(tool_name, seq): entry}
_tool_next_seq: dict[
    tuple[str, str], int
] = {}  # (run_id, tool_name) -> next sequence number

# Per-run guardrail state. CrewAI catches Exception around hook execution, so
# adapter callbacks escalate GuardrailExceeded to a private BaseException signal.
_abort_exceptions: dict[str, GuardrailExceeded] = {}


def _snapshot_messages(messages: Any) -> Any:
    """Snapshot messages for storage (avoid holding mutable refs)."""
    if messages is None:
        return None
    if not isinstance(messages, (list, tuple)):
        return messages
    out = []
    for m in messages:
        if isinstance(m, dict):
            out.append(dict(m))
        elif hasattr(m, "__dict__"):
            out.append(
                {
                    "type": getattr(m, "type", "unknown"),
                    "content": getattr(m, "content", str(m)),
                }
            )
        else:
            out.append(str(m))
    return out


def _snapshot_tool_input(tool_input: Any) -> Any:
    """Snapshot tool_input for storage."""
    if tool_input is None:
        return None
    if isinstance(tool_input, dict):
        return dict(tool_input)
    return tool_input


def _model_from_llm(llm: Any) -> str:
    """Best-effort model string from context.llm."""
    if llm is None:
        return "unknown"
    if hasattr(llm, "model_name"):
        return str(getattr(llm, "model_name", "unknown"))
    if hasattr(llm, "model"):
        return str(getattr(llm, "model", "unknown"))
    return str(llm)[:200] if llm else "unknown"


def _crewai_meta_llm(context: Any) -> dict[str, Any]:
    """Build meta.crewai.* for LLM_CALL."""
    meta: dict[str, Any] = {"framework": "crewai"}
    try:
        if hasattr(context, "executor") and context.executor is not None:
            meta["crewai"] = meta.get("crewai") or {}
            meta["crewai"]["executor_id"] = id(context.executor)
        if hasattr(context, "iterations"):
            meta.setdefault("crewai", {})["iterations"] = context.iterations
        if (
            hasattr(context, "agent")
            and context.agent is not None
            and getattr(context.agent, "role", None)
        ):
            meta.setdefault("crewai", {})["agent_role"] = context.agent.role
        if (
            hasattr(context, "task")
            and context.task is not None
            and getattr(context.task, "description", None)
        ):
            meta.setdefault("crewai", {})["task_desc"] = context.task.description
        if hasattr(context, "crew") and context.crew is not None:
            meta.setdefault("crewai", {})["crew_id"] = id(context.crew)
    except Exception:
        pass
    return meta


def _crewai_meta_tool(context: Any) -> dict[str, Any]:
    """Build meta.crewai.* for TOOL_CALL."""
    meta: dict[str, Any] = {"framework": "crewai"}
    try:
        if (
            hasattr(context, "agent")
            and context.agent is not None
            and getattr(context.agent, "role", None)
        ):
            meta.setdefault("crewai", {})["agent_role"] = context.agent.role
        if (
            hasattr(context, "task")
            and context.task is not None
            and getattr(context.task, "description", None)
        ):
            meta.setdefault("crewai", {})["task_desc"] = context.task.description
    except Exception:
        pass
    return meta


def _ensure_crewai_hooks_registered() -> None:
    """Register CrewAI hooks once. Idempotent; does not clear user hooks."""
    global _crewai_hooks_registered
    if _crewai_hooks_registered:
        return
    register_before_llm_call_hook(_before_llm_call)
    register_after_llm_call_hook(_after_llm_call)
    register_before_tool_call_hook(_before_tool_call)
    register_after_tool_call_hook(_after_tool_call)
    _crewai_hooks_registered = True


def _get_active_run_id() -> str | None:
    """Return current run id from active run (same as recorders use); None if no run."""
    ctx = _ensure_run()
    return ctx[0] if ctx else None


def _check_aborted(run_id: str) -> None:
    """Stop CrewAI from starting or completing hooks after a guardrail abort."""
    cause = _abort_exceptions.get(run_id)
    if cause is not None:
        raise _MaidaAbortSignal(cause)


def raise_if_aborted() -> None:
    """Re-raise a guardrail captured for the active run, if any."""
    run_id = _get_active_run_id()
    if run_id is None:
        return
    cause = _abort_exceptions.get(run_id)
    if cause is not None:
        raise cause


def _before_llm_call(context: Any) -> bool | None:
    """Capture an LLM start, or propagate a prior guardrail abort."""
    try:
        run_id = _get_active_run_id()
        if run_id is None:
            return None
        _check_aborted(run_id)
        executor_id = (
            id(context.executor)
            if getattr(context, "executor", None) is not None
            else 0
        )
        iterations = getattr(context, "iterations", 0)
        key_base = (run_id, executor_id, iterations)
        seq = _llm_next_seq.get(key_base, 0)
        _llm_next_seq[key_base] = seq + 1
        key = (executor_id, iterations, seq)
        _pending_llm.setdefault(run_id, {})[key] = {
            "start_ts": time.perf_counter(),
            "messages": _snapshot_messages(getattr(context, "messages", None)),
            "model": _model_from_llm(getattr(context, "llm", None)),
            "meta": _crewai_meta_llm(context),
        }
        _llm_stack.setdefault(key_base, []).append(key)
        return None
    except Exception:
        return None


def _after_llm_call(context: Any) -> str | None:
    """Record an LLM completion, escalating guardrails past CrewAI."""
    try:
        run_id = _get_active_run_id()
        if run_id is None:
            return None
        _check_aborted(run_id)
        executor_id = (
            id(context.executor)
            if getattr(context, "executor", None) is not None
            else 0
        )
        iterations = getattr(context, "iterations", 0)
        key_base = (run_id, executor_id, iterations)
        stack = _llm_stack.get(key_base, [])
        if not stack:
            return None
        key = stack.pop()
        pending = _pending_llm.get(run_id, {}).pop(key, None)
        if pending is None:
            return None
        duration_ms = max(0, int((time.perf_counter() - pending["start_ts"]) * 1000))
        response = getattr(context, "response", None)
        record_llm_call(
            model=pending["model"],
            prompt=pending["messages"],
            response=response,
            usage=None,
            meta={
                **pending["meta"],
                "crewai": {
                    **(pending["meta"].get("crewai") or {}),
                    "duration_ms": duration_ms,
                },
            },
            provider="unknown",
            status="ok",
        )
        return None
    except GuardrailExceeded as e:
        _abort_exceptions[run_id] = e
        raise _MaidaAbortSignal(e) from e
    except Exception:
        return None


def _before_tool_call(context: Any) -> bool | None:
    """Capture a tool start, or propagate a prior guardrail abort."""
    try:
        run_id = _get_active_run_id()
        if run_id is None:
            return None
        _check_aborted(run_id)
        tool_name = getattr(context, "tool_name", "unknown") or "unknown"
        key_base = (run_id, tool_name)
        seq = _tool_next_seq.get(key_base, 0)
        _tool_next_seq[key_base] = seq + 1
        key = (tool_name, seq)
        _pending_tool.setdefault(run_id, {})[key] = {
            "start_ts": time.perf_counter(),
            "tool_input": _snapshot_tool_input(getattr(context, "tool_input", None)),
            "meta": _crewai_meta_tool(context),
        }
        return None
    except Exception:
        return None


def _after_tool_call(context: Any) -> str | None:
    """Record a tool completion, escalating guardrails past CrewAI."""
    try:
        run_id = _get_active_run_id()
        if run_id is None:
            return None
        _check_aborted(run_id)
        tool_name = getattr(context, "tool_name", "unknown") or "unknown"
        by_run = _pending_tool.get(run_id, {})
        # Match FIFO: same tool_name, smallest seq (first before_hook for this tool).
        candidates = [k for k in by_run if k[0] == tool_name]
        if not candidates:
            return None
        matching_key = min(candidates, key=lambda k: k[1])
        pending = by_run.pop(matching_key)
        duration_ms = max(0, int((time.perf_counter() - pending["start_ts"]) * 1000))
        tool_result = getattr(context, "tool_result", None)
        record_tool_call(
            name=tool_name,
            args=pending["tool_input"],
            result=tool_result,
            meta={
                **pending["meta"],
                "crewai": {
                    **(pending["meta"].get("crewai") or {}),
                    "duration_ms": duration_ms,
                },
            },
            status="ok",
        )
        return None
    except GuardrailExceeded as e:
        _abort_exceptions[run_id] = e
        raise _MaidaAbortSignal(e) from e
    except Exception:
        return None


def _flush_pending_for_run(
    run_id: str,
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    tb: TracebackType | None,
) -> None:
    """Emit best-effort events for pending LLM/tool calls and clear per-run state."""
    try:
        if exc_type is not None and exc_value is not None and tb is not None:
            stack = "".join(traceback.format_exception(exc_type, exc_value, tb))
            incomplete_error: dict[str, Any] = {
                "error_type": exc_type.__name__,
                "message": str(exc_value),
                "stack": stack,
            }
        else:
            incomplete_error = {
                "error_type": "IncompleteCall",
                "message": "Missing after_hook",
                "stack": None,
            }

        for key, entry in list(_pending_llm.get(run_id, {}).items()):
            duration_ms = max(0, int((time.perf_counter() - entry["start_ts"]) * 1000))
            crewai_meta = {
                **((entry.get("meta") or {}).get("crewai") or {}),
                "completion": "missing_after_hook",
                "duration_ms": duration_ms,
            }
            meta = {**(entry.get("meta") or {}), "crewai": crewai_meta}
            record_llm_call(
                model=entry["model"],
                prompt=entry["messages"],
                response=None,
                usage=None,
                meta=meta,
                provider="unknown",
                status="error",
                error=incomplete_error,
            )

        for key, entry in list(_pending_tool.get(run_id, {}).items()):
            duration_ms = max(0, int((time.perf_counter() - entry["start_ts"]) * 1000))
            crewai_meta = {
                **((entry.get("meta") or {}).get("crewai") or {}),
                "completion": "missing_after_hook",
                "duration_ms": duration_ms,
            }
            meta = {**(entry.get("meta") or {}), "crewai": crewai_meta}
            record_tool_call(
                name=key[0],
                args=entry["tool_input"],
                result=None,
                meta=meta,
                status="error",
                error=incomplete_error,
            )

    except Exception:
        pass
    finally:
        _pending_llm.pop(run_id, None)
        _pending_tool.pop(run_id, None)
        for k in list(_llm_stack.keys()):
            if k[0] == run_id:
                del _llm_stack[k]
        for k in list(_llm_next_seq.keys()):
            if k[0] == run_id:
                del _llm_next_seq[k]
        for k in list(_tool_next_seq.keys()):
            if k[0] == run_id:
                del _tool_next_seq[k]


def _on_run_enter() -> None:
    """Lifecycle: on run start, ensure CrewAI hooks are registered once."""
    try:
        _ensure_crewai_hooks_registered()
    except Exception:
        pass


def _on_run_exit(
    run_id: str,
    exc_type: type[BaseException] | None,
    exc_value: BaseException | None,
    tb: TracebackType | None,
) -> None:
    """Lifecycle: on run exit, flush pending calls for this run and clean up."""
    try:
        _flush_pending_for_run(run_id, exc_type, exc_value, tb)
    finally:
        _abort_exceptions.pop(run_id, None)


# Activate on import: register with core lifecycle (register-on-import, Option C).
register_run_enter(_on_run_enter)
register_run_exit(_on_run_exit)
