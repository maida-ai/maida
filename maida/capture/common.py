"""Shared passive capture redaction, persistence, identity, and locks."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from maida._file_lock import file_lock
from maida._tracing.redact import _key_matches_redact, _truncate_string
from maida.config import MaidaConfig
from maida.constants import REDACTED_MARKER, TRUNCATED_MARKER

_TOKEN_COUNTERS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "result_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "totalTokens",
    }
)


_JSON_CONTENT_FIELDS = frozenset(
    {
        "body",
        "hook_definitions",
        "tool_input",
        "tool_parameters",
    }
)


class CaptureValidationError(ValueError):
    """An OTLP batch does not satisfy the Claude Code capture contract."""


class CaptureConflictError(RuntimeError):
    """A source identity was delivered again with different content."""


def _json_sanitize_string(value: str, config: MaidaConfig) -> str:
    try:
        decoded = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return _truncate_string(value, config.max_field_bytes)
    return json.dumps(
        _sanitize(decoded, config),
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _sanitize(value: Any, config: MaidaConfig, *, key: str | None = None, depth: int = 0) -> Any:
    if depth > 12:
        return TRUNCATED_MARKER
    if key in _TOKEN_COUNTERS and isinstance(value, int) and not isinstance(value, bool):
        return value
    if key is not None and config.redact and _key_matches_redact(key, config.redact_keys):
        return REDACTED_MARKER
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if key in _JSON_CONTENT_FIELDS:
            return _json_sanitize_string(value, config)
        return _truncate_string(value, config.max_field_bytes)
    if isinstance(value, dict):
        return {
            str(child_key): _sanitize(
                child_value,
                config,
                key=str(child_key),
                depth=depth + 1,
            )
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(item, config, depth=depth + 1) for item in value]
    return _truncate_string(str(value), config.max_field_bytes)


def _session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def _canonical(record: dict[str, Any]) -> str:
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise CaptureConflictError(f"existing capture {path} line {line_no} is invalid JSON") from exc
            if not isinstance(value, dict):
                raise CaptureConflictError(f"existing capture {path} line {line_no} is not an object")
            records.append(value)
    return records


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _atomic_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(_canonical(record) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


_PROCESS_LOCK = threading.RLock()


@contextmanager
def _capture_lock(root: Path, namespace: str = "claude-code") -> Iterator[None]:
    with file_lock(root / f".{namespace}.lock", _PROCESS_LOCK):
        yield
