"""Explicit local measurement of activation attempts and human effort.

The journal is never uploaded. Milestones are self-reported, including whether
help was needed; a demo or a command's exit status cannot imply activation.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

import typer

from maida import __version__
from maida.config import load_config


app = typer.Typer(help="Measure onboarding attempts and effort locally; nothing is uploaded.")
PHASES = ("setup", "trial", "investigation", "maintenance")
ACTORS = ("user", "founder", "other")
ASSISTANCE = ("none", "founder", "other", "unknown")
ACTIVATION_STEPS = ("captured", "baseline-reviewed", "gate-pass", "regression-caught", "repair-pass")
MILESTONES = ("installed", *ACTIVATION_STEPS, "ci-verified")
ACTIVATION = set(ACTIVATION_STEPS)
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
    if milestone in ACTIVATION_STEPS:
        index = ACTIVATION_STEPS.index(milestone)
        if index and ACTIVATION_STEPS[index - 1] not in seen:
            raise ValueError(f"Record the completed {ACTIVATION_STEPS[index - 1]} milestone before {milestone}")


def _validate_attempt(attempt: object) -> None:
    _require(isinstance(attempt, dict))
    required = {"id", "started_at", "engine_version", "task_kind", "assistance", "outcome", "events"}
    _require(required <= attempt.keys() <= required | {"activated_at", "activation_assistance"})
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
        if activated_at is None and ACTIVATION <= seen:
            activated_at = event["at"]
            activation_assistance = "assisted" if helped else attempt["assistance"]
    _require(attempt.get("activated_at") == activated_at)
    _require(attempt.get("activation_assistance") == activation_assistance)
    _require(attempt["outcome"] != "activated" or activated_at is not None)
    _require(activated_at is None or attempt["outcome"] != "in-progress")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path() -> Path:
    # Scope attempts to this local checkout without retaining its name or path.
    project = hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()
    return load_config().data_dir / "onboarding" / f"{project}.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {"journal_version": 1, "attempts": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _require(isinstance(data, dict) and set(data) == {"journal_version", "attempts"})
        _require(type(data["journal_version"]) is int and data["journal_version"] == 1)
        _require(isinstance(data["attempts"], list))
        for attempt in data["attempts"]:
            _validate_attempt(attempt)
    except (ValueError, TypeError, KeyError):
        raise ValueError("Invalid local onboarding journal; preserve it and inspect the file before retrying") from None
    return data


def _save(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.replace(temporary, path)
        finally:
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
        typer.echo(
            "Activation requires own-task capture, reviewed baseline, passing gate, caught regression and repaired PASS."
        )
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
        data = _load(path)
        if not data["attempts"]:
            raise ValueError("Start an attempt first: maida onboarding start")
        attempt = data["attempts"][-1]
        if attempt["outcome"] in {"blocked", "abandoned"}:
            raise ValueError("This attempt has ended; run maida onboarding start for a new attempt")
        _check_milestone(milestone, {e.get("milestone") for e in attempt["events"]})
        event = {"at": _now(), "actor": actor}
        if phase is not None:
            event.update({"phase": phase, "minutes": minutes})
        if milestone is not None:
            event["milestone"] = milestone
        if outcome is not None:
            event["outcome"] = outcome
        attempt["events"].append(event)
        if not attempt.get("activated_at") and ACTIVATION <= {e.get("milestone") for e in attempt["events"]}:
            attempt["activated_at"] = event["at"]
            attempt["outcome"] = "activated"
            helped = any(e["actor"] != "user" for e in attempt["events"])
            attempt["activation_assistance"] = (
                "assisted" if helped or attempt["assistance"] in {"founder", "other"} else attempt["assistance"]
            )
        if outcome is not None:
            attempt["outcome"] = outcome
        _save(path, data)
        typer.echo("Recorded locally. Inspect totals with maida onboarding report.")
    except (OSError, ValueError) as error:
        _invalid(error)


def summarize(data: dict) -> dict:
    attempts = data["attempts"]
    activated = [a for a in attempts if a.get("activated_at")]
    effort = {actor: dict.fromkeys(PHASES, 0.0) for actor in ACTORS}
    for attempt in attempts:
        for event in attempt["events"]:
            if "phase" in event:
                effort[event["actor"]][event["phase"]] += event["minutes"]
    elapsed = [
        round((datetime.fromisoformat(a["activated_at"]) - datetime.fromisoformat(a["started_at"])).total_seconds(), 3)
        for a in activated
    ]
    return {
        "measurement_version": 1,
        "evidence": "self-reported local workflow milestones",
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
        "ci_verified": sum(any(e.get("milestone") == "ci-verified" for e in a["events"]) for a in attempts),
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
            f"Activation: {summary['activated']}/{summary['attempts']} local attempts; {summary['unassisted']} unassisted, {summary['assisted']} assisted, {summary['unknown_assistance']} assistance unknown."
        )
        typer.echo(
            f"Unfinished: {summary['blocked']} blocked, {summary['abandoned']} abandoned, {summary['in_progress']} in progress."
        )
        for actor, totals in summary["effort_minutes"].items():
            typer.echo(f"{actor} minutes: " + ", ".join(f"{phase}={minutes:g}" for phase, minutes in totals.items()))
        typer.echo("Self-reported local workflow evidence; demo completion and elapsed time alone are not activation.")
        typer.echo("Use --json for elapsed activation times and explicit CI verification counts. Nothing is uploaded.")
    except (OSError, ValueError) as error:
        _invalid(error)
