"""Passive Claude Code command-hook capture fallback."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from maida.capture.claude_code import (
    _atomic_json,
    _atomic_jsonl,
    _canonical,
    _capture_lock,
    _read_jsonl,
    _sanitize,
    _session_hash,
)
from maida.config import MaidaConfig
from maida.integrations.claude_code import ClaudeImportResult, import_claude_capture

DEFAULT_MAX_HOOK_BYTES = 8 * 1024 * 1024
_SERVICE_NAME = "claude-code"
_EVENT_NAMES = {
    "SessionStart": "claude_code.hook.session_start",
    "PreToolUse": "claude_code.hook.pre_tool_use",
    "PostToolUse": "claude_code.hook.post_tool_use",
    "PostToolUseFailure": "claude_code.hook.post_tool_use_failure",
    "PermissionDenied": "claude_code.hook.permission_denied",
    "SessionEnd": "claude_code.hook.session_end",
}
_TOOL_EVENTS = frozenset(
    {"PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied"}
)
_SESSION_SOURCES = frozenset({"startup", "resume", "clear", "compact", "fork"})
_OMITTED_SOURCE_FIELDS = frozenset({"session_id", "transcript_path"})


class ClaudeHookInputError(ValueError):
    """A Claude hook stdin payload is missing, malformed, or unsupported."""


class ClaudeHookConflictError(RuntimeError):
    """A hook delivery reused an identity with different sanitized content."""


class ClaudeHookImportError(RuntimeError):
    """A completed hook capture could not be imported automatically."""


@dataclass(frozen=True)
class ClaudeHookCaptureResult:
    session_hash: str
    segment: str
    accepted: bool
    import_result: ClaudeImportResult | None = None


def _require_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ClaudeHookInputError(f"{field} must be a nonempty string")
    return value.strip()


def _validate_payload(payload: Any) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise ClaudeHookInputError("hook payload must be a JSON object")
    session_id = _require_string(payload, "session_id")
    event = _require_string(payload, "hook_event_name")
    if event not in _EVENT_NAMES:
        raise ClaudeHookInputError(f"unsupported hook event {event!r}")

    for field in (
        "cwd",
        "prompt_id",
        "permission_mode",
        "agent_id",
        "agent_type",
        "model",
        "session_title",
    ):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            raise ClaudeHookInputError(f"{field} must be a string")

    if event == "SessionStart":
        source = _require_string(payload, "source")
        if source not in _SESSION_SOURCES:
            raise ClaudeHookInputError("SessionStart source is invalid")
    elif event in _TOOL_EVENTS:
        _require_string(payload, "tool_name")
        _require_string(payload, "tool_use_id")
        if not isinstance(payload.get("tool_input"), dict):
            raise ClaudeHookInputError("tool_input must be a JSON object")

    if event == "PostToolUse" and "tool_response" not in payload:
        raise ClaudeHookInputError("PostToolUse tool_response is required")
    if event == "PostToolUseFailure":
        _require_string(payload, "error")
        interrupted = payload.get("is_interrupt")
        if interrupted is not None and not isinstance(interrupted, bool):
            raise ClaudeHookInputError("is_interrupt must be a boolean")
    if event in {"PermissionDenied", "SessionEnd"}:
        _require_string(payload, "reason")

    duration = payload.get("duration_ms")
    if duration is not None and (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or duration < 0
    ):
        raise ClaudeHookInputError("duration_ms must be nonnegative")
    return session_id, event


def parse_claude_hook_json(
    raw: str,
    config: MaidaConfig,
    *,
    max_hook_bytes: int = DEFAULT_MAX_HOOK_BYTES,
) -> ClaudeHookCaptureResult:
    """Parse exactly one JSON value and capture it without emitting a decision."""
    if len(raw.encode("utf-8")) > max_hook_bytes:
        raise ClaudeHookInputError("hook payload is too large")
    if not raw.strip():
        raise ClaudeHookInputError("hook payload is empty")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ClaudeHookInputError(
            "hook payload must contain exactly one JSON object"
        ) from exc
    return capture_claude_hook(payload, config)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaudeHookConflictError(f"existing hook state {path} is invalid") from exc
    if not isinstance(value, dict):
        raise ClaudeHookConflictError(f"existing hook state {path} is invalid")
    return value


def _next_segment(session_dir: Path) -> str:
    numbers = (
        [
            int(entry.name)
            for entry in session_dir.iterdir()
            if entry.is_dir() and entry.name.isdigit()
        ]
        if session_dir.is_dir()
        else []
    )
    return f"{max(numbers, default=0) + 1:04d}"


def _safe_payload(payload: dict[str, Any], config: MaidaConfig) -> dict[str, Any]:
    source = {
        key: value
        for key, value in payload.items()
        if key not in _OMITTED_SOURCE_FIELDS
    }
    sanitized = _sanitize(source, config)
    if not isinstance(sanitized, dict):  # pragma: no cover - source is always a dict
        raise ClaudeHookInputError("hook payload could not be sanitized")
    return sanitized


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _delivery_id(event: str, payload: dict[str, Any]) -> str:
    if event in _TOOL_EVENTS:
        parts = (event, payload.get("prompt_id"), payload["tool_use_id"])
    elif event == "SessionStart":
        parts = (event, payload["source"])
    else:
        parts = (event, payload.get("reason"))
    return hashlib.sha256(repr(parts).encode("utf-8")).hexdigest()


def _iso_from_nanos(value: int) -> str:
    return (
        datetime.fromtimestamp(value / 1_000_000_000, timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _find_delivery(
    records: list[dict[str, Any]], delivery_id: str
) -> dict[str, Any] | None:
    for record in records:
        attributes = record.get("record", {}).get("attributes", {})
        if attributes.get("hook.delivery_id") == delivery_id:
            return record
    return None


def _record(
    *,
    safe_payload: dict[str, Any],
    session_hash: str,
    event: str,
    delivery_id: str,
    fingerprint: str,
    sequence: int,
    now_nanos: int,
    config: MaidaConfig,
) -> dict[str, Any]:
    attributes = dict(safe_payload)
    prompt_id = attributes.get("prompt_id")
    attributes.update(
        {
            "session.id": session_hash,
            "event.name": f"hook.{_EVENT_NAMES[event].rsplit('.', 1)[-1]}",
            "event.sequence": sequence,
            "event.timestamp": _iso_from_nanos(now_nanos),
            "hook.delivery_id": delivery_id,
            "hook.payload_fingerprint": fingerprint,
        }
    )
    if prompt_id:
        attributes["prompt.id"] = prompt_id
    return _sanitize(
        {
            "resource": {
                "attributes": {
                    "service.name": _SERVICE_NAME,
                    "capture.transport": "claude-hook",
                },
                "dropped_attributes_count": 0,
                "schema_url": "",
            },
            "scope": {
                "name": "maida.capture.claude-hook",
                "version": "1",
                "attributes": {},
                "dropped_attributes_count": 0,
            },
            "record": {
                "event_name": _EVENT_NAMES[event],
                "time_unix_nano": now_nanos,
                "observed_time_unix_nano": now_nanos,
                "severity_number": 0,
                "severity_text": "",
                "body": _EVENT_NAMES[event],
                "attributes": attributes,
                "dropped_attributes_count": 0,
                "flags": 0,
                "trace_id": "",
                "span_id": "",
            },
            "session_hash": session_hash,
        },
        config,
    )


def _write_manifest(
    segment_dir: Path,
    *,
    session_hash: str,
    segment: str,
    log_count: int,
    state: str,
    now: str,
) -> None:
    path = segment_dir / "manifest.json"
    manifest = {
        "capture_version": 1,
        "source": _SERVICE_NAME,
        "session_hash": session_hash,
        "segment": segment,
        "capture_transport": "claude-hook",
        "created_at": now,
        "updated_at": now,
        "state": state,
        "signals": {"logs": log_count},
    }
    if path.is_file():
        current = _read_json(path)
        manifest.update(current)
        manifest["updated_at"] = now
        manifest["state"] = state
        signals = dict(manifest.get("signals") or {})
        signals["logs"] = log_count
        manifest["signals"] = signals
    if state == "closed":
        manifest["closed_at"] = now
    _atomic_json(path, manifest)


def _close_segment(session_dir: Path, segment: str, now: str) -> None:
    segment_dir = session_dir / segment
    manifest = _read_json(segment_dir / "manifest.json")
    if not manifest:
        return
    manifest["state"] = "closed"
    manifest["updated_at"] = now
    manifest["closed_at"] = now
    _atomic_json(segment_dir / "manifest.json", manifest)


def capture_claude_hook(
    payload: Any,
    config: MaidaConfig,
) -> ClaudeHookCaptureResult:
    """Validate and append one passive Claude command-hook delivery."""
    session_id, event = _validate_payload(payload)
    safe_payload = _safe_payload(payload, config)
    session_hash = _session_hash(session_id)
    fingerprint = _payload_fingerprint(safe_payload)
    delivery_id = _delivery_id(event, safe_payload)
    captures_root = config.data_dir.expanduser() / "captures"
    session_dir = captures_root / _SERVICE_NAME / session_hash
    state_path = session_dir / ".hook-state.json"
    now_nanos = time.time_ns()
    now = _iso_from_nanos(now_nanos)

    with _capture_lock(captures_root):
        state = _read_json(state_path)
        active = state.get("active_segment")
        if not isinstance(active, str):
            active = None

        if event == "SessionStart" and safe_payload["source"] != "compact":
            if (
                active is not None
                and state.get("last_start_delivery_id") == delivery_id
            ):
                segment = active
            else:
                if active is not None:
                    _close_segment(session_dir, active, now)
                segment = _next_segment(session_dir)
                active = segment
                state["active_segment"] = segment
        elif event == "SessionEnd" and active is None:
            previous = state.get("last_closed_segment")
            if isinstance(previous, str):
                existing = _read_jsonl(session_dir / previous / "logs.jsonl")
                duplicate = _find_delivery(existing, delivery_id)
                segment = (
                    previous if duplicate is not None else _next_segment(session_dir)
                )
            else:
                segment = _next_segment(session_dir)
            active = segment
            state["active_segment"] = segment
        elif active is None:
            segment = _next_segment(session_dir)
            active = segment
            state["active_segment"] = segment
        else:
            segment = active

        segment_dir = session_dir / segment
        logs_path = segment_dir / "logs.jsonl"
        existing = _read_jsonl(logs_path)
        previous = _find_delivery(existing, delivery_id)
        accepted = previous is None
        if previous is not None:
            previous_fingerprint = previous["record"]["attributes"].get(
                "hook.payload_fingerprint"
            )
            if previous_fingerprint != fingerprint:
                raise ClaudeHookConflictError(
                    "conflicting duplicate Claude hook delivery identity"
                )
        else:
            sequences = [
                record.get("record", {}).get("attributes", {}).get("event.sequence", 0)
                for record in existing
            ]
            sequence = (
                max(
                    (
                        value
                        for value in sequences
                        if isinstance(value, int) and not isinstance(value, bool)
                    ),
                    default=0,
                )
                + 1
            )
            incoming = _record(
                safe_payload=safe_payload,
                session_hash=session_hash,
                event=event,
                delivery_id=delivery_id,
                fingerprint=fingerprint,
                sequence=sequence,
                now_nanos=now_nanos,
                config=config,
            )
            existing.append(incoming)
            _atomic_jsonl(logs_path, existing)

        capture_state = "closed" if event == "SessionEnd" else "active"
        _write_manifest(
            segment_dir,
            session_hash=session_hash,
            segment=segment,
            log_count=len(existing),
            state=capture_state,
            now=now,
        )

        if event == "SessionStart" and safe_payload["source"] != "compact":
            state["last_start_delivery_id"] = delivery_id
            state["last_start_fingerprint"] = fingerprint
        import_result = None
        if event == "SessionEnd":
            state["active_segment"] = None
            state["last_closed_segment"] = segment
        state["session_hash"] = session_hash
        state["updated_at"] = now
        _atomic_json(state_path, state)

        if event == "SessionEnd":
            try:
                import_result = import_claude_capture(
                    session_id,
                    config,
                    segment=segment,
                )
            except Exception as exc:
                raise ClaudeHookImportError(
                    f"completed Claude hook capture could not be imported: {exc}"
                ) from exc

    return ClaudeHookCaptureResult(
        session_hash=session_hash,
        segment=segment,
        accepted=accepted,
        import_result=import_result,
    )
