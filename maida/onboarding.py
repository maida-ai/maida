"""Explicit, local funnel measurement; never uploaded or implicitly started."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

import typer

from maida import __version__
from maida._file_lock import file_lock
from maida.config import load_config
from maida.project_local import repository_root

if TYPE_CHECKING:
    from maida.config import MaidaConfig

_PROCESS_LOCK = RLock()


app = typer.Typer(help="Measure onboarding attempts and effort locally; nothing is uploaded.")
PHASES = ("setup", "trial", "investigation", "maintenance")
ACTORS = ("user", "founder", "other")
ASSISTANCE = ("none", "founder", "other", "unknown")
FUNNEL = (
    "setup-ready",
    "own-task-captured",
    "first-report",
    "gate-configured",
    "local-gate-verified",
    "pr-gate-verified",
)
VERIFICATION_STEPS = ("gate-pass", "regression-caught", "repair-pass")
MILESTONES = (*FUNNEL, "demo-seen", *VERIFICATION_STEPS)
TASK_KINDS = ("coding-agent", "python-agent", "other")


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


def record_automatically(milestone: str, *, root: Path | None = None) -> None:
    """Best-effort and quiet; never start measurement or change product results."""
    if milestone not in FUNNEL[:4]:
        raise ValueError("Only mechanically provable funnel stages may be recorded automatically")
    try:
        path = _path(root)
        if not path.is_file():
            return
        with _locked(path):
            data = _load(path)
            if not data["attempts"] or data["attempts"][-1]["outcome"] in {"blocked", "abandoned"}:
                return
            if _append(data["attempts"][-1], {"at": _now(), "actor": "user", "milestone": milestone}):
                _save(path, data)
    except (OSError, ValueError):
        # Invalid/unwritable journals remain available for explicit inspection.
        # Measurement must never break a passive capture hook or a valid report.
        return


def record_completed_capture(config: MaidaConfig, session_hash: str, segment: str) -> None:
    """Only complete, repository-scoped task receipts establish an own task."""
    if config.project_root is None:
        return
    try:
        receipt = json.loads((config.data_dir / "onboarding" / f"{session_hash}-{segment}.json").read_text())
        if receipt["state"] == "closed" and receipt["has_start"] is True and receipt["complete_tools"] is True:
            record_automatically("own-task-captured", root=config.project_root)
    except (OSError, ValueError, KeyError, TypeError):
        return


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


def _invalid(error: Exception) -> None:
    typer.echo(f"Cannot record onboarding measurement: {error}", err=True)
    raise typer.Exit(2)


@app.command("start")
def start(
    assistance: str = typer.Option("unknown", "--assistance", help="none, founder, other, or unknown"),
    task_kind: str | None = typer.Option(None, "--task-kind", help=", ".join(TASK_KINDS)),
) -> None:
    """Start an attempt before setup; unfinished previous attempts remain counted."""
    try:
        if assistance not in ASSISTANCE:
            raise ValueError("--assistance must be none, founder, other, or unknown")
        if task_kind is not None and task_kind not in TASK_KINDS:
            raise ValueError(f"--task-kind must be one of: {', '.join(TASK_KINDS)}")
        path = _path()
        with _locked(path):
            data = _load(path)
            if data["attempts"] and data["attempts"][-1]["outcome"] == "in-progress":
                data["attempts"][-1]["outcome"] = "abandoned"
            attempt = {
                "id": uuid.uuid4().hex,
                "started_at": _now(),
                "engine_version": __version__,
                "task_kind": task_kind,
                "assistance": assistance,
                "outcome": "in-progress",
                "events": [],
            }
            data["attempts"].append(attempt)
            _save(path, data)
        typer.echo("Started local onboarding attempt. Record actual work with maida onboarding record.")
        typer.echo("Product activation is the first valid maida check report on your own captured task (PASS or FAIL).")
    except (OSError, ValueError) as error:
        _invalid(error)


@app.command("record")
def record(
    milestone: str | None = typer.Option(None, "--milestone", help=", ".join(MILESTONES)),
    phase: str | None = typer.Option(None, "--phase", help=", ".join(PHASES)),
    minutes: float | None = typer.Option(None, "--minutes", help="Human effort, separate from elapsed attempt time"),
    actor: str = typer.Option("user", "--actor", help=", ".join(ACTORS)),
    outcome: str | None = typer.Option(None, "--outcome", help="blocked or abandoned; retains unsuccessful attempts"),
) -> None:
    """Record an observed milestone or effort; never include task text or identities."""
    try:
        if milestone is not None and milestone not in MILESTONES:
            raise ValueError(f"--milestone must be one of: {', '.join(MILESTONES)}")
        if phase is not None and phase not in PHASES:
            raise ValueError(f"--phase must be one of: {', '.join(PHASES)}")
        if actor not in ACTORS:
            raise ValueError(f"--actor must be one of: {', '.join(ACTORS)}")
        if outcome is not None and outcome not in {"blocked", "abandoned"}:
            raise ValueError("--outcome must be blocked or abandoned")
        if outcome is not None and milestone is not None:
            raise ValueError("Record a completed milestone or a stopped outcome, not both")
        if (minutes is None) != (phase is None):
            raise ValueError("Record effort with both --phase and --minutes")
        if minutes is not None and (not math.isfinite(minutes) or minutes < 0):
            raise ValueError("--minutes must be a finite non-negative number")
        if milestone is None and phase is None and outcome is None:
            raise ValueError("Pass --milestone, --phase with --minutes, or --outcome")
        path = _path()
        with _locked(path):
            data = _load(path)
            if not data["attempts"]:
                raise ValueError("Start an attempt first: maida onboarding start")
            attempt = data["attempts"][-1]
            if attempt["outcome"] in {"blocked", "abandoned"}:
                raise ValueError("This attempt has ended; run maida onboarding start for a new attempt")
            event = {"at": _now(), "actor": actor}
            if phase is not None:
                event.update({"phase": phase, "minutes": minutes})
            if milestone is not None:
                event["milestone"] = milestone
            if outcome is not None:
                event["outcome"] = outcome
            if _append(attempt, event):
                _save(path, data)
        typer.echo("Recorded locally. Inspect totals with maida onboarding report.")
    except (OSError, ValueError) as error:
        _invalid(error)


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


def summarize(data: dict) -> dict:
    attempts = data["attempts"]
    activated = [a for a in attempts if a.get("activated_at")]
    effort = {actor: dict.fromkeys(PHASES, 0.0) for actor in ACTORS}
    for attempt in attempts:
        for event in attempt["events"]:
            if "phase" in event:
                effort[event["actor"]][event["phase"]] += event["minutes"]
    occurrences = [_stages(a) for a in attempts]
    funnel = []
    for index, stage in enumerate(FUNNEL):
        reached = [s[stage] for s in occurrences if stage in s]
        previous = FUNNEL[index - 1] if index else None
        eligible = [s for s in occurrences if previous in s] if previous else occurrences
        converted = sum(
            stage in s and (previous is None or _timestamp(s[stage]["at"]) >= _timestamp(s[previous]["at"]))
            for s in eligible
        )
        funnel.append(
            {
                "stage": stage,
                "reached": len(reached),
                "percentage_of_attempts": 100 * len(reached) / len(attempts) if attempts else None,
                "conversion_from_previous": converted / len(eligible) if eligible else None,
                "elapsed_seconds": [_elapsed(a, s[stage]["at"]) for a, s in zip(attempts, occurrences) if stage in s],
                "unassisted": sum(s["assistance"] == "none" for s in reached),
                "assisted": sum(s["assistance"] == "assisted" for s in reached),
                "unknown_assistance": sum(s["assistance"] == "unknown" for s in reached),
            }
        )
    elapsed = [_elapsed(a, a["activated_at"]) for a in activated]
    return {
        "measurement_version": 2,
        "evidence": "local command observations and explicit self-reported milestones; not evidence of understanding",
        "activation_stage": "first-report",
        "time_to_first_report_seconds": elapsed,
        "funnel": funnel,
        "engine_versions": sorted({a["engine_version"] for a in attempts}),
        "task_kinds": {
            kind: sum((a["task_kind"] or "unknown") == kind for a in attempts)
            for kind in sorted({a["task_kind"] or "unknown" for a in attempts})
        },
        "attempts": len(attempts),
        "activated": len(activated),
        "activation_rate": len(activated) / len(attempts) if attempts else None,
        "unassisted": sum(a["activation_assistance"] == "none" for a in activated),
        "assisted": sum(a["activation_assistance"] == "assisted" for a in activated),
        "unknown_assistance": sum(a["activation_assistance"] == "unknown" for a in activated),
        "blocked": sum(a["outcome"] == "blocked" for a in attempts),
        "abandoned": sum(a["outcome"] == "abandoned" for a in attempts),
        "in_progress": sum(a["outcome"] == "in-progress" for a in attempts),
        "time_to_activation_seconds": elapsed,
        "effort_minutes": effort,
        "local_gate_verified": funnel[4]["reached"],
        "pr_gate_verified": funnel[5]["reached"],
        "ci_verified": funnel[5]["reached"],
        "setup_without_first_report": sum("setup-ready" in s and "first-report" not in s for s in occurrences),
        "demo_seen": sum(any(e.get("milestone") == "demo-seen" for e in a["events"]) for a in attempts),
    }


@app.command("report")
def report(json_out: bool = typer.Option(False, "--json", help="Print machine-readable aggregate counts")) -> None:
    """Report all attempts, including unknown assistance and incomplete attempts."""
    try:
        summary = summarize(_load(_path()))
        if json_out:
            typer.echo(json.dumps(summary, indent=2))
            return
        typer.echo(
            f"First report (product activation): {summary['activated']}/{summary['attempts']} local attempts; {summary['unassisted']} unassisted, {summary['assisted']} assisted, {summary['unknown_assistance']} assistance unknown."
        )
        typer.echo(f"time_to_first_report_seconds: {summary['time_to_first_report_seconds']}")
        for stage in summary["funnel"]:
            percentage = (
                f"{stage['percentage_of_attempts']:.1f}%" if stage["percentage_of_attempts"] is not None else "n/a"
            )
            conversion = (
                f"{100 * stage['conversion_from_previous']:.1f}%"
                if stage["conversion_from_previous"] is not None
                else "n/a"
            )
            typer.echo(
                f"{stage['stage']}: {stage['reached']}/{summary['attempts']} ({percentage}); conversion={conversion}; seconds={stage['elapsed_seconds']}; unassisted={stage['unassisted']}, assisted={stage['assisted']}, unknown={stage['unknown_assistance']}"
            )
        typer.echo(f"Setup without a first report: {summary['setup_without_first_report']} attempts.")
        typer.echo(
            f"Unfinished: {summary['blocked']} blocked, {summary['abandoned']} abandoned, {summary['in_progress']} in progress."
        )
        for actor, totals in summary["effort_minutes"].items():
            typer.echo(f"{actor} minutes: " + ", ".join(f"{phase}={minutes:g}" for phase, minutes in totals.items()))
        typer.echo(
            f"Engine versions: {', '.join(summary['engine_versions']) or 'none'}; task kinds: {summary['task_kinds']}"
        )
        typer.echo(
            "Reports establish observed evidence, not understanding. Local regression and PR verification are later explicit milestones. Nothing is uploaded."
        )
    except (OSError, ValueError) as error:
        _invalid(error)
