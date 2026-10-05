"""Internal helpers for the local onboarding journal."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock

from maida._file_lock import file_lock
from maida._onboarding.constants import ACTORS, ASSISTANCE, FUNNEL, MILESTONES, PHASES, TASK_KINDS
from maida.config import load_config
from maida.project_local import repository_root

_PROCESS_LOCK = RLock()


def _require(condition: bool) -> None:
    if not condition:
        raise ValueError("Invalid local onboarding journal; preserve it and inspect the file before retrying")


def _timestamp(value: object) -> datetime:
    _require(isinstance(value, str))
    parsed = datetime.fromisoformat(value)
    _require(parsed.tzinfo is not None)
    return parsed


def _check_milestone(milestone: str | None, seen: set[str]) -> None:
    prerequisite = {
        "first-report": "own-task-captured",
        "gate-pass": "gate-configured",
        "regression-caught": "gate-pass",
        "repair-pass": "regression-caught",
        "local-gate-verified": "repair-pass",
    }.get(milestone)
    if prerequisite and prerequisite not in seen:
        raise ValueError(f"Record the completed {prerequisite} milestone before {milestone}")


def _validate_attempt(attempt: object) -> None:
    _require(isinstance(attempt, dict))
    required = {"id", "started_at", "engine_version", "task_kind", "assistance", "outcome", "events"}
    optional = {"activated_at", "activation_assistance"}
    _require(required <= attempt.keys() <= required | optional)
    _require(isinstance(attempt["id"], str) and re.fullmatch(r"[a-f0-9]{32}", attempt["id"]) is not None)
    _require(
        isinstance(attempt["engine_version"], str)
        and re.fullmatch(r"[A-Za-z0-9.+_-]{1,128}", attempt["engine_version"]) is not None
    )
    _require(attempt["task_kind"] is None or attempt["task_kind"] in TASK_KINDS)
    _require(attempt["assistance"] in ASSISTANCE)
    _require(attempt["outcome"] in ("in-progress", "activated", "blocked", "abandoned"))
    _require(isinstance(attempt["events"], list))
    previous = _timestamp(attempt["started_at"])
    seen = set()
    helped = attempt["assistance"] in ("founder", "other")
    activated_at = activation_assistance = None
    for event in attempt["events"]:
        _require(
            isinstance(event, dict)
            and {"at", "actor"} < event.keys() <= {"at", "actor", "phase", "minutes", "milestone", "outcome"}
        )
        _require(event["actor"] in ACTORS)
        at = _timestamp(event["at"])
        _require(at >= previous)
        previous = at
        _require(("phase" in event) == ("minutes" in event))
        if "phase" in event:
            _require(event["phase"] in PHASES)
            value = event["minutes"]
            _require(type(value) in (int, float) and math.isfinite(value) and value >= 0)
        if "outcome" in event:
            _require(event["outcome"] in ("blocked", "abandoned") and "milestone" not in event)
        if "milestone" in event:
            _require(event["milestone"] in MILESTONES)
            _check_milestone(event["milestone"], seen)
            seen.add(event["milestone"])
        helped = helped or event["actor"] != "user"
        if activated_at is None and "first-report" in seen:
            activated_at = event["at"]
            activation_assistance = "assisted" if helped else attempt["assistance"]
    _require(attempt.get("activated_at") == activated_at)
    _require(attempt.get("activation_assistance") == activation_assistance)
    _require(attempt["outcome"] != "activated" or activated_at is not None)
    _require(activated_at is None or attempt["outcome"] != "in-progress")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(root: Path | None = None) -> Path:
    # Scope attempts to this local checkout without retaining its name or path.
    root = root or repository_root(Path.cwd()) or Path.cwd()
    project = hashlib.sha256(str(root.resolve()).encode()).hexdigest()
    # Use the storage parent, not the post-init capture namespace: start often
    # precedes init. Measurement journals must not mix with capture receipts.
    return load_config(project_root=root).data_dir / "onboarding" / f"{project}.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"journal_version": 2, "attempts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _require(isinstance(data, dict))
    except (ValueError, TypeError):
        raise ValueError("Invalid local onboarding journal; preserve it and inspect the file before retrying") from None
    if type(data.get("journal_version")) is not int or data["journal_version"] != 2:
        raise ValueError(
            "This onboarding journal was created by an older or unsupported measurement format "
            "and cannot be used with the current funnel.\n\n"
            f"Existing data was left unchanged:\n{path}\n\n"
            "Move or archive that file, then run:\n  maida onboarding start"
        )
    try:
        _require(set(data) == {"journal_version", "attempts"})
        _require(isinstance(data["attempts"], list))
        for attempt in data["attempts"]:
            _validate_attempt(attempt)
    except (ValueError, TypeError, KeyError):
        raise ValueError("Invalid local onboarding journal; preserve it and inspect the file before retrying") from None
    return data


@contextmanager
def _locked(path: Path):
    """Serialize read/modify/write across hooks and explicit measurement."""
    with file_lock(path.with_suffix(".lock"), _PROCESS_LOCK):
        yield


def _append(attempt: dict, event: dict) -> bool:
    """Keep first milestone occurrence, including its assistance snapshot."""
    seen = {e.get("milestone") for e in attempt["events"]}
    milestone = event.get("milestone")
    if milestone in seen or milestone in _stages(attempt):
        event = {key: value for key, value in event.items() if key != "milestone"}
        if set(event) == {"at", "actor"}:
            return False
    _check_milestone(event.get("milestone"), seen)
    attempt["events"].append(event)
    if event.get("milestone") == "first-report" and not attempt.get("activated_at"):
        attempt["activated_at"] = event["at"]
        attempt["outcome"] = "activated"
        helped = any(e["actor"] != "user" for e in attempt["events"])
        attempt["activation_assistance"] = (
            "assisted" if helped or attempt["assistance"] in {"founder", "other"} else attempt["assistance"]
        )
    if "outcome" in event:
        attempt["outcome"] = event["outcome"]
    return True


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
        # Windows cannot rename an open temporary file. Close before replacing
        # the journal while the separate lock file still serializes writers.
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _stages(attempt: dict) -> dict:
    """Use explicit v2 evidence; repair after a caught regression verifies a gate."""
    stages = {}
    helped = attempt["assistance"] in {"founder", "other"}
    for event in attempt["events"]:
        helped = helped or event["actor"] != "user"
        milestone = event.get("milestone")
        stage = "local-gate-verified" if milestone == "repair-pass" else milestone
        if stage in FUNNEL and stage not in stages:
            stages[stage] = {
                "at": event["at"],
                "assistance": "assisted" if helped else attempt["assistance"],
            }
    return stages


def _elapsed(attempt: dict, at: str) -> float:
    return round((_timestamp(at) - _timestamp(attempt["started_at"])).total_seconds(), 3)
