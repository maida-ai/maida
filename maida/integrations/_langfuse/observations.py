"""Observation identity and deduplication shared by transport and mapping."""

from typing import Any

from maida.integrations._langfuse.types import LangfuseImportError


def _deduplicate_observations(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in rows:
        observation_id = row.get("id")
        if not isinstance(observation_id, str) or not observation_id:
            raise LangfuseImportError("Langfuse observation is missing a string id")
        previous = by_id.get(observation_id)
        if previous is not None and previous != row:
            raise LangfuseImportError(f"Langfuse returned conflicting rows for observation {observation_id!r}")
        if previous is None:
            order.append(observation_id)
            by_id[observation_id] = row
    return [by_id[observation_id] for observation_id in order]
