"""Local storage for Maida runs using OpenTelemetry span data.

Each run (trace) is stored in: <data_dir>/runs/<trace_id_hex>/
  - meta.json: run metadata (run_name, status, counts, etc.)
  - spans.jsonl: one JSON span dict per line (written by MaidaLocalSpanExporter)

Note: trace_id_hex is 32 hex characters (128-bit trace ID).
"""

from maida._storage.errors import RunValidationError, UnsupportedTraceFormatError
from maida._storage.load import (
    load_events,
    load_run_for_analysis,
    load_run_meta,
    load_spans,
    load_validated_run,
)
from maida._storage.resolve import (
    list_runs,
    resolve_latest_run_id,
    resolve_latest_trace_id,
    resolve_run_id,
    resolve_trace_id,
    resolve_trace_id_for_read,
)
from maida._storage.writes import (
    append_event,
    create_run,
    delete_run,
    finalize_run,
    get_run_paths,
    install_validated_run,
    rename_run,
)

__all__ = [
    "RunValidationError",
    "UnsupportedTraceFormatError",
    "append_event",
    "create_run",
    "delete_run",
    "finalize_run",
    "get_run_paths",
    "install_validated_run",
    "list_runs",
    "load_events",
    "load_run_for_analysis",
    "load_run_meta",
    "load_spans",
    "load_validated_run",
    "rename_run",
    "resolve_latest_run_id",
    "resolve_latest_trace_id",
    "resolve_run_id",
    "resolve_trace_id",
    "resolve_trace_id_for_read",
]
