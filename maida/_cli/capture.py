"""CLI command implementation: capture."""

import json
import logging
import os
import sys
from pathlib import Path

import typer
import uvicorn
from typer import Exit

from maida._cli.common import EXIT_INTERNAL, EXIT_NOT_FOUND, _emit_claude_capture_error, _emit_langfuse_error
from maida.capture.claude_code import create_claude_code_app
from maida.capture.claude_hook import (
    ClaudeHookConflictError,
    ClaudeHookImportError,
    ClaudeHookInputError,
    parse_claude_hook_json,
)
from maida.capture.codex_hook import DEFAULT_MAX_HOOK_BYTES, parse_codex_hook_json
from maida.capture.providers import provider_enabled, runtime_enabled
from maida.config import load_config
from maida.integrations.claude_code import (
    ClaudeCaptureImportError,
    ClaudeCaptureInputError,
    import_claude_capture,
)
from maida.integrations.langfuse import (
    LangfuseImportError,
    LangfuseInputError,
    client_from_environment,
    import_langfuse_traces,
)
from maida.project_local import installation


def capture_claude_code_cmd(
    host: str = typer.Option(
        "127.0.0.1",
        "--host",
        help="OTLP HTTP bind host",
    ),
    port: int = typer.Option(
        4318,
        "--port",
        min=1,
        max=65535,
        help="OTLP HTTP bind port",
    ),
) -> None:
    """Receive Claude Code logs and beta traces over OTLP HTTP/protobuf."""
    try:
        config = load_config(capture=True)
        receiver = create_claude_code_app(config)
        typer.echo(
            f"Listening for Claude Code OTLP on http://{host}:{port}",
            err=True,
        )
        uvicorn.run(
            app=receiver,
            host=host,
            port=port,
            access_log=False,
            log_level="warning",
        )
    except (KeyboardInterrupt, typer.Exit):
        raise
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise Exit(EXIT_INTERNAL)


def capture_claude_hook_cmd() -> None:
    """Record one passive Claude Code command-hook payload from stdin."""
    try:
        origin = os.environ.get("CLAUDE_PROJECT_DIR")
        local = installation(Path(origin) if origin else Path.cwd())
        if local and not provider_enabled(local[1], "claude-code"):
            diagnostic = (
                f"Maida capture disabled for {local[0]} (enabled: false). "
                "Reconnect with maida init --agent claude-code."
            )
            logging.getLogger(__name__).debug(diagnostic)
            if os.environ.get("MAIDA_DEBUG", "").strip().lower() in {"1", "true", "yes"}:
                typer.echo(diagnostic, err=True)
            return
        project_root = local[0] if local else None
        # Hook producers send UTF-8 JSON, regardless of Windows' pipe encoding.
        try:
            raw = sys.stdin.buffer.read().decode("utf-8") if hasattr(sys.stdin, "buffer") else sys.stdin.read()
        except UnicodeDecodeError:
            raise ClaudeHookInputError("hook payload must be UTF-8 JSON") from None
        parse_claude_hook_json(raw, load_config(project_root=project_root, capture=True))
    except ClaudeHookInputError as exc:
        typer.echo(f"Invalid Claude hook payload: {exc}", err=True)
        # Claude assigns blocking semantics to hook exit code 2. This capture
        # command is an observer, so even invalid input uses a non-blocking
        # failure code.
        raise Exit(EXIT_INTERNAL)
    except (ClaudeHookConflictError, ClaudeHookImportError) as exc:
        typer.echo(f"Claude hook capture failed: {exc}", err=True)
        raise Exit(EXIT_INTERNAL)
    except Exit:
        raise
    except Exception as exc:
        typer.echo(f"error: {exc}", err=True)
        raise Exit(EXIT_INTERNAL)


def capture_codex_hook_cmd() -> None:
    """Record one local Codex-runtime hook delivery; never emit an agent decision."""
    try:
        stream = sys.stdin.buffer if hasattr(sys.stdin, "buffer") else sys.stdin
        raw = stream.read(DEFAULT_MAX_HOOK_BYTES + 1)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        payload = json.loads(raw)
        if not isinstance(payload, dict) or not isinstance(payload.get("cwd"), str) or not payload["cwd"]:
            raise ValueError("cwd must identify the local session repository")
        origin = Path(payload["cwd"])
        if not origin.is_absolute():
            raise ValueError("cwd must be an absolute path")
        local = installation(origin)
        # Already-open sessions cannot re-enable detached capture.
        if local is None or not runtime_enabled(local[1], "codex"):
            return
        parse_codex_hook_json(raw, load_config(project_root=local[0], capture=True))
    except (ValueError, UnicodeError) as exc:
        typer.echo(f"Invalid Codex hook payload: {exc}", err=True)
        raise Exit(EXIT_INTERNAL) from exc
    except Exception as exc:
        typer.echo("Codex hook capture failed. Preserve capture state and rerun maida init --agent codex.", err=True)
        raise Exit(EXIT_INTERNAL) from exc


