"""Create, install, and mutate stored runs."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

from maida._storage.errors import RunValidationError
from maida._storage.paths import (
    EVENTS_JSONL,
    META_JSON,
    RUN_JSON,
    SPANS_JSONL,
    _atomic_write_json,
    _legacy_run_dir,
    _meta_path,
    _parse_iso_z,
    _runs_dir,
    _trace_dir,
    _validate_trace_id,
)
from maida.config import MaidaConfig
from maida.constants import SPEC_VERSION, default_counts
from maida.events import utc_now_iso_ms_z
from maida.trace_validation import (
    TraceValidationError,
    format_diagnostic_problem,
    validate_trace_payload,
)


def append_event(run_id: str, event: dict, config: MaidaConfig) -> None:
    """Compatibility wrapper for legacy event append callers."""
    path = _legacy_run_dir(run_id, config) / EVENTS_JSONL
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


def create_run(run_name: str, config: MaidaConfig) -> dict:
    """Compatibility wrapper for legacy run.json callers."""
    run_id = str(uuid.uuid4())
    run_dir = _legacy_run_dir(run_id, config)
    meta = {
        "spec_version": SPEC_VERSION,
        "run_id": run_id,
        "run_name": run_name,
        "started_at": utc_now_iso_ms_z(),
        "ended_at": None,
        "duration_ms": None,
        "status": "running",
        "counts": default_counts(),
        "last_event_ts": None,
        "paths": {
            "run_json": str(run_dir / RUN_JSON),
            "events_jsonl": str(run_dir / EVENTS_JSONL),
        },
    }
    _atomic_write_json(run_dir / RUN_JSON, meta)
    (run_dir / EVENTS_JSONL).touch()
    return meta


def finalize_run(run_id: str, status: str, counts: dict[str, int], config: MaidaConfig) -> dict:
    """Compatibility wrapper for legacy run finalization callers."""
    path = _legacy_run_dir(run_id, config) / RUN_JSON
    if not path.is_file():
        raise FileNotFoundError(f"No run found for run_id '{run_id}'")
    with open(path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    ended_at = utc_now_iso_ms_z()
    started_dt = _parse_iso_z(meta.get("started_at"))
    ended_dt = _parse_iso_z(ended_at)
    duration_ms = None
    if started_dt and ended_dt:
        duration_ms = max(0, int((ended_dt - started_dt).total_seconds() * 1000))
    meta.update(
        {
            "ended_at": ended_at,
            "duration_ms": duration_ms,
            "status": status,
            "counts": counts,
        }
    )
    _atomic_write_json(path, meta)
    return meta


def install_validated_run(meta: dict, spans: list[dict], config: MaidaConfig) -> Path:
    """Atomically install a complete externally-produced current-format run.

    The payload is validated before any filesystem mutation. Existing run
    directories are never replaced; callers may inspect an existing run and
    decide whether their import is idempotent before calling this function.
    """
    trace_id = _validate_trace_id(meta.get("trace_id"))
    try:
        validated = validate_trace_payload(meta, spans)
    except TraceValidationError as exc:
        raise RunValidationError(trace_id, format_diagnostic_problem(exc.diagnostics[0])) from exc
    meta = validated.meta
    spans = validated.spans

    runs_dir = _runs_dir(config)
    final_dir = _trace_dir(trace_id, config)
    if final_dir.exists():
        raise FileExistsError(f"Run {trace_id} already exists")

    runs_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = runs_dir / f".{trace_id}.{uuid.uuid4().hex}.tmp"
    staging_dir.mkdir()
    try:
        _atomic_write_json(staging_dir / META_JSON, meta)
        spans_path = staging_dir / SPANS_JSONL
        with open(spans_path, "w", encoding="utf-8") as f:
            for span in spans:
                f.write(json.dumps(span, ensure_ascii=False, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())

        if final_dir.exists():
            raise FileExistsError(f"Run {trace_id} already exists")
        os.replace(staging_dir, final_dir)
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
    return final_dir


def get_run_paths(trace_id: str, config: MaidaConfig) -> dict:
    """Return local filesystem paths for a run."""
    trace_dir = _trace_dir(trace_id, config)
    meta_p = trace_dir / META_JSON
    spans_p = trace_dir / SPANS_JSONL
    if not meta_p.is_file():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    return {
        "run_dir": str(trace_dir),
        "meta_json": str(meta_p),
        "spans_jsonl": str(spans_p),
    }


def rename_run(trace_id: str, run_name: str, config: MaidaConfig) -> dict:
    """Update meta.json with a new run_name."""
    path = _meta_path(trace_id, config)
    if not path.is_file():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    new_name = (run_name or "").strip()
    if not new_name:
        raise ValueError("run_name must be non-empty")
    with open(path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    meta["run_name"] = new_name
    _atomic_write_json(path, meta)
    return meta


def delete_run(trace_id: str, config: MaidaConfig) -> None:
    """Delete a run directory and all its contents."""
    run_dir = _trace_dir(trace_id, config)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    shutil.rmtree(run_dir)
