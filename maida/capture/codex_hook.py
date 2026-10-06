"""Passive native Codex hooks; task completion is a root turn Stop."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maida.capture.common import (
    CaptureConflictError,
    _atomic_json,
    _atomic_jsonl,
    _canonical,
    _capture_lock,
    _json_sanitize_string,
    _sanitize,
    _session_hash,
)
from maida.config import MaidaConfig

from maida.capture.codex_state import (
    CodexHookConflictError as CodexHookConflictError,
    CodexHookInputError as CodexHookInputError,
    _completion as _completion,
    _read_state as _read_state,
    _string as _string,
    read_turn_records as read_turn_records,
    receipt_path as receipt_path,
    turn_dir as turn_dir,
    _EVENTS,
    _TOOLS,
)

DEFAULT_MAX_HOOK_BYTES = 8 * 1024 * 1024
# Lifecycle fields are an explicit allowlist: conversational text and transcript
# locations never reach disk, even when redaction is disabled. Tools carry
# sanitized behavioral inputs/results, plus the identities required for pairing.
_LIFECYCLE_FIELDS = frozenset(
    {"hook_event_name", "source", "model", "model_version", "version", "reason", "stop_hook_active"}
)
_TOOL_FIELDS = frozenset(
    {"hook_event_name", "tool_use_id", "tool_name", "tool_input", "tool_response", "duration_ms", "error", "reason"}
)


@dataclass(frozen=True)
class CodexHookCaptureResult:
    session_hash: str
    turn_hash: str | None
    accepted: bool
    receipt_path: Path | None


def parse_codex_hook_json(
    raw: str, config: MaidaConfig, *, max_hook_bytes: int = DEFAULT_MAX_HOOK_BYTES
) -> CodexHookCaptureResult:
    """Capture one bounded stdin delivery synchronously, without importing it."""
    if len(raw.encode("utf-8")) > max_hook_bytes:
        raise CodexHookInputError("hook payload is too large")
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise CodexHookInputError("hook payload must contain exactly one JSON object") from exc
    return capture_codex_hook(payload, config)


def capture_codex_hook(payload: Any, config: MaidaConfig) -> CodexHookCaptureResult:
    """Record redacted source evidence and a cheap task receipt, never a verdict."""
    if not isinstance(payload, dict):
        raise CodexHookInputError("hook payload must be a JSON object")
    session_hash = _session_hash(_canonical([config.project_id, _string(payload, "session_id")]))
    event = _string(payload, "hook_event_name")
    if event not in _EVENTS:
        raise CodexHookInputError(f"unsupported hook event {event!r}")
    if event in _TOOLS:
        _string(payload, "tool_name")
        _string(payload, "tool_use_id")
        if "tool_input" not in payload:
            raise CodexHookInputError("tool_input is required")
    if event == "PostToolUse" and "tool_response" not in payload:
        raise CodexHookInputError("tool_response is required")
    for field in ("agent_id", "agent_type", "parent_agent_id", "event_id", "cwd"):
        if payload.get(field) is not None and not isinstance(payload[field], str):
            raise CodexHookInputError(f"{field} must be a string")
    for field in ("stop_hook_active", "is_interrupt"):
        if payload.get(field) is not None and not isinstance(payload[field], bool):
            raise CodexHookInputError(f"{field} must be a boolean")
    duration = payload.get("duration_ms")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration < 0):
        raise CodexHookInputError("duration_ms must be nonnegative")
    turn_id = payload.get("turn_id", payload.get("prompt_id"))
    if event not in {"SessionStart", "SessionEnd"} and (not isinstance(turn_id, str) or not turn_id.strip()):
        raise CodexHookInputError("turn_id must be a nonempty string")
    source_keys = {
        key: _session_hash(value)
        for key in ("tool_use_id", "tool_name", "agent_id", "event_id")
        if isinstance((value := payload.get(key)), str) and value
    }
    fields = _TOOL_FIELDS if event in _TOOLS else _LIFECYCLE_FIELDS
    source = {key: value for key, value in payload.items() if key in fields}
    if isinstance(source.get("tool_response"), str):
        source["tool_response"] = _json_sanitize_string(source["tool_response"], config)
    safe = _sanitize(source, config)
    captures_root = config.data_dir.expanduser() / "captures"
    session_dir = captures_root / "codex" / session_hash
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with _capture_lock(captures_root, "codex"):
        session_state = _read_state(session_dir / "session.json")
        if event == "SessionStart":
            session_state.update(session_hash=session_hash, source=safe, updated_at=now)
            _atomic_json(session_dir / "session.json", session_state)
            return CodexHookCaptureResult(session_hash, None, True, None)
        if event == "SessionEnd" and not turn_id:
            # Closing a session cannot close any open root task successfully.
            turn_hash = session_state.get("active_turn")
            if not turn_hash:
                return CodexHookCaptureResult(session_hash, None, False, None)
        else:
            if not isinstance(turn_id, str) or not turn_id.strip():
                raise CodexHookInputError("turn_id must be a nonempty string")
            turn_hash = _session_hash(turn_id.strip())
        path = receipt_path(config, session_hash, turn_hash)
        current = _read_state(path)
        directory = turn_dir(config, session_hash, turn_hash)
        try:
            records = read_turn_records(directory / "events.jsonl")
        except CaptureConflictError as exc:
            current.update(
                provider="codex",
                started_at=current.get("started_at", now),
                state="corrupt",
                has_start=False,
                complete_tools=False,
            )
            _atomic_json(path, current)
            raise CodexHookConflictError(str(exc)) from exc
        generation = sum(1 for item in records if item["event"] != "Stop")
        if event in _TOOLS:
            identity = (event, source_keys.get("agent_id"), source_keys["tool_use_id"])
        elif payload.get("event_id"):
            identity = (event, source_keys.get("agent_id"), source_keys["event_id"])
        elif event == "Stop":
            identity = (event, source_keys.get("agent_id"), generation, _session_hash(_canonical(safe)))
        else:
            identity = (event, source_keys.get("agent_id"), _session_hash(_canonical(safe)))
        delivery_id = _session_hash(repr(identity))
        fingerprint = _session_hash(_canonical({"payload": safe, "source_keys": source_keys}))
        previous = next((item for item in records if item["delivery_id"] == delivery_id), None)
        if previous and previous["fingerprint"] != fingerprint:
            current["state"] = "corrupt"
            current["trace_id"] = None
            _atomic_json(path, current)
            raise CodexHookConflictError("conflicting duplicate Codex hook delivery identity")
        accepted = previous is None
        if accepted:
            records.append(
                {
                    "event": event,
                    "payload": safe,
                    "source_keys": source_keys,
                    "delivery_id": delivery_id,
                    "fingerprint": fingerprint,
                    "observed_at": now,
                }
            )
            _atomic_jsonl(directory / "events.jsonl", records)
        state, has_start, complete_tools = _completion(records)
        receipt = {
            "provider": "codex",
            "started_at": current.get("started_at", now),
            "updated_at": now,
            "state": "corrupt" if current.get("state") == "corrupt" else state,
            "has_start": has_start,
            "complete_tools": complete_tools,
            "session_hash": session_hash,
            "turn_hash": turn_hash,
            "project_id": config.project_id,
            "session_context": current.get("session_context", session_state.get("source", {})),
            "trace_id": current.get("trace_id") if not accepted else None,
        }
        _atomic_json(path, receipt)
        session_state.update(active_turn=turn_hash, updated_at=now)
        _atomic_json(session_dir / "session.json", session_state)
    return CodexHookCaptureResult(session_hash, turn_hash, accepted, path)


def materialize_turn(config: MaidaConfig, receipt: dict) -> str:
    """Validate and install an immutable content-addressed turn snapshot."""
    from maida.integrations.codex import import_codex_turn

    return import_codex_turn(config, receipt)
