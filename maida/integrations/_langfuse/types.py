"""Read-only langfuse import: types."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

_OBSERVATION_FIELDS = "basic,time,io,metadata,model,usage,metrics,trace_context"


_MAPPING_VERSION = 1


_KNOWN_STRUCTURAL_TYPES = {
    "SPAN",
    "EVENT",
    "AGENT",
    "CHAIN",
    "RETRIEVER",
    "EVALUATOR",
    "EMBEDDING",
    "GUARDRAIL",
}


class LangfuseImportError(RuntimeError):
    """Langfuse data could not be fetched, normalized, or persisted safely."""


class LangfuseInputError(ValueError):
    """The user-provided Langfuse import selection is invalid."""


class IncompleteLangfuseTrace(LangfuseImportError):
    """A source trace still contains an unfinished non-event observation."""


@dataclass(frozen=True)
class NormalizedLangfuseRun:
    trace_id: str
    source_trace_id: str
    project_id: str
    source_fingerprint: str
    run_name: str
    meta: dict[str, Any]
    spans: list[dict[str, Any]]
    unmapped_observation_types: tuple[str, ...] = ()


@dataclass
class LangfuseImportSummary:
    imported: list[dict[str, str]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    unmapped_observation_types: set[str] = field(default_factory=set)

    def as_dict(self) -> dict[str, Any]:
        return {
            "imported": self.imported,
            "skipped": self.skipped,
            "unmapped_observation_types": sorted(self.unmapped_observation_types),
        }
