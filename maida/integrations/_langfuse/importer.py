"""Read-only langfuse import: importer."""

from __future__ import annotations

import json
from typing import Any

from maida._tracing.attributes import (
    MAIDA_META,
)
from maida.config import MaidaConfig
from maida.integrations._langfuse.client import LangfuseClient
from maida.integrations._langfuse.normalize import _iso_utc, _parse_timestamp, normalize_langfuse_trace
from maida.integrations._langfuse.types import (
    _MAPPING_VERSION,
    IncompleteLangfuseTrace,
    LangfuseImportError,
    LangfuseImportSummary,
    LangfuseInputError,
    NormalizedLangfuseRun,
)
from maida.storage import install_validated_run, load_validated_run


def _parse_selection_time(value: str, *, option: str) -> str:
    try:
        parsed = _parse_timestamp(value, field_name=option)
    except LangfuseImportError as exc:
        raise LangfuseInputError(str(exc)) from exc
    return _iso_utc(parsed)


def _discovery_filter(
    *,
    from_time: str,
    to_time: str,
    trace_name: str | None,
    session_id: str | None,
    environments: tuple[str, ...],
) -> str:
    from_iso = _parse_selection_time(from_time, option="--from")
    to_iso = _parse_selection_time(to_time, option="--to")
    if _parse_timestamp(from_iso, field_name="--from") >= _parse_timestamp(to_iso, field_name="--to"):
        raise LangfuseInputError("--from must be earlier than --to")
    filters: list[dict[str, Any]] = [
        {
            "type": "datetime",
            "column": "startTime",
            "operator": ">=",
            "value": from_iso,
        },
        {
            "type": "datetime",
            "column": "startTime",
            "operator": "<",
            "value": to_iso,
        },
    ]
    if trace_name:
        filters.append(
            {
                "type": "string",
                "column": "traceName",
                "operator": "=",
                "value": trace_name,
            }
        )
    if session_id:
        filters.append(
            {
                "type": "string",
                "column": "sessionId",
                "operator": "=",
                "value": session_id,
            }
        )
    if environments:
        filters.append(
            {
                "type": "stringOptions",
                "column": "environment",
                "operator": "any of",
                "value": list(environments),
            }
        )
    return json.dumps(filters, separators=(",", ":"))


def _existing_source_matches(run: NormalizedLangfuseRun, config: MaidaConfig) -> bool:
    try:
        _meta, spans = load_validated_run(run.trace_id, config)
    except FileNotFoundError:
        return False
    except Exception as exc:
        raise LangfuseImportError(f"Existing destination run {run.trace_id} is invalid") from exc
    root = next((span for span in spans if span.get("parent_span_id") is None), None)
    if root is None:
        raise LangfuseImportError(f"Existing destination run {run.trace_id} has no root span")
    raw_meta = root.get("attributes", {}).get(MAIDA_META)
    try:
        source = json.loads(raw_meta)["langfuse"]
    except (TypeError, KeyError, json.JSONDecodeError) as exc:
        raise LangfuseImportError(f"Existing destination run {run.trace_id} is not the same Langfuse import") from exc
    if source.get("trace_id") != run.source_trace_id or source.get("project_id") != run.project_id:
        raise LangfuseImportError(f"Deterministic trace ID collision at destination {run.trace_id}")
    if (
        source.get("mapping_version") != _MAPPING_VERSION
        or source.get("observation_fingerprint") != run.source_fingerprint
    ):
        raise LangfuseImportError(
            f"Langfuse source trace changed since destination {run.trace_id} was "
            "imported; refusing to overwrite the local run"
        )
    return True


def import_langfuse_traces(
    client: LangfuseClient,
    config: MaidaConfig,
    *,
    source_trace_id: str | None = None,
    from_time: str | None = None,
    to_time: str | None = None,
    trace_name: str | None = None,
    session_id: str | None = None,
    environments: tuple[str, ...] = (),
) -> LangfuseImportSummary:
    """Fetch, normalize, validate, and locally install selected Langfuse traces."""
    if source_trace_id:
        if any((from_time, to_time, trace_name, session_id, environments)):
            raise LangfuseInputError("--trace-id cannot be combined with range or grouping filters")
        source_ids = [source_trace_id]
        hydrated = {source_trace_id: client.fetch_observations({"traceId": source_trace_id})}
    else:
        if from_time is None or to_time is None:
            raise LangfuseInputError("Pass --trace-id or both timezone-aware --from and --to values")
        filter_json = _discovery_filter(
            from_time=from_time,
            to_time=to_time,
            trace_name=trace_name,
            session_id=session_id,
            environments=environments,
        )
        discovery_rows = client.fetch_observations({"filter": filter_json})
        source_ids = sorted(
            {str(row["traceId"]) for row in discovery_rows if isinstance(row.get("traceId"), str) and row["traceId"]}
        )
        hydrated = {trace_id: client.fetch_observations({"traceId": trace_id}) for trace_id in source_ids}
    if not source_ids:
        raise LangfuseInputError(
            "No Langfuse observations matched the selection. Check the time range "
            "and filters, or run `maida demo` to verify Maida locally."
        )

    summary = LangfuseImportSummary()
    prepared: list[NormalizedLangfuseRun] = []
    for trace_id in source_ids:
        rows = hydrated.get(trace_id) or []
        if not rows:
            summary.skipped.append({"source_trace_id": trace_id, "reason": "no observations"})
            continue
        try:
            run = normalize_langfuse_trace(rows, config)
        except IncompleteLangfuseTrace as exc:
            summary.skipped.append({"source_trace_id": trace_id, "reason": str(exc)})
            continue
        prepared.append(run)
        summary.unmapped_observation_types.update(run.unmapped_observation_types)

    pending: list[NormalizedLangfuseRun] = []
    for run in prepared:
        if _existing_source_matches(run, config):
            summary.skipped.append(
                {
                    "source_trace_id": run.source_trace_id,
                    "trace_id": run.trace_id,
                    "run_name": run.run_name,
                    "reason": "already imported",
                }
            )
            continue
        pending.append(run)

    for run in pending:
        try:
            install_validated_run(run.meta, run.spans, config)
        except FileExistsError as exc:
            raise LangfuseImportError(
                f"Destination run {run.trace_id} was created concurrently; rerun the import to verify it"
            ) from exc
        summary.imported.append(
            {
                "source_trace_id": run.source_trace_id,
                "trace_id": run.trace_id,
                "run_name": run.run_name,
            }
        )
    return summary
