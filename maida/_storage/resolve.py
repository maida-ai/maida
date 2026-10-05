"""Run discovery and ID resolution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from maida._storage.paths import (
    META_JSON,
    RUN_JSON,
    _parse_iso_z,
    _runs_dir,
    _trace_dir,
    _validate_trace_id,
)
from maida.config import MaidaConfig


def list_runs(limit: int, config: MaidaConfig) -> list[dict]:
    """List most recent runs by started_at descending. Returns list of meta dicts."""
    runs_base = _runs_dir(config)
    if not runs_base.is_dir():
        return []

    candidates: list[tuple[datetime | None, dict]] = []
    for entry in runs_base.iterdir():
        if not entry.is_dir():
            continue
        trace_id = entry.name
        meta_f: Path
        try:
            _validate_trace_id(trace_id)
        except ValueError:
            meta_f = entry / RUN_JSON
        else:
            meta_f = entry / META_JSON
            if not meta_f.is_file():
                legacy_meta_f = entry / RUN_JSON
                if legacy_meta_f.is_file():
                    meta_f = legacy_meta_f
        if not meta_f.is_file():
            continue
        try:
            with open(meta_f, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        started_str = meta.get("started_at")
        started_dt = _parse_iso_z(started_str) if started_str else None
        candidates.append((started_dt, meta))

    def sort_key(item: tuple[datetime | None, dict]) -> tuple[bool, datetime]:
        dt, _ = item
        return (dt is None, dt or datetime.min.replace(tzinfo=timezone.utc))

    candidates.sort(key=sort_key, reverse=True)
    return [meta for _, meta in candidates[:limit]]


def _trace_candidates(config: MaidaConfig, prefix: str | None = None) -> list[tuple[datetime | None, str]]:
    """Collect (started_at, trace_id) for runs, newest first.

    When *prefix* is given, only trace IDs matching it are included.
    Raises FileNotFoundError if the runs directory does not exist.
    """
    runs_base = _runs_dir(config)
    if not runs_base.is_dir():
        raise FileNotFoundError(f"No runs directory at {runs_base}")

    candidates: list[tuple[datetime | None, str]] = []
    for entry in runs_base.iterdir():
        if not entry.is_dir():
            continue
        tid = entry.name
        try:
            _validate_trace_id(tid)
        except ValueError:
            continue
        if prefix is not None and tid != prefix and not tid.startswith(prefix):
            continue
        meta_f = entry / META_JSON
        if not meta_f.is_file():
            continue
        try:
            with open(meta_f, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        started_str = meta.get("started_at")
        started_dt = _parse_iso_z(started_str) if started_str else None
        candidates.append((started_dt, tid))

    def sort_key(item: tuple[datetime | None, str]) -> tuple[bool, datetime]:
        dt, _ = item
        return (dt is None, dt or datetime.min.replace(tzinfo=timezone.utc))

    candidates.sort(key=sort_key, reverse=True)
    return candidates


def resolve_trace_id(prefix: str, config: MaidaConfig) -> str:
    """Resolve a trace_id prefix (short or full) to the full 32-char hex trace_id.

    Raises FileNotFoundError if no match.
    """
    if not prefix or not prefix.strip():
        raise FileNotFoundError("Trace ID is required")
    prefix = prefix.strip().lower()
    if ".." in prefix or "/" in prefix or "\\" in prefix:
        raise FileNotFoundError("Trace ID is required")
    candidates = _trace_candidates(config, prefix)
    if not candidates:
        raise FileNotFoundError(f"No run found matching '{prefix}'")
    return candidates[0][1]


def resolve_trace_id_for_read(prefix: str, config: MaidaConfig) -> str:
    """Resolve a trace ID for strict read paths, including incomplete run dirs.

    Normal prefix resolution is metadata-driven. For explicit full trace IDs, keep
    a directory that exists but is missing/corrupt metadata so validation can
    report the real format problem instead of a generic not-found error.
    """
    try:
        return resolve_trace_id(prefix, config)
    except FileNotFoundError as original:
        raw = (prefix or "").strip().lower()
        try:
            trace_id = _validate_trace_id(raw)
        except ValueError:
            raise original
        try:
            run_dir = _trace_dir(trace_id, config)
        except ValueError:
            raise original
        if run_dir.is_dir():
            return trace_id
        raise original


def resolve_latest_trace_id(config: MaidaConfig) -> str:
    """Return the trace_id of the most recently started run.

    Raises FileNotFoundError if there are no runs yet.
    """
    try:
        candidates = _trace_candidates(config)
    except FileNotFoundError:
        candidates = []
    if not candidates:
        raise FileNotFoundError("No runs found. Run your traced agent first (or try `maida demo`).")
    return candidates[0][1]


def resolve_latest_run_id(config: MaidaConfig) -> str:
    """Return the most recent run identifier, whether current trace or legacy run."""
    runs = list_runs(limit=1, config=config)
    if not runs:
        raise FileNotFoundError("No runs found. Run your traced agent first (or try `maida demo`).")
    run_id = runs[0].get("trace_id") or runs[0].get("run_id")
    if not run_id:
        raise FileNotFoundError("No runs found. Run your traced agent first (or try `maida demo`).")
    return str(run_id)


def resolve_run_id(prefix: str, config: MaidaConfig) -> str:
    """Compatibility wrapper for legacy run IDs and new trace IDs."""
    if not prefix or not prefix.strip():
        raise FileNotFoundError("Run ID is required")
    prefix = prefix.strip()
    if ".." in prefix or "/" in prefix or "\\" in prefix:
        raise FileNotFoundError("Run ID is required")
    try:
        return resolve_trace_id_for_read(prefix, config)
    except FileNotFoundError:
        pass

    runs_base = _runs_dir(config)
    if not runs_base.is_dir():
        raise FileNotFoundError(f"No runs directory at {runs_base}")

    candidates: list[tuple[datetime | None, str]] = []
    for entry in runs_base.iterdir():
        if not entry.is_dir():
            continue
        rid = entry.name
        if rid != prefix and not rid.startswith(prefix):
            continue
        meta_f = entry / RUN_JSON
        if not meta_f.is_file():
            continue
        try:
            with open(meta_f, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        started_str = meta.get("started_at")
        started_dt = _parse_iso_z(started_str) if started_str else None
        candidates.append((started_dt, rid))

    if not candidates:
        raise FileNotFoundError(f"No run found matching '{prefix}'")

    def sort_key(item: tuple[datetime | None, str]) -> tuple[bool, datetime]:
        dt, _ = item
        return (dt is None, dt or datetime.min.replace(tzinfo=timezone.utc))

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0][1]
