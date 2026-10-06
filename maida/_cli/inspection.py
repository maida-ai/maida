"""CLI command implementation: inspection."""

import json
import socket
import threading
import time
import webbrowser
from pathlib import Path

import typer
import yaml
from typer import Exit

import maida.storage as storage
from maida._assertions.types import AssertionPolicy
from maida._cli.common import (
    EXIT_INTERNAL,
    EXIT_NOT_FOUND,
    _exit_run_validation_error,
    _exit_unsupported_trace_format,
    _read_config,
    _resolve_run_or_latest,
)
from maida.baseline import (
    load_baseline,
)
from maida.config import load_config
from maida.constants import LOCAL_DIR_NAME, SPEC_VERSION
from maida.diff import compute_diff, format_diff_text
from maida.evaluation import evaluate_stored_run_against_baseline
from maida.integrations.claude_code import (
    ClaudeCaptureImportError,
    ClaudeCaptureInputError,
    import_claude_capture,
)
from maida.policy import load_policy
from maida.server import create_app


def _wait_for_port(host: str, port: int, timeout_s: float = 5.0) -> bool:
    """Block until *host*:*port* accepts a TCP connection, or *timeout_s* elapses.

    Used to avoid opening the browser before the viewer server is reachable
    (race-condition prevention).  Pure-stdlib, no new dependencies.

    Returns ``True`` if the port became reachable, ``False`` on timeout.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.1):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _run_table_rows(runs: list[dict]) -> list[list[str]]:
    """Build rows for text table: run_id (short), run_name, started_at, duration_ms, llm_calls, tool_calls, status."""
    rows = []
    for r in runs:
        run_id = (r.get("trace_id") or r.get("run_id") or "")[:8]
        run_name = r.get("run_name") or ""
        started_at = r.get("started_at") or ""
        duration_ms = r.get("duration_ms")
        duration_str = str(duration_ms) if duration_ms is not None else ""
        counts = r.get("counts") or {}
        llm = counts.get("llm_calls", 0)
        tool = counts.get("tool_calls", 0)
        status = r.get("status") or ""
        rows.append([run_id, run_name, started_at, duration_str, str(llm), str(tool), status])
    return rows


def _format_text_table(rows: list[list[str]], headers: list[str]) -> str:
    """Format rows as a simple text table (no external libs)."""
    if not rows:
        return "\n".join(["\t".join(headers), ""])
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(col_widths):
                col_widths[i] = max(col_widths[i], len(str(cell)))
    lines = []
    sep = "\t"
    lines.append(sep.join(h.ljust(col_widths[i]) for i, h in enumerate(headers)))
    for row in rows:
        lines.append(sep.join(str(row[i]).ljust(col_widths[i]) for i in range(min(len(row), len(col_widths)))))
    return "\n".join(lines)


def list_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="Max runs to list"),
    json_out: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
) -> None:
    """List recent runs."""
    try:
        config = _read_config()
        runs = storage.list_runs(limit=limit, config=config)
        if json_out:
            out = {"spec_version": SPEC_VERSION, "runs": runs}
            print(json.dumps(out, ensure_ascii=False))
        else:
            headers = [
                "run_id",
                "run_name",
                "started_at",
                "duration_ms",
                "llm_calls",
                "tool_calls",
                "status",
            ]
            rows = _run_table_rows(runs)
            print(_format_text_table(rows, headers))
    except ValueError as e:
        typer.echo(f"Invalid configuration: {e}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as e:
        if not json_out:
            typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


def export_cmd(
    run_id: str | None = typer.Argument(None, help="Run ID or trace ID prefix to export (default: latest run)"),
    out: Path = typer.Option(..., "--out", "-o", path_type=Path, help="Output JSON file path"),
) -> None:
    """Export a run to a single JSON file (run metadata + events array)."""
    try:
        config = _read_config(run_id)
        try:
            trace_id = _resolve_run_or_latest(run_id, config)
        except FileNotFoundError as e:
            typer.echo(f"Run not found: {e}", err=True)
            raise Exit(EXIT_NOT_FOUND)
        try:
            _, run_meta, events = storage.load_run_for_analysis(trace_id, config)
        except storage.UnsupportedTraceFormatError as e:
            _exit_unsupported_trace_format(e)
        except storage.RunValidationError as e:
            _exit_run_validation_error(e)
        except (ValueError, FileNotFoundError):
            raise Exit(EXIT_NOT_FOUND)
        payload = {"spec_version": SPEC_VERSION, "run": run_meta, "events": events}
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exit:
        raise
    except storage.UnsupportedTraceFormatError as e:
        _exit_unsupported_trace_format(e)
    except ValueError as e:
        typer.echo(f"Invalid configuration: {e}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


def view_cmd(
    run_id: str | None = typer.Argument(None, help="Run ID to view (default: latest)"),
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind host"),
    port: int = typer.Option(8712, "--port", "-p", help="Bind port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open browser"),
    json_out: bool = typer.Option(False, "--json", help="Print run_id, url, status as JSON then start server"),
) -> None:
    """Start local viewer server and optionally open browser."""
    try:
        config = _read_config(run_id)
        if run_id is None:
            runs = storage.list_runs(limit=1, config=config)
            if not runs:
                run_id = ""
            else:
                run_id = runs[0].get("trace_id") or runs[0].get("run_id") or ""
        if run_id:
            try:
                run_id = storage.resolve_trace_id_for_read(run_id, config)
            except FileNotFoundError as e:
                if not json_out:
                    typer.echo(f"Run not found: {e}", err=True)
                raise Exit(EXIT_NOT_FOUND)
            try:
                storage.load_validated_run(run_id, config)
            except storage.RunValidationError as e:
                _exit_run_validation_error(e)
            except (ValueError, FileNotFoundError) as e:
                if not json_out:
                    typer.echo(f"Run not found: {e}", err=True)
                raise Exit(EXIT_NOT_FOUND)

        url = f"http://{host}:{port}/" + (f"?run_id={run_id}" if run_id else "")
        if json_out:
            out = {
                "spec_version": SPEC_VERSION,
                "run_id": run_id,
                "url": url,
                "status": "serving",
            }
            print(json.dumps(out, ensure_ascii=False))

        import uvicorn

        fastapi_app = create_app(config=config)
        log_level = "warning" if json_out else "info"

        # Start the server in a background thread so we can gate the browser
        # open on actual TCP readiness (prevents "connection refused" race).
        # Server runs until the user presses Ctrl+C (main thread blocks on join).
        server_thread = threading.Thread(
            target=uvicorn.run,
            kwargs=dict(app=fastapi_app, host=host, port=port, log_level=log_level),
            daemon=False,
        )
        server_thread.start()

        if not no_browser:
            if _wait_for_port(host, port):
                webbrowser.open(url)
            else:
                typer.echo(
                    f"Server did not become ready in time. Open manually: {url}",
                    err=True,
                )

        # Block the main thread until the server exits or the user interrupts.
        server_thread.join()
    except Exit:
        raise
    except KeyboardInterrupt:
        if not json_out:
            typer.echo("Stopped.", err=True)
        raise Exit(0)
    except ValueError as e:
        typer.echo(f"Invalid configuration: {e}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as e:
        if not json_out:
            typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


def diff_cmd(
    run_a: str | None = typer.Argument(None, help="First run ID or prefix (default: latest run)"),
    run_b: str | None = typer.Argument(None, help="Second run ID (or use --baseline)"),
    baseline_path: Path | None = typer.Option(None, "--baseline", "-b", help="Baseline JSON file to compare against"),
    capture_session_id: str | None = typer.Option(
        None,
        "--capture",
        help="Claude Code session ID to import and evaluate",
    ),
    policy_path: Path | None = typer.Option(
        None,
        "--policy",
        help="Policy YAML file for capture gate mode",
    ),
    output_format: str = typer.Option("text", "--format", "-f", help="Output format: text, json, markdown"),
) -> None:
    """Inspect stored runs, or gate a Claude capture against a baseline."""
    try:
        config = load_config(capture=True) if capture_session_id is not None else _read_config(run_a)

        if capture_session_id is not None:
            if run_a is not None or run_b is not None:
                typer.echo(
                    "error: positional run IDs cannot be used with --capture",
                    err=True,
                )
                raise Exit(EXIT_NOT_FOUND)
            if baseline_path is None:
                typer.echo("error: --baseline is required with --capture", err=True)
                raise Exit(EXIT_NOT_FOUND)
            if output_format not in {"text", "json", "markdown"}:
                typer.echo(
                    "error: --format must be text, json, or markdown",
                    err=True,
                )
                raise Exit(EXIT_NOT_FOUND)

            try:
                bl = load_baseline(baseline_path)
            except FileNotFoundError:
                typer.echo(f"Baseline not found: {baseline_path}", err=True)
                raise Exit(EXIT_NOT_FOUND)
            except (json.JSONDecodeError, OSError, UnicodeError, ValueError):
                typer.echo(f"Invalid baseline file: {baseline_path}", err=True)
                raise Exit(EXIT_NOT_FOUND)

            policy = AssertionPolicy()
            selected_policy = policy_path
            if selected_policy is None:
                default_policy = LOCAL_DIR_NAME / "policy.yaml"
                if default_policy.is_file():
                    selected_policy = default_policy
            if selected_policy is not None:
                try:
                    policy = load_policy(selected_policy)
                except (
                    FileNotFoundError,
                    OSError,
                    UnicodeError,
                    ValueError,
                    yaml.YAMLError,
                ):
                    typer.echo(f"Invalid policy file: {selected_policy}", err=True)
                    raise Exit(EXIT_NOT_FOUND)

            imported = import_claude_capture(capture_session_id, config)
            typer.echo(
                f"Using Claude Code capture segment: {imported.segment}",
                err=True,
            )
            if imported.imported:
                typer.echo(
                    f"Imported Claude Code capture {imported.session_hash[:12]} "
                    f"segment {imported.segment} as {imported.trace_id[:8]}",
                    err=True,
                )
            else:
                typer.echo(
                    f"Claude Code capture {imported.session_hash[:12]} segment "
                    f"{imported.segment} is already imported as "
                    f"{imported.trace_id[:8]}",
                    err=True,
                )

            evaluation = evaluate_stored_run_against_baseline(
                imported.trace_id,
                bl,
                policy,
                config,
            )
            typer.echo(evaluation.render(output_format, baseline_path=baseline_path))
            if not evaluation.passed:
                raise Exit(1)
            return

        if policy_path is not None:
            typer.echo("error: --policy requires --capture", err=True)
            raise Exit(EXIT_NOT_FOUND)

        try:
            run_a = _resolve_run_or_latest(run_a, config)
        except FileNotFoundError as e:
            typer.echo(f"Run not found: {run_a or e}", err=True)
            raise Exit(EXIT_NOT_FOUND)

        bl = None
        resolved_b = None
        if baseline_path is not None:
            try:
                bl = load_baseline(baseline_path)
            except FileNotFoundError:
                typer.echo(f"Baseline not found: {baseline_path}", err=True)
                raise Exit(EXIT_NOT_FOUND)
        elif run_b is not None:
            try:
                resolved_b = storage.resolve_run_id(run_b, config)
            except FileNotFoundError:
                typer.echo(f"Run not found: {run_b}", err=True)
                raise Exit(EXIT_NOT_FOUND)
        else:
            typer.echo("error: provide either a second run ID or --baseline", err=True)
            raise Exit(EXIT_NOT_FOUND)

        d = compute_diff(run_a, run_b_id=resolved_b, baseline=bl, config=config)
        typer.echo(format_diff_text(d))
    except Exit:
        raise
    except ClaudeCaptureInputError as e:
        typer.echo(f"Invalid Claude Code capture: {e}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except ClaudeCaptureImportError as e:
        typer.echo(f"Claude Code import failed: {e}", err=True)
        raise Exit(EXIT_INTERNAL)
    except storage.UnsupportedTraceFormatError as e:
        if capture_session_id is not None:
            typer.echo(f"error: {e}", err=True)
            raise Exit(EXIT_INTERNAL)
        _exit_unsupported_trace_format(e)
    except storage.RunValidationError as e:
        if capture_session_id is not None:
            typer.echo(f"error: {e}", err=True)
            raise Exit(EXIT_INTERNAL)
        _exit_run_validation_error(e)
    except ValueError as e:
        typer.echo(f"Invalid configuration: {e}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)
