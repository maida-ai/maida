"""
Local storage for Maida runs using OpenTelemetry span data.

Each run (trace) is stored in: <data_dir>/runs/<trace_id_hex>/
  - meta.json: run metadata (run_name, status, counts, etc.)
  - spans.jsonl: one JSON span dict per line (written by MaidaLocalSpanExporter)

Note: trace_id_hex is 32 hex characters (128-bit trace ID).
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from maida.config import MaidaConfig

META_JSON = "meta.json"
SPANS_JSONL = "spans.jsonl"
RUN_JSON = "run.json"
EVENTS_JSONL = "events.jsonl"

logger = logging.getLogger(__name__)

_TRACE_ID_LEN = 32


def _validate_trace_id(trace_id: str) -> str:
    """Validate that trace_id is a 32-char hex string (no path traversal)."""
    if not trace_id or not isinstance(trace_id, str):
        raise ValueError("invalid trace_id")
    t = trace_id.strip().lower()
    if len(t) != _TRACE_ID_LEN or not all(c in "0123456789abcdef" for c in t.lower()):
        raise ValueError("invalid trace_id")
    if ".." in t or "/" in t or "\\" in t:
        raise ValueError("invalid trace_id")
    return t


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


def load_run_meta(trace_id: str, config: MaidaConfig) -> dict:
    """Load run metadata from meta.json."""
    try:
        path = _meta_path(trace_id, config)
    except ValueError:
        path = _legacy_run_dir(trace_id, config) / RUN_JSON
    if not path.is_file():
        legacy_path = _legacy_run_dir(trace_id, config) / RUN_JSON
        if legacy_path.is_file():
            path = legacy_path
    if not path.is_file():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_spans(trace_id: str, config: MaidaConfig) -> list[dict]:
    """Read spans.jsonl for the trace and return a list of span dicts."""
    path = _spans_path(trace_id, config)
    if not path.is_file():
        return []
    spans: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(
                    "load_spans: skipping corrupt JSONL line trace_id=%s line=%s: %s",
                    trace_id,
                    line_no,
                    e,
                )
                continue
    return spans


def load_events(run_id: str, config: MaidaConfig) -> list[dict]:
    """Compatibility wrapper: return projected OTel events or legacy events.jsonl."""
    try:
        spans = load_spans(run_id, config)
    except ValueError:
        spans = []
    if spans:
        from maida.events import spans_to_events

        return spans_to_events(spans)

    path = _legacy_run_dir(run_id, config) / EVENTS_JSONL
    if not path.is_file():
        return []
    events: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as e:
                logger.warning(
                    "load_events: skipping corrupt JSONL line run_id=%s line=%s: %s",
                    run_id,
                    line_no,
                    e,
                )
    return events


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
    from maida.constants import SPEC_VERSION, default_counts
    from maida.events import utc_now_iso_ms_z

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


def finalize_run(
    run_id: str, status: str, counts: dict[str, int], config: MaidaConfig
) -> dict:
    """Compatibility wrapper for legacy run finalization callers."""
    from maida.events import utc_now_iso_ms_z

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


def resolve_trace_id(prefix: str, config: MaidaConfig) -> str:
    """Resolve a trace_id prefix (short or full) to the full 32-char hex trace_id.

    Raises FileNotFoundError if no match.
    """
    if not prefix or not prefix.strip():
        raise FileNotFoundError("Trace ID is required")
    prefix = prefix.strip().lower()
    if ".." in prefix or "/" in prefix or "\\" in prefix:
        raise FileNotFoundError("Trace ID is required")
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
        if tid != prefix and not tid.startswith(prefix):
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

    if not candidates:
        raise FileNotFoundError(f"No run found matching '{prefix}'")

    def sort_key(item: tuple[datetime | None, str]) -> tuple[bool, datetime]:
        dt, _ = item
        return (dt is None, dt or datetime.min.replace(tzinfo=timezone.utc))

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0][1]


def resolve_run_id(prefix: str, config: MaidaConfig) -> str:
    """Compatibility wrapper for legacy run IDs and new trace IDs."""
    if not prefix or not prefix.strip():
        raise FileNotFoundError("Run ID is required")
    prefix = prefix.strip()
    if ".." in prefix or "/" in prefix or "\\" in prefix:
        raise FileNotFoundError("Run ID is required")
    try:
        return resolve_trace_id(prefix, config)
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
    """Update run.json meta.json with a new run_name."""
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
    import shutil

    run_dir = _trace_dir(trace_id, config)
    if not run_dir.is_dir():
        raise FileNotFoundError(f"No run found for trace_id '{trace_id}'")
    shutil.rmtree(run_dir)