def import_claude_code_cmd(
    session_id: str = typer.Option(
        ...,
        "--session-id",
        help="Raw Claude Code session ID used to locate the hashed capture",
    ),
    segment: str = typer.Option(
        "latest",
        "--segment",
        help="Immutable capture segment ID, or latest",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Output a machine-readable import summary",
    ),
) -> None:
    """Normalize and install one locally captured Claude Code session."""
    try:
        result = import_claude_capture(
            session_id,
            load_config(capture=True),
            segment=segment,
        )
        if segment == "latest":
            typer.echo(
                f"Using Claude Code capture segment: {result.segment}",
                err=True,
            )
        if json_out:
            print(json.dumps(result.as_dict(), ensure_ascii=False))
        elif result.imported:
            typer.echo(
                f"Imported Claude Code capture {result.session_hash[:12]} "
                f"segment {result.segment} as {result.trace_id[:8]}"
            )
        else:
            typer.echo(
                f"Claude Code capture {result.session_hash[:12]} segment "
                f"{result.segment} is already imported as {result.trace_id[:8]}"
            )
    except Exit:
        raise
    except ClaudeCaptureInputError as exc:
        _emit_claude_capture_error(
            kind="invalid_capture",
            prefix="Invalid Claude Code capture",
            error=exc,
            json_out=json_out,
        )
        raise Exit(EXIT_NOT_FOUND)
    except ClaudeCaptureImportError as exc:
        _emit_claude_capture_error(
            kind="import_failed",
            prefix="Claude Code import failed",
            error=exc,
            json_out=json_out,
        )
        raise Exit(EXIT_INTERNAL)
    except Exception as exc:
        _emit_claude_capture_error(
            kind="internal_error",
            prefix="error",
            error=exc,
            json_out=json_out,
        )
        raise Exit(EXIT_INTERNAL)


def import_langfuse_cmd(
    trace_id: str | None = typer.Option(
        None,
        "--trace-id",
        help="Import one complete Langfuse trace by source trace ID",
    ),
    from_time: str | None = typer.Option(
        None,
        "--from",
        help="Inclusive timezone-aware observation start time",
    ),
    to_time: str | None = typer.Option(
        None,
        "--to",
        help="Exclusive timezone-aware observation start time",
    ),
    trace_name: str | None = typer.Option(
        None,
        "--trace-name",
        help="Restrict range discovery to a recurring Langfuse trace name",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session-id",
        help="Restrict range discovery to a Langfuse session",
    ),
    environment: list[str] | None = typer.Option(
        None,
        "--environment",
        help="Restrict discovery to an environment; repeat for multiple values",
    ),
    base_url: str | None = typer.Option(
        None,
        "--base-url",
        help="Langfuse cloud or self-hosted base URL",
    ),
    json_out: bool = typer.Option(
        False,
        "--json",
        help="Output a machine-readable import summary",
    ),
) -> None:
    """Import Langfuse traces through the read-only v2 observations API."""
    try:
        config = load_config()
        client = client_from_environment(base_url)
        summary = import_langfuse_traces(
            client,
            config,
            source_trace_id=trace_id,
            from_time=from_time,
            to_time=to_time,
            trace_name=trace_name,
            session_id=session_id,
            environments=tuple(environment or ()),
        )
        payload = summary.as_dict()
        if json_out:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            for item in summary.imported:
                typer.echo(f"Imported {item['run_name']} as {item['trace_id'][:8]}")
            for item in summary.skipped:
                label = item.get("run_name") or item["source_trace_id"]
                typer.echo(f"Skipped {label}: {item['reason']}")
            if summary.unmapped_observation_types:
                typer.echo(
                    "Preserved unknown Langfuse observation types as structural "
                    f"spans: {', '.join(sorted(summary.unmapped_observation_types))}",
                    err=True,
                )
        if (
            not summary.imported
            and summary.skipped
            and not any(item.get("reason") == "already imported" for item in summary.skipped)
        ):
            raise Exit(EXIT_NOT_FOUND)
    except Exit:
        raise
    except LangfuseInputError as exc:
        _emit_langfuse_error(
            kind="invalid_input",
            prefix="Invalid Langfuse import",
            error=exc,
            json_out=json_out,
        )
        raise Exit(EXIT_NOT_FOUND)
    except LangfuseImportError as exc:
        _emit_langfuse_error(
            kind="import_failed",
            prefix="Langfuse import failed",
            error=exc,
            json_out=json_out,
        )
        raise Exit(EXIT_INTERNAL)
    except Exception as exc:
        _emit_langfuse_error(
            kind="internal_error",
            prefix="error",
            error=exc,
            json_out=json_out,
        )
        raise Exit(EXIT_INTERNAL)
