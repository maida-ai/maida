"""
Run guardrails: pure check logic after each event.

No I/O, no network. Used by the tracing layer to abort runs when limits are exceeded.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from maida.events import utc_now_iso_ms_z
from maida.exceptions import GuardrailExceeded, LoopAbort


@dataclass
class GuardrailParams:
    """Optional guardrail limits; None means disabled."""

    stop_on_loop: bool = False
    stop_on_loop_min_repetitions: int = 3
    max_llm_calls: int | None = None
    max_tool_calls: int | None = None
    max_events: int | None = None
    max_duration_s: float | None = None


def merge_guardrail_params(base: GuardrailParams, **overrides: Any) -> GuardrailParams:
    """Return a new GuardrailParams with overrides applied. Only keys present in overrides are used."""
    out = GuardrailParams(
        stop_on_loop=base.stop_on_loop,
        stop_on_loop_min_repetitions=base.stop_on_loop_min_repetitions,
        max_llm_calls=base.max_llm_calls,
        max_tool_calls=base.max_tool_calls,
        max_events=base.max_events,
        max_duration_s=base.max_duration_s,
    )
    if "stop_on_loop" in overrides:
        out = GuardrailParams(
            stop_on_loop=bool(overrides["stop_on_loop"]),
            stop_on_loop_min_repetitions=out.stop_on_loop_min_repetitions,
            max_llm_calls=out.max_llm_calls,
            max_tool_calls=out.max_tool_calls,
            max_events=out.max_events,
            max_duration_s=out.max_duration_s,
        )
    if (
        "stop_on_loop_min_repetitions" in overrides
        and overrides["stop_on_loop_min_repetitions"] is not None
    ):
        n = overrides["stop_on_loop_min_repetitions"]
        try:
            out = GuardrailParams(
                stop_on_loop=out.stop_on_loop,
                stop_on_loop_min_repetitions=max(2, int(n)),
                max_llm_calls=out.max_llm_calls,
                max_tool_calls=out.max_tool_calls,
                max_events=out.max_events,
                max_duration_s=out.max_duration_s,
            )
        except (TypeError, ValueError):
            pass
    if "max_llm_calls" in overrides and overrides["max_llm_calls"] is not None:
        try:
            n = int(overrides["max_llm_calls"])
            if n >= 0:
                out = GuardrailParams(
                    stop_on_loop=out.stop_on_loop,
                    stop_on_loop_min_repetitions=out.stop_on_loop_min_repetitions,
                    max_llm_calls=n,
                    max_tool_calls=out.max_tool_calls,
                    max_events=out.max_events,
                    max_duration_s=out.max_duration_s,
                )
        except (TypeError, ValueError):
            pass
    if "max_tool_calls" in overrides and overrides["max_tool_calls"] is not None:
        try:
            n = int(overrides["max_tool_calls"])
            if n >= 0:
                out = GuardrailParams(
                    stop_on_loop=out.stop_on_loop,
                    stop_on_loop_min_repetitions=out.stop_on_loop_min_repetitions,
                    max_llm_calls=out.max_llm_calls,
                    max_tool_calls=n,
                    max_events=out.max_events,
                    max_duration_s=out.max_duration_s,
                )
        except (TypeError, ValueError):
            pass
    if "max_events" in overrides and overrides["max_events"] is not None:
        try:
            n = int(overrides["max_events"])
            if n >= 0:
                out = GuardrailParams(
                    stop_on_loop=out.stop_on_loop,
                    stop_on_loop_min_repetitions=out.stop_on_loop_min_repetitions,
                    max_llm_calls=out.max_llm_calls,
                    max_tool_calls=out.max_tool_calls,
                    max_events=n,
                    max_duration_s=out.max_duration_s,
                )
        except (TypeError, ValueError):
            pass
    if "max_duration_s" in overrides and overrides["max_duration_s"] is not None:
        try:
            n = max(0.0, float(overrides["max_duration_s"]))
            out = GuardrailParams(
                stop_on_loop=out.stop_on_loop,
                stop_on_loop_min_repetitions=out.stop_on_loop_min_repetitions,
                max_llm_calls=out.max_llm_calls,
                max_tool_calls=out.max_tool_calls,
                max_events=out.max_events,
                max_duration_s=n,
            )
        except (TypeError, ValueError):
            pass
    return out


def _parse_iso_z(ts: str) -> datetime:
    """Parse ISO8601 UTC timestamp with Z suffix."""
    normalized = ts.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


def check_after_event(
    event: dict,
    counts: dict,
    total_events: int,
    started_at_iso: str,
    params: GuardrailParams,
    *,
    now_iso: str | None = None,
) -> None:
    """
    Check guardrail limits after an event was recorded. Raise GuardrailExceeded
    (or LoopAbort for stop_on_loop) when a threshold is exceeded.

    Call after appending the event and incrementing counts/total_events so that
    max_llm_calls=50 aborts on the 51st LLM call (count > 50).

    Args:
        event: The event just recorded (event_type, payload, etc.).
        counts: Current run counts (llm_calls, tool_calls, errors, loop_warnings).
        total_events: Total number of events recorded so far (including this one).
        started_at_iso: Run start timestamp (ISO8601 UTC).
        params: Guardrail limits from config/decoration.
        now_iso: Optional current time for deterministic tests (default: utc_now).
    """
    # stop_on_loop: abort when we just emitted a LOOP_WARNING and repetitions >= threshold
    if event.get("event_type") == "LOOP_WARNING" and params.stop_on_loop:
        payload = event.get("payload") or {}
        repetitions = payload.get("repetitions", 0)
        if repetitions >= params.stop_on_loop_min_repetitions:
            msg = (
                f"guardrail stop_on_loop: repetitions {repetitions} >= "
                f"stop_on_loop_min_repetitions {params.stop_on_loop_min_repetitions}"
            )
            raise LoopAbort(
                threshold=params.stop_on_loop_min_repetitions,
                actual=repetitions,
                message=msg,
            )

    # max_llm_calls: abort when count exceeds limit (trigger at N+1)
    if params.max_llm_calls is not None:
        llm = counts.get("llm_calls", 0)
        if llm > params.max_llm_calls:
            raise GuardrailExceeded(
                guardrail="max_llm_calls",
                threshold=params.max_llm_calls,
                actual=llm,
                message=f"guardrail max_llm_calls: {llm} > {params.max_llm_calls}",
            )

    # max_tool_calls: same
    if params.max_tool_calls is not None:
        tool = counts.get("tool_calls", 0)
        if tool > params.max_tool_calls:
            raise GuardrailExceeded(
                guardrail="max_tool_calls",
                threshold=params.max_tool_calls,
                actual=tool,
                message=f"guardrail max_tool_calls: {tool} > {params.max_tool_calls}",
            )

    # max_events: abort when total events exceeds limit
    if params.max_events is not None:
        if total_events > params.max_events:
            raise GuardrailExceeded(
                guardrail="max_events",
                threshold=params.max_events,
                actual=total_events,
                message=f"guardrail max_events: {total_events} > {params.max_events}",
            )

    # max_duration_s: abort when elapsed time >= limit
    if params.max_duration_s is not None:
        now_str = now_iso if now_iso is not None else utc_now_iso_ms_z()
        try:
            start_dt = _parse_iso_z(started_at_iso)
            end_dt = _parse_iso_z(now_str)
            elapsed_s = (end_dt - start_dt).total_seconds()
        except (ValueError, TypeError):
            elapsed_s = 0.0
        if elapsed_s >= params.max_duration_s:
            raise GuardrailExceeded(
                guardrail="max_duration_s",
                threshold=params.max_duration_s,
                actual=elapsed_s,
                message=f"guardrail max_duration_s: elapsed {elapsed_s}s >= {params.max_duration_s}s",
            )
