"""
Loop detection for agent runs: signature computation and repeated-pattern detection.

Stdlib only. Pure functions, no I/O. Used to emit LOOP_WARNING when the last N
events contain a consecutively repeating signature subsequence.
"""

from typing import Any

# Sentinel for evidence_event_ids when an event has no event_id (better UX than "")
MISSING_EVENT_ID = "__MISSING__"
_MAX_SIGNATURE_DEPTH = 4
_MAX_SEQUENCE_ITEMS = 3


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    return type(value).__name__


def _structural_signature(value: Any, depth: int = 0) -> str:
    """Return a compact shape-only signature without raw scalar values."""
    if depth >= _MAX_SIGNATURE_DEPTH:
        # TODO: If value has infinite depth, this will silently accept it.
        return "..."
    if isinstance(value, dict):
        if not value:
            return "{}"
        parts = []
        for key in sorted(value, key=lambda item: str(item)):
            parts.append(f"{key}:{_structural_signature(value[key], depth + 1)}")
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        item_shapes = []
        for item in value[:_MAX_SEQUENCE_ITEMS]:
            shape = _structural_signature(item, depth + 1)
            if shape not in item_shapes:
                item_shapes.append(shape)
        suffix = ",..." if len(value) > _MAX_SEQUENCE_ITEMS else ""
        return "[" + "|".join(item_shapes) + suffix + "]"
    return _type_name(value)


def compute_signature(event: dict) -> str:
    """
    Produce a stable string signature for an event for loop detection.

    - LLM_CALL: "LLM_CALL:" + model (or "UNKNOWN" if missing)
    - TOOL_CALL: "TOOL_CALL:" + tool_name, plus structural args when present
    - Else: event_type (or empty string)
    """
    t = event.get("event_type")
    if t == "LLM_CALL":
        model = event.get("payload", {}).get("model", "") or "UNKNOWN"
        return "LLM_CALL:" + str(model)
    if t == "TOOL_CALL":
        payload = event.get("payload", {})
        tool_name = payload.get("tool_name", "") or "UNKNOWN"
        signature = "TOOL_CALL:" + str(tool_name)
        args = payload.get("args", None)
        if args is not None:
            signature += " args:" + _structural_signature(args)
        return signature
    return str(t or "")


def detect_loop(
    events: list[dict],
    window: int,
    repetitions: int,
) -> dict | None:
    """
    Detect a consecutively repeating signature subsequence near the end of the run.

    Only considers the last `window` events. Finds the smallest pattern length m (>= 1)
    such that the last m*repetitions signatures form the same m-length block repeated
    `repetitions` times. Returns a LOOP_WARNING payload or None.
    """
    if not events or repetitions < 2 or window < 2:
        return None

    events_window = events[-window:] if len(events) >= window else events
    n = len(events_window)
    sigs = [compute_signature(e) for e in events_window]

    # m * repetitions must fit in the window
    max_m = n // repetitions
    if max_m < 1:
        return None

    for m in range(1, max_m + 1):
        L = m * repetitions
        if L > n:
            continue
        tail = sigs[-L:]
        block = tail[:m]
        # Check tail == block repeated 'repetitions' times
        if all(tail[i * m : (i + 1) * m] == block for i in range(repetitions)):
            evidence_events = events_window[-L:]
            evidence_event_ids = [
                e.get("event_id") or MISSING_EVENT_ID for e in evidence_events
            ]
            pattern = " -> ".join(block)
            return {
                "pattern": pattern,
                "pattern_type": "repeated_call" if m == 1 else "cycle",
                "pattern_length": m,
                "repetitions": repetitions,
                "window_size": len(events_window),
                "evidence_event_ids": evidence_event_ids,
            }
    return None


def pattern_key(payload: dict) -> str:
    """
    Stable key for deduplication from LOOP_WARNING payload.

    Derived only from pattern and repetitions (no timestamps).
    """
    return f"{payload.get('pattern', '')}|{payload.get('repetitions', 0)}"
