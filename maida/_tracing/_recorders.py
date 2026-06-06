"""
Recorders: record_llm_call, record_tool_call, record_state.

Each function creates an OTel span for LLM/TOOL/state/loop events using
GenAI semantic conventions, storing meta as span attributes, and applying
redaction/truncation. State and loop-warning events are stored as child
spans (not root-span events) so they are exported immediately by the
SimpleSpanProcessor and visible during a live run.
"""

import json
from typing import Any

from opentelemetry.trace import SpanKind, Status, StatusCode

from maida.config import MaidaConfig
from maida.events import EventType, new_event
from maida.exceptions import LoopAbort
from maida.loopdetect import detect_loop, pattern_key as loop_pattern_key

from maida._tracing._context import (
    _append_event_and_check_guardrails,
    _ensure_run,
    _guardrail_params_var,
)
from maida._tracing._otel import (
    _get_tracer,
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    MAIDA_ERROR_MESSAGE,
    MAIDA_ERROR_TYPE,
    MAIDA_EVENT_TYPE,
    MAIDA_META,
    MAIDA_STATUS,
    MAIDA_TOOL_NAME,
)
from maida._tracing._redact import (
    _apply_redaction_truncation,
    _normalize_usage,
    _redact_and_truncate,
)


def _record_llm_call_otel(
    model: str,
    config: MaidaConfig,
    prompt: Any = None,
    response: Any = None,
    usage: Any = None,
    provider: str = "unknown",
    temperature: Any = None,
    stop_reason: str | None = None,
    status: str = "ok",
    error: str | BaseException | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Create an OTel span for an LLM call using GenAI semantic conventions."""
    tracer = _get_tracer()
    status_val = "ok" if status not in ("ok", "error") else status
    error_obj: dict[str, Any] | None = None
    if status_val == "error" and error is not None:
        from maida._tracing._redact import _build_error_payload

        error_obj = _build_error_payload(error, config, include_stack=True)

    attrs = {
        GEN_AI_SYSTEM: provider or "unknown",
        GEN_AI_OPERATION_NAME: "chat",
        GEN_AI_REQUEST_MODEL: model,
        MAIDA_STATUS: status_val,
    }
    if temperature is not None:
        try:
            attrs[GEN_AI_REQUEST_TEMPERATURE] = float(temperature)
        except (TypeError, ValueError):
            pass
    if stop_reason:
        attrs[GEN_AI_RESPONSE_FINISH_REASONS] = stop_reason

    normalized = _normalize_usage(usage)
    if normalized:
        if normalized.get("prompt_tokens") is not None:
            attrs[GEN_AI_USAGE_INPUT_TOKENS] = normalized["prompt_tokens"]
        if normalized.get("completion_tokens") is not None:
            attrs[GEN_AI_USAGE_OUTPUT_TOKENS] = normalized["completion_tokens"]
        if normalized.get("total_tokens") is not None:
            attrs[GEN_AI_USAGE_TOTAL_TOKENS] = normalized["total_tokens"]

    if error_obj:
        attrs[MAIDA_ERROR_TYPE] = error_obj.get("error_type", "Error")
        attrs[MAIDA_ERROR_MESSAGE] = error_obj.get("message", "")

    if meta:
        payload_raw, meta_safe = _apply_redaction_truncation({}, meta, config)
        attrs[MAIDA_META] = json.dumps(meta_safe, ensure_ascii=False, default=str)

    span = tracer.start_span(
        name=model or "llm_call",
        kind=SpanKind.CLIENT,
        attributes=attrs,
    )

    if prompt is not None:
        prompt_redacted = _redact_and_truncate(prompt, config)
        span.add_event("gen_ai.user.message", {"content": str(prompt_redacted)})
    if response is not None:
        response_redacted = _redact_and_truncate(response, config)
        span.add_event("gen_ai.assistant.message", {"content": str(response_redacted)})

    if status_val == "error":
        span.set_status(
            Status(StatusCode.ERROR, error_obj.get("message", "") if error_obj else "")
        )
    else:
        span.set_status(Status(StatusCode.OK))
    span.end()


def _record_tool_call_otel(
    name: str,
    config: MaidaConfig,
    args: Any = None,
    result: Any = None,
    status: str = "ok",
    error: str | BaseException | dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Create an OTel span for a tool call."""
    tracer = _get_tracer()
    status_val = "ok" if status not in ("ok", "error") else status
    error_obj: dict[str, Any] | None = None
    if status_val == "error" and error is not None:
        from maida._tracing._redact import _build_error_payload

        error_obj = _build_error_payload(error, config, include_stack=True)

    attrs = {
        MAIDA_TOOL_NAME: name,
        MAIDA_STATUS: status_val,
    }
    if error_obj:
        attrs[MAIDA_ERROR_TYPE] = error_obj.get("error_type", "Error")
        attrs[MAIDA_ERROR_MESSAGE] = error_obj.get("message", "")

    if meta:
        payload_raw, meta_safe = _apply_redaction_truncation({}, meta, config)
        attrs[MAIDA_META] = json.dumps(meta_safe, ensure_ascii=False, default=str)

    span = tracer.start_span(
        name=name or "tool_call",
        kind=SpanKind.INTERNAL,
        attributes=attrs,
    )

    if args is not None:
        args_redacted = _redact_and_truncate(args, config)
        span.add_event(
            "maida.tool.args",
            {
                "args": json.dumps(args_redacted, ensure_ascii=False, default=str),
            },
        )
    if result is not None:
        result_redacted = _redact_and_truncate(result, config)
        span.add_event(
            "maida.tool.result",
            {
                "result": json.dumps(result_redacted, ensure_ascii=False, default=str),
            },
        )

    if status_val == "error":
        span.set_status(
            Status(StatusCode.ERROR, error_obj.get("message", "") if error_obj else "")
        )
    else:
        span.set_status(Status(StatusCode.OK))
    span.end()


def _maybe_emit_loop_warning(
    run_id: str,
    counts: dict[str, int],
    config: MaidaConfig,
    window: list[dict],
    emitted: set[str],
) -> None:
    """Emit LOOP_WARNING as a child span (exported immediately) if a repeating pattern is detected."""
    payload = detect_loop(window, config.loop_window, config.loop_repetitions)
    if payload is None:
        return
    key = loop_pattern_key(payload)
    if key in emitted:
        params = _guardrail_params_var.get()
        if params is not None and params.stop_on_loop:
            repetitions = payload.get("repetitions", 0)
            if repetitions >= params.stop_on_loop_min_repetitions:
                raise LoopAbort(
                    threshold=params.stop_on_loop_min_repetitions,
                    actual=repetitions,
                    message=(
                        f"guardrail stop_on_loop: loop re-detected after previous "
                        f"abort was swallowed (repetitions {repetitions} >= "
                        f"stop_on_loop_min_repetitions {params.stop_on_loop_min_repetitions})"
                    ),
                )
        return

    tracer = _get_tracer()
    payload_redacted = _redact_and_truncate(payload, config)
    span = tracer.start_span(
        name="loop_warning",
        kind=SpanKind.INTERNAL,
        attributes={
            MAIDA_EVENT_TYPE: "LOOP_WARNING",
        },
    )
    safe_attrs: dict[str, str | bool | int | float] = {}
    for k, v in payload_redacted.items():
        if isinstance(v, (str, bool, int, float)):
            safe_attrs[k] = v
        else:
            safe_attrs[k] = json.dumps(v, ensure_ascii=False, default=str)
    span.add_event("maida.loop.warning", safe_attrs)
    span.set_status(Status(StatusCode.OK))
    span.end()

    pattern = payload.get("pattern", "loop_warning")
    max_name_len = 80
    name = (
        pattern if len(pattern) <= max_name_len else pattern[: max_name_len - 1] + "..."
    )

    ev = new_event(EventType.LOOP_WARNING, run_id, name, payload_redacted)
    emitted.add(key)
    counts["loop_warnings"] = counts.get("loop_warnings", 0) + 1
    _append_event_and_check_guardrails(run_id, ev, config, counts)


def record_llm_call(
    model: str,
    prompt: Any = None,
    response: Any = None,
    usage: Any = None,
    meta: dict[str, Any] | None = None,
    provider: str = "unknown",
    temperature: Any = None,
    stop_reason: str | None = None,
    status: str = "ok",
    error: str | BaseException | dict[str, Any] | None = None,
) -> None:
    """Record an LLM call as an OTel span. No-op if no active run."""
    ctx = _ensure_run()
    if ctx is None:
        return
    run_id, counts, config, window, emitted = ctx

    _record_llm_call_otel(
        model=model,
        config=config,
        prompt=prompt,
        response=response,
        usage=usage,
        provider=provider,
        temperature=temperature,
        stop_reason=stop_reason,
        status=status,
        error=error,
        meta=meta,
    )

    counts["llm_calls"] = counts.get("llm_calls", 0) + 1
    ev = new_event(EventType.LLM_CALL, run_id, model, {"model": model})
    _append_event_and_check_guardrails(run_id, ev, config, counts)
    window.append({"event_type": "LLM_CALL", "payload": {"model": model}})
    if len(window) > config.loop_window:
        window[:] = window[-config.loop_window :]
    _maybe_emit_loop_warning(run_id, counts, config, window, emitted)


def record_tool_call(
    name: str,
    args: Any = None,
    result: Any = None,
    meta: dict[str, Any] | None = None,
    status: str = "ok",
    error: str | BaseException | dict[str, Any] | None = None,
) -> None:
    """Record a tool call as an OTel span. No-op if no active run."""
    ctx = _ensure_run()
    if ctx is None:
        return
    run_id, counts, config, window, emitted = ctx

    _record_tool_call_otel(
        name=name,
        config=config,
        args=args,
        result=result,
        status=status,
        error=error,
        meta=meta,
    )

    counts["tool_calls"] = counts.get("tool_calls", 0) + 1
    ev = new_event(EventType.TOOL_CALL, run_id, name, {"tool_name": name})
    _append_event_and_check_guardrails(run_id, ev, config, counts)
    window.append({"event_type": "TOOL_CALL", "payload": {"tool_name": name}})
    if len(window) > config.loop_window:
        window[:] = window[-config.loop_window :]
    _maybe_emit_loop_warning(run_id, counts, config, window, emitted)


def record_state(
    state: Any = None,
    meta: dict[str, Any] | None = None,
    diff: Any = None,
) -> None:
    """Record a state update as a child OTel span (exported immediately). No-op if no active run."""
    ctx = _ensure_run()
    if ctx is None:
        return
    run_id, counts, config, window, emitted = ctx

    tracer = _get_tracer()
    span = tracer.start_span(
        name="state",
        kind=SpanKind.INTERNAL,
    )

    event_attrs = {}
    if state is not None:
        state_redacted = _redact_and_truncate(state, config)
        event_attrs["state"] = json.dumps(
            state_redacted, ensure_ascii=False, default=str
        )
    if diff is not None:
        diff_redacted = _redact_and_truncate(diff, config)
        event_attrs["diff"] = json.dumps(diff_redacted, ensure_ascii=False, default=str)
    if meta:
        _, meta_redacted = _apply_redaction_truncation({}, meta, config)
        event_attrs["meta"] = json.dumps(meta_redacted, ensure_ascii=False, default=str)

    if event_attrs:
        span.add_event("state", event_attrs)
    span.set_status(Status(StatusCode.OK))
    span.end()

    ev = new_event(EventType.STATE_UPDATE, run_id, "state", {})
    _append_event_and_check_guardrails(run_id, ev, config, counts)
    window.append({"event_type": "STATE_UPDATE", "payload": {}})
    if len(window) > config.loop_window:
        window[:] = window[-config.loop_window :]
