"""Explicit per-capture state shared by ordered normalization stages."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from maida.config import MaidaConfig
from maida.integrations._claude_code.loading import _hash_id
from maida.integrations._claude_code.types import ClaudeCaptureImportError, ClaudeCaptureSegment


@dataclass
class NormalizationContext:
    capture: ClaudeCaptureSegment
    config: MaidaConfig
    trace_id: str = field(init=False)
    root_span_id: str = field(init=False)
    run_name: str = field(init=False)
    used_ids: set[str] = field(default_factory=set)
    interaction_by_group: dict[str, str] = field(default_factory=dict)
    interaction_times: dict[str, list[datetime]] = field(default_factory=dict)
    actual_interactions: set[str] = field(default_factory=set)
    normalized: list[dict[str, Any]] = field(default_factory=list)
    normalized_by_source: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    action_spans: list[dict[str, Any]] = field(default_factory=list)
    ordered_logs: list[dict[str, Any]] = field(default_factory=list)
    consumed_hook_logs: set[str] = field(default_factory=set)

    def __post_init__(self):
        self.trace_id = _hash_id("claude-code", self.capture.session_hash, self.capture.segment, length=32)
        self.root_span_id = _hash_id(self.trace_id, "session-root", length=16)
        self.run_name = f"claude-code:{self.capture.session_hash[:12]}:{self.capture.segment}"
        self.used_ids.add(self.root_span_id)

    def span_id(self, key: str) -> str:
        value = _hash_id(self.trace_id, key, length=16)
        if value in self.used_ids:
            raise ClaudeCaptureImportError("deterministic Claude span ID collision")
        self.used_ids.add(value)
        return value

    def ensure_interaction(self, group: str) -> str:
        current = self.interaction_by_group.get(group)
        if current is None:
            current = self.span_id(f"synthetic-interaction:{group}")
            self.interaction_by_group[group] = current
        return current
