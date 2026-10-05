"""Run directory layout, ID validation, and atomic JSON writes."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from maida.config import MaidaConfig

META_JSON = "meta.json"
SPANS_JSONL = "spans.jsonl"
RUN_JSON = "run.json"
EVENTS_JSONL = "events.jsonl"

_TRACE_ID_LEN = 32
_SPAN_ID_LEN = 16
_HEX_CHARS = frozenset("0123456789abcdef")


def _validate_hex_id(value: object, length: int, field_name: str) -> str:
    """Validate that *value* is a fixed-length lowercase hex string.

    The hex charset + length check also rejects every path-traversal character
    (``.``, ``/``, ``\\`` are not hex digits), so callers need no separate guard.
    """
    if not value or not isinstance(value, str):
        raise ValueError(f"invalid {field_name}")
    v = value.strip().lower()
    if len(v) != length or not _HEX_CHARS.issuperset(v):
        raise ValueError(f"invalid {field_name}")
    return v


def _validate_trace_id(trace_id: str) -> str:
    """Validate that trace_id is a 32-char hex string (no path traversal)."""
    return _validate_hex_id(trace_id, _TRACE_ID_LEN, "trace_id")


def _validate_span_id(span_id: str, *, field_name: str) -> str:
    return _validate_hex_id(span_id, _SPAN_ID_LEN, field_name)


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _runs_dir(config: MaidaConfig) -> Path:
    return config.data_dir.expanduser() / "runs"


def _trace_dir(trace_id: str, config: MaidaConfig) -> Path:
    trace_id = _validate_trace_id(trace_id)
    base = _runs_dir(config)
    path = base / trace_id
    try:
        resolved = path.resolve()
        base_resolved = base.resolve()
        if not resolved.is_relative_to(base_resolved):
            raise ValueError("invalid trace_id")
    except (ValueError, OSError):
        raise ValueError("invalid trace_id")
    return path


def _legacy_run_dir(run_id: str, config: MaidaConfig) -> Path:
    if not run_id or not isinstance(run_id, str):
        raise ValueError("invalid run_id")
    rid = run_id.strip()
    if not rid or ".." in rid or "/" in rid or "\\" in rid:
        raise ValueError("invalid run_id")
    return _runs_dir(config) / rid


def _meta_path(trace_id: str, config: MaidaConfig) -> Path:
    return _trace_dir(trace_id, config) / META_JSON


def _spans_path(trace_id: str, config: MaidaConfig) -> Path:
    return _trace_dir(trace_id, config) / SPANS_JSONL


def _parse_iso_z(s: str) -> datetime | None:
    """Parse ISO8601 UTC timestamp with optional trailing Z."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    try:
        normalized = s.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except (ValueError, TypeError):
        return None
