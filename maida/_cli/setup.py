"""CLI command implementation: setup."""

import json
import shlex
from pathlib import Path

import typer
from typer import Exit

from maida._cli.common import EXIT_INTERNAL, EXIT_NOT_FOUND, _read_config
from maida.baseline import (
    load_baseline,
)
from maida.config import load_config
from maida.constants import LOCAL_DIR_NAME
from maida.first_run import detach_capture, initialize_capture
from maida.onboarding import record_automatically
from maida.scaffold import (
    WORKFLOW_RELPATH,
    render_workflow,
)
from maida.starter import (
    ACTIVE_BASELINE,
    ACTIVE_POLICY,
    STARTER_REVIEW,
    draft_starter,
    reviewed_starter,
    validate_agent_script,
    verify_active_starter,
    write_files,
)


def init_cmd(
    github: bool = typer.Option(False, "--github", help="Also scaffold a GitHub Actions workflow"),
    force: bool = typer.Option(False, "--force", help="Overwrite existing files"),
    from_run: list[str] = typer.Option(
        [], "--from-run", help="Completed observation to draft from (repeatable; latest is explicit)"
    ),
    reviewed: bool = typer.Option(
        False, "--reviewed", help="Activate candidates after you have reviewed and edited them"
    ),
    reason: str | None = typer.Option(None, "--reason", help="Record why the reviewed invariants fit this task"),
    agent_script: Path | None = typer.Option(None, "--agent-script", help="Existing traced Python entrypoint for CI"),
    agent: str | None = typer.Option(
        None, "--agent", help="Resolve ambiguous first-run detection: claude-code or codex"
    ),
) -> None:
    """Set up local agent capture, or draft and activate reviewed invariants."""
    try:
        if agent is not None and (from_run or reviewed or github):
            raise ValueError(f"--agent is for first-run capture setup. Use maida init --agent {agent} separately.")
        if from_run and (reviewed or github):
            raise ValueError("Draft first with --from-run; review the candidates before --reviewed or --github")
        if reason is not None and not reviewed:
            raise ValueError("--reason requires --reviewed")
        if agent_script is not None and not github:
            raise ValueError("--agent-script requires --github")
        if not from_run and not reviewed and not github:
            try:
                if initialize_capture(agent):
                    record_automatically("setup-ready")
            except OSError as exc:
                raise ValueError(
                    "Cannot read capture setup files or Git metadata. Check permissions for this checkout and rerun maida init."
                ) from exc
            return
        if from_run:
            config = _read_config(None if from_run[0] == "latest" else from_run[0])
        elif reviewed and (LOCAL_DIR_NAME / "starter/baseline.json").is_file():
            config = _read_config(load_baseline(LOCAL_DIR_NAME / "starter/baseline.json").get("source_run_id"))
        else:
            config = load_config()
        if from_run:
            targets = draft_starter(from_run, config)
            write_files(targets, force=force)
            evidence = json.loads(targets[STARTER_REVIEW])
            typer.echo(
                f"Drafted {len(evidence['candidates'])} candidate invariants for {evidence['run_name']!r} from {evidence['observations']} observation(s)."
            )
            typer.echo(f"Selected traces: {', '.join(evidence['source_trace_ids'])}")
            typer.echo("Review .maida/starter/policy.yaml: remove any rule your task does not require.")
            typer.echo("These observations do not establish correctness or guarantees about future runs.")
            typer.echo("Next: maida init --reviewed --reason 'why these rules fit this task'")
            return
        script = validate_agent_script(agent_script) if github else None
        targets = {}
        review_record = None
        if reviewed:
            targets, review_record = reviewed_starter(reason or "", config)
        else:
            verify_active_starter()
        if github:
            targets[WORKFLOW_RELPATH] = render_workflow(script, ACTIVE_BASELINE.as_posix())
        # Preflight active files together; an invalid workflow never partly activates a policy.
        if review_record is not None:
            targets[STARTER_REVIEW] = json.dumps(review_record, ensure_ascii=False, indent=2) + "\n"
        write_files(targets, force=force, update={STARTER_REVIEW} if review_record else set())
        if reviewed:
            record_automatically("gate-configured")
        for path in targets:
            typer.echo(f"Wrote {path}")
        typer.echo("Next: run the same task again, then gate the new observation:")
        selection = " RUN_ID" if config.project_id else ""
        typer.echo(f"  maida assert{selection} --baseline {ACTIVE_BASELINE} --policy {ACTIVE_POLICY}")
        if selection:
            typer.echo("Use the new task's run ID printed by maida check.")
        typer.echo("This checks one observed execution; it does not certify a population pass rate.")
        if script:
            typer.echo(
                f"For the traced Python entrypoint: maida run {shlex.quote(script)} --baseline {ACTIVE_BASELINE} --policy {ACTIVE_POLICY}"
            )
            typer.echo(
                "Review and commit these files, then require the Maida / agent-check status in repository settings."
            )
            typer.echo(
                "CI dependency installation and workflow protection remain part of your repository configuration."
            )
    except (ValueError, FileNotFoundError) as e:
        operation = "initialize gate" if from_run or reviewed or github else "set up capture"
        typer.echo(f"Cannot {operation}: {e}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


def detach_cmd(
    agent: str | None = typer.Option(None, "--agent", help="Agent to detach: claude-code or codex"),
) -> None:
    """Preview and confirm removal of repository Maida capture hooks."""
    try:
        detach_capture(agent)
    except (ValueError, OSError) as exc:
        retry = "maida detach" + (f" --agent {agent}" if agent else "")
        message = (
            str(exc)
            if isinstance(exc, ValueError)
            else (f"Cannot read or write capture setup files. Check permissions for this checkout and rerun {retry}.")
        )
        typer.echo(f"Cannot detach Maida: {message}", err=True)
        raise Exit(EXIT_NOT_FOUND)
