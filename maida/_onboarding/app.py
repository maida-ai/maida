"""Typer commands for explicit local onboarding measurement."""

from __future__ import annotations

import json
import math
import uuid

import typer

from maida import __version__
from maida._onboarding.constants import ACTORS, ASSISTANCE, MILESTONES, PHASES, TASK_KINDS
from maida._onboarding.report import summarize
from maida._onboarding.utils import _append, _load, _locked, _now, _path, _save

app = typer.Typer(help="Measure onboarding attempts and effort locally; nothing is uploaded.")


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
