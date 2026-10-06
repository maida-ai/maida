"""Persisted Codex evidence validation shared by capture and replay."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from maida.capture.common import (
    CaptureConflictError,
    _canonical,
    _read_jsonl,
    _session_hash,
)
from maida.config import MaidaConfig

_EVENTS = frozenset(
    {
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "Interrupt",
    }
)


_TOOLS = frozenset({"PreToolUse", "PostToolUse"})


_TERMINALS = _TOOLS - {"PreToolUse"}


class CodexHookInputError(ValueError):
    """Hook evidence cannot be safely identified or materialized."""


class CodexHookConflictError(CaptureConflictError):
    """A source delivery identity has conflicting evidence."""


def _string(payload: dict, field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CodexHookInputError(f"{field} must be a nonempty string")
    return value.strip()


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, ValueError) as exc:
        raise CodexHookConflictError("existing Codex capture state is invalid") from exc
    raise CodexHookConflictError("existing Codex capture state is invalid")


def receipt_path(config: MaidaConfig, session_hash: str, turn_hash: str) -> Path:
    return config.data_dir.expanduser() / "onboarding" / f"codex-{session_hash}-{turn_hash}.json"


def turn_dir(config: MaidaConfig, session_hash: str, turn_hash: str) -> Path:
    return config.data_dir.expanduser() / "captures" / "codex" / session_hash / turn_hash


def _completion(records: list[dict]) -> tuple[str, bool, bool]:
    starts = set()
    terminals = set()
    state = "active"
    has_start = False
    for record in records:
        event = record["event"]
        child = record["source_keys"].get("agent_id")
        key = (child, record["source_keys"].get("tool_use_id"))
        if event == "UserPromptSubmit" and not child:
            has_start = True
        if event == "PreToolUse":
            starts.add(key)
        elif event in _TERMINALS:
            terminals.add(key)
        if event in _TOOLS or event == "UserPromptSubmit":
            state = "active"
        elif event == "Stop" and not child:
            state = "closed" if has_start and starts == terminals else "active"
        elif event == "Interrupt" and not child:
            state = "interrupted"
        elif event == "SessionEnd" and not child and state != "closed":
            state = "interrupted"
    return state, has_start, bool(starts) and starts == terminals


def read_turn_records(path: Path) -> list[dict]:
    """Reject malformed or inconsistent persisted deliveries before replay."""
    records = _read_jsonl(path)
    identities = set()
    for record in records:
        try:
            event, source = record["event"], record["payload"]
            if event not in _EVENTS or not isinstance(source, dict):
                raise ValueError("invalid event or source")
            for field in ("delivery_id", "fingerprint"):
                if not isinstance(record[field], str) or len(record[field]) != 64:
                    raise ValueError("invalid delivery identity")
            if not isinstance(record["source_keys"], dict) or any(
                not isinstance(value, str) or len(value) != 64 for value in record["source_keys"].values()
            ):
                raise ValueError("invalid structural identity")
            if record["fingerprint"] != _session_hash(
                _canonical({"payload": source, "source_keys": record["source_keys"]})
            ):
                raise ValueError("source fingerprint mismatch")
            if record["delivery_id"] in identities:
                raise ValueError("duplicate persisted delivery")
            identities.add(record["delivery_id"])
            datetime.fromisoformat(record["observed_at"].replace("Z", "+00:00"))
            if event in _TOOLS:
                _string(source, "tool_use_id")
                _string(source, "tool_name")
                for key in ("tool_use_id", "tool_name"):
                    if key not in record["source_keys"]:
                        raise ValueError("missing structural tool identity")
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise CodexHookConflictError("existing Codex turn evidence is malformed") from exc
    return records
