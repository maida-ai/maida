"""Read-only claude_code import: types."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MAPPING_VERSION = 2


_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


_TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


_SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")


_KNOWN_LOGS = frozenset(
    {
        "claude_code.user_prompt",
        "claude_code.assistant_response",
        "claude_code.tool_result",
        "claude_code.api_request",
        "claude_code.api_error",
        "claude_code.api_refusal",
        "claude_code.tool_decision",
        "claude_code.hook.session_start",
        "claude_code.hook.pre_tool_use",
        "claude_code.hook.post_tool_use",
        "claude_code.hook.post_tool_use_failure",
        "claude_code.hook.permission_denied",
        "claude_code.hook.session_end",
    }
)


_HOOK_TOOL_LOGS = frozenset(
    {
        "claude_code.hook.pre_tool_use",
        "claude_code.hook.post_tool_use",
        "claude_code.hook.post_tool_use_failure",
        "claude_code.hook.permission_denied",
    }
)


_NONNEGATIVE_FIELDS = frozenset(
    {
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "cache_read_tokens",
        "cache_creation_tokens",
        "result_tokens",
        "prompt_length",
        "response_length",
        "tool_input_size_bytes",
        "tool_result_size_bytes",
        "event.sequence",
        "attempt",
        "cost_usd",
        "cost_usd_micros",
    }
)


_KIND_NAMES = {
    0: "INTERNAL",
    1: "INTERNAL",
    2: "SERVER",
    3: "CLIENT",
    4: "PRODUCER",
    5: "CONSUMER",
}


class ClaudeCaptureInputError(ValueError):
    """A selected local capture is missing or malformed."""


class ClaudeCaptureImportError(RuntimeError):
    """A valid capture could not be installed safely."""


class ClaudeCaptureChangedError(ClaudeCaptureInputError):
    """The capture changed after the deterministic run was installed."""


@dataclass(frozen=True)
class ClaudeCaptureSegment:
    path: Path
    session_hash: str
    segment: str
    manifest: dict[str, Any]
    logs: list[dict[str, Any]]
    spans: list[dict[str, Any]]
    source_fingerprint: str
    service_versions: tuple[str, ...]


@dataclass(frozen=True)
class NormalizedClaudeRun:
    trace_id: str
    session_hash: str
    segment: str
    source_fingerprint: str
    run_name: str
    meta: dict[str, Any]
    spans: list[dict[str, Any]]


@dataclass(frozen=True)
class ClaudeImportResult:
    trace_id: str
    run_name: str
    session_hash: str
    segment: str
    source_fingerprint: str
    imported: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_name": self.run_name,
            "session_hash": self.session_hash,
            "segment": self.segment,
            "source_fingerprint": self.source_fingerprint,
            "imported": self.imported,
        }
