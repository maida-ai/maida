"""CLI command implementation: common."""

import getpass
import json
import os
from pathlib import Path

import typer
from typer import Exit

import maida.storage as storage
from maida.acceptance import AcceptanceSource
from maida.config import MaidaConfig, load_config
from maida.trace_validation import (
    TraceDiagnostic,
    TraceInputError,
    TraceValidationError,
    validate_trace_path,
)

EXIT_NOT_FOUND = 2


EXIT_INTERNAL = 10


_DEMO_TRACE_DURATION_MS = 120


_PLAN_BACKEND_INSTALL_COMMAND = 'uv tool install --force --python 3.12 --with "maida-workflows>=0.2" "maida-ai>=0.6.1"'


def _resolve_run_or_latest(run_id: str | None, config) -> str:
    """Resolve *run_id* (full ID or prefix) or fall back to the latest run.

    When falling back, the chosen run is announced on stderr so stdout stays
    machine-readable.  Raises FileNotFoundError if nothing can be resolved.
    """
    if run_id is None:
        resolved = storage.resolve_latest_run_id(config)
        typer.echo(f"Using latest run: {resolved[:8]}", err=True)
        return resolved
    return storage.resolve_run_id(run_id, config)


def _read_config(run_id: str | None = None) -> MaidaConfig:
    """Preserve configured read defaults, resolving explicit captured IDs too."""
    config = load_config()
    if run_id is None:
        return config
    try:
        storage.resolve_run_id(run_id, config)
        return config
    except FileNotFoundError:
        pass
    return load_config(capture=True)


def _trace_validation_payload(
    *,
    valid: bool,
    diagnostics: list[TraceDiagnostic],
    trace_id: str | None = None,
    spec_version: str | None = None,
    status: str | None = None,
    span_count: int | None = None,
) -> dict:
    return {
        "valid": valid,
        "trace_id": trace_id,
        "spec_version": spec_version,
        "status": status,
        "span_count": span_count,
        "diagnostics": [item.to_dict() for item in diagnostics],
    }


def validate_trace_cmd(
    path: Path = typer.Argument(
        ...,
        help="Native Maida run directory or its meta.json file",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Print a machine-readable validation result",
    ),
) -> None:
    """Validate a native externally emitted Maida trace without installing it."""
    try:
        validated = validate_trace_path(path)
        if json_out:
            typer.echo(
                json.dumps(
                    _trace_validation_payload(
                        valid=True,
                        diagnostics=[],
                        trace_id=validated.trace_id,
                        spec_version=validated.spec_version,
                        status=validated.status,
                        span_count=len(validated.spans),
                    ),
                    sort_keys=True,
                )
            )
        else:
            typer.echo(
                f"Valid Maida trace {validated.trace_id[:8]} "
                f"(spec_version {validated.spec_version}, "
                f"{len(validated.spans)} spans, status {validated.status})"
            )
    except TraceInputError as exc:
        if json_out:
            typer.echo(
                json.dumps(
                    _trace_validation_payload(
                        valid=False,
                        diagnostics=[exc.diagnostic],
                    ),
                    sort_keys=True,
                )
            )
        else:
            typer.echo(f"Invalid trace input: {exc.diagnostic.message}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except TraceValidationError as exc:
        if json_out:
            typer.echo(
                json.dumps(
                    _trace_validation_payload(
                        valid=False,
                        diagnostics=list(exc.diagnostics),
                        trace_id=exc.trace_id,
                        spec_version=exc.spec_version,
                        status=exc.status,
                        span_count=exc.span_count,
                    ),
                    sort_keys=True,
                )
            )
        else:
            typer.echo("Invalid Maida trace:", err=True)
            for diagnostic in exc.diagnostics:
                typer.echo(f"- {diagnostic.location}: {diagnostic.message}", err=True)
        raise Exit(1)
    except Exit:
        raise
    except Exception:
        diagnostic = TraceDiagnostic(
            code="internal_error",
            location="trace",
            message="trace validation failed unexpectedly",
        )
        if json_out:
            typer.echo(
                json.dumps(
                    _trace_validation_payload(
                        valid=False,
                        diagnostics=[diagnostic],
                    ),
                    sort_keys=True,
                )
            )
        else:
            typer.echo(f"error: {diagnostic.message}", err=True)
        raise Exit(EXIT_INTERNAL)


def _acceptance_source_from_environment() -> AcceptanceSource:
    """Build acceptance provenance from local or GitHub write-back context."""
    accepted_by = (os.environ.get("MAIDA_ACCEPTED_BY") or os.environ.get("GITHUB_ACTOR") or getpass.getuser()).strip()
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip() or None
    commit_sha = os.environ.get("MAIDA_EXPECTED_HEAD_SHA", "").strip() or None
    pull_request_text = os.environ.get("MAIDA_PR_NUMBER", "").strip()
    pull_request = None
    if pull_request_text:
        try:
            pull_request = int(pull_request_text)
        except ValueError as exc:
            raise ValueError("MAIDA_PR_NUMBER must be a positive integer") from exc
        if pull_request <= 0:
            raise ValueError("MAIDA_PR_NUMBER must be a positive integer")

    pull_request_url = None
    if repository and pull_request is not None:
        server_url = os.environ.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
        pull_request_url = f"{server_url}/{repository}/pull/{pull_request}"

    return AcceptanceSource(
        accepted_by=accepted_by or "unknown",
        repository=repository,
        pull_request=pull_request,
        pull_request_url=pull_request_url,
        commit_sha=commit_sha,
    )


def _exit_unsupported_trace_format(error: storage.UnsupportedTraceFormatError) -> None:
    typer.echo(str(error), err=True)
    raise Exit(EXIT_NOT_FOUND)


def _exit_run_validation_error(error: storage.RunValidationError) -> None:
    typer.echo(str(error), err=True)
    raise Exit(EXIT_NOT_FOUND)


def _emit_langfuse_error(*, kind: str, prefix: str, error: Exception, json_out: bool) -> None:
    if json_out:
        print(
            json.dumps(
                {"error": {"kind": kind, "message": str(error)}},
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(f"{prefix}: {error}", err=True)


def _emit_claude_capture_error(*, kind: str, prefix: str, error: Exception, json_out: bool) -> None:
    if json_out:
        print(
            json.dumps(
                {"error": {"kind": kind, "message": str(error)}},
                ensure_ascii=False,
            )
        )
    else:
        typer.echo(f"{prefix}: {error}", err=True)
