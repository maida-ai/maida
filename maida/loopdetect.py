"""
Loop detection for agent runs: signature computation and repeated-pattern detection.

Stdlib only. Pure functions, no I/O. Used to emit LOOP_WARNING when the last N
events contain a consecutively repeating signature subsequence.
"""

import hashlib
import json
import struct
from typing import Any

# Sentinel for evidence_event_ids when an event has no event_id (better UX than "")
MISSING_EVENT_ID = "__MISSING__"


def _canonical_args(value: Any) -> Any:
    """Canonicalize normalized JSON args, including numeric parity with JS.

    Containers are tagged to distinguish them from scalar encodings. Numbers
    use big-endian IEEE-754 bytes so 1 and 1.0 agree across producers without
    depending on JSON float formatting. Nonrepresentable Python integers keep
    their exact value. Object order is ignored; array order and all items matter.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        try:
            number = float(value)
        except OverflowError:
            return ["integer", str(value)]
        if isinstance(value, int) and number != value:
            return ["integer", str(value)]
        return ["number", struct.pack("!d", number or 0.0).hex()]
    if isinstance(value, dict):
        # Sort by UTF-16 code units to match JS Object.keys(...).sort().
        keys = sorted(value, key=lambda key: key.encode("utf-16-be", errors="surrogatepass"))
        return ["object", [[key, _canonical_args(value[key])] for key in keys]]
    if isinstance(value, (list, tuple)):
        return ["array", [_canonical_args(item) for item in value]]
    raise TypeError("Loop arguments must be normalized JSON values")


def _argument_fingerprint(args: Any) -> str:
    """Fixed-size digest of already-redacted/truncated args; never render values."""
    canonical = json.dumps(_canonical_args(args), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def compute_signature(event: dict) -> str:
    """
    Produce a stable string signature for an event for loop detection.

    - LLM_CALL: "LLM_CALL:" + model (or "UNKNOWN" if missing)
    - TOOL_CALL: "TOOL_CALL:" + tool_name, plus a digest of sanitized args when present
    - Else: event_type (or empty string)

    Callers must apply configured redaction/truncation before supplying args.
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
            signature += " args:sha256:" + _argument_fingerprint(args)
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
            evidence_event_ids = [e.get("event_id") or MISSING_EVENT_ID for e in evidence_events]
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
