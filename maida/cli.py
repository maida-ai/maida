"""
Typer CLI for Maida.

Commands: list, export, view, baseline, assert, diff.
Entrypoint: main() for console script maida.cli:main.
"""

import json
import socket
import threading
import time
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from typer import Exit

import maida.storage as storage
from maida.config import load_config
from maida.constants import SPEC_VERSION
from maida.server import create_app
from maida.events import spans_to_events
from maida import __version__

from maida.constants import LOCAL_DIR_NAME

EXIT_NOT_FOUND = 2
EXIT_INTERNAL = 10

app = typer.Typer(help="Maida CLI: list runs, export, or view in browser.")


def _version_callback(value: bool) -> None:
    if value:
        print(f"Maida {__version__}")
        raise typer.Exit()


@app.callback()
def version_callback(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            "-v",
            help="Show version and exit.",
            callback=_version_callback,
            is_eager=True,
            show_default=False,
        ),
    ] = None,
):
    """Show Maida version."""


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
        rows.append(
            [run_id, run_name, started_at, duration_str, str(llm), str(tool), status]
        )
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
        lines.append(
            sep.join(
                str(row[i]).ljust(col_widths[i])
                for i in range(min(len(row), len(col_widths)))
            )
        )
    return "\n".join(lines)


@app.command("list")
def list_cmd(
    limit: int = typer.Option(20, "--limit", "-n", help="Max runs to list"),
    json_out: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
) -> None:
    """List recent runs."""
    try:
        config = load_config()
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
    except Exception as e:
        if not json_out:
            typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


@app.command("export")
def export_cmd(
    run_id: str = typer.Argument(..., help="Run ID or trace ID prefix to export"),
    out: Path = typer.Option(
        ..., "--out", "-o", path_type=Path, help="Output JSON file path"
    ),
) -> None:
    """Export a run to a single JSON file (run metadata + events array)."""
    try:
        config = load_config()
        try:
            trace_id = storage.resolve_trace_id(run_id, config)
        except FileNotFoundError:
            raise Exit(EXIT_NOT_FOUND)
        try:
            run_meta = storage.load_run_meta(trace_id, config)
        except (ValueError, FileNotFoundError):
            raise Exit(EXIT_NOT_FOUND)
        spans = storage.load_spans(trace_id, config)
        events = spans_to_events(spans)
        payload = {"spec_version": SPEC_VERSION, "run": run_meta, "events": events}
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exit:
        raise
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


@app.command("view")
def view_cmd(
    run_id: str | None = typer.Argument(None, help="Run ID to view (default: latest)"),
    host: str = typer.Option("127.0.0.1", "--host", "-H", help="Bind host"),
    port: int = typer.Option(8712, "--port", "-p", help="Bind port"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Do not open browser"),
    json_out: bool = typer.Option(
        False, "--json", help="Print run_id, url, status as JSON then start server"
    ),
) -> None:
    """Start local viewer server and optionally open browser."""
    try:
        config = load_config()
        if run_id is None:
            runs = storage.list_runs(limit=1, config=config)
            if not runs:
                run_id = ""
            else:
                run_id = runs[0].get("trace_id") or runs[0].get("run_id") or ""
        if run_id:
            try:
                run_id = storage.resolve_trace_id(run_id, config)
            except FileNotFoundError as e:
                if not json_out:
                    typer.echo(f"Run not found: {e}", err=True)
                raise Exit(EXIT_NOT_FOUND)
            try:
                storage.load_run_meta(run_id, config)
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

        fastapi_app = create_app()
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
    except Exception as e:
        if not json_out:
            typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


@app.command("baseline")
def baseline_cmd(
    run_id: str = typer.Argument(..., help="Run ID or prefix to snapshot"),
    out: Path | None = typer.Option(
        None, "--out", "-o", help="Output path for baseline JSON"
    ),
) -> None:
    """Capture a baseline snapshot from a completed run."""
    from maida.baseline import create_baseline, save_baseline

    try:
        config = load_config()
        try:
            run_id = storage.resolve_trace_id(run_id, config)
        except FileNotFoundError:
            typer.echo(f"Run not found: {run_id}", err=True)
            raise Exit(EXIT_NOT_FOUND)

        bl = create_baseline(run_id, config)

        if out is None:
            name_part = bl.get("source_run_name") or run_id
            out = LOCAL_DIR_NAME / "baselines" / f"{name_part}.json"

        save_baseline(bl, out)
        typer.echo(f"Baseline saved to {out}")
    except Exit:
        raise
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


@app.command(name="assert")
def assert_cmd(
    run_id: str = typer.Argument(..., help="Run ID or prefix to check"),
    baseline_path: Path | None = typer.Option(
        None, "--baseline", "-b", help="Baseline JSON file to compare against"
    ),
    policy_path: Path | None = typer.Option(None, "--policy", help="Policy YAML file"),
    max_steps: int | None = typer.Option(
        None, "--max-steps", help="Max total events allowed"
    ),
    step_tolerance: float | None = typer.Option(
        None, "--step-tolerance", help="Fractional tolerance for step count"
    ),
    max_tool_calls: int | None = typer.Option(
        None, "--max-tool-calls", help="Max tool calls allowed"
    ),
    tool_call_tolerance: float | None = typer.Option(
        None, "--tool-call-tolerance", help="Fractional tolerance for tool calls"
    ),
    no_new_tools: bool = typer.Option(
        False, "--no-new-tools", help="Fail if run uses tools not in baseline"
    ),
    no_loops: bool = typer.Option(
        False, "--no-loops", help="Fail if any LOOP_WARNING present"
    ),
    no_guardrails: bool = typer.Option(
        False, "--no-guardrails", help="Fail if any guardrail was triggered"
    ),
    max_cost_tokens: int | None = typer.Option(
        None, "--max-cost-tokens", help="Max total tokens allowed"
    ),
    cost_tolerance: float | None = typer.Option(
        None, "--cost-tolerance", help="Fractional tolerance for token cost"
    ),
    max_duration_ms: int | None = typer.Option(
        None, "--max-duration-ms", help="Max run duration in ms"
    ),
    duration_tolerance: float | None = typer.Option(
        None, "--duration-tolerance", help="Fractional tolerance for duration"
    ),
    expect_status: str | None = typer.Option(
        None, "--expect-status", help="Expected run status (ok or error)"
    ),
    output_format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text, json, markdown"
    ),
) -> None:
    """Assert that a run meets behavioral policy checks. Exit 0 = pass, 1 = fail."""
    from maida.assertions import (
        AssertionPolicy,
        format_report_json,
        format_report_markdown,
        format_report_text,
        run_assertions,
    )
    from maida.baseline import load_baseline

    try:
        config = load_config()
        try:
            run_id = storage.resolve_trace_id(run_id, config)
        except FileNotFoundError:
            typer.echo(f"Run not found: {run_id}", err=True)
            raise Exit(EXIT_NOT_FOUND)

        # Build policy: start from file, then overlay CLI flags
        from maida.policy import load_policy, merge_policy

        policy = AssertionPolicy()
        if policy_path is not None:
            policy = load_policy(policy_path)
        else:
            default_policy = LOCAL_DIR_NAME / "policy.yaml"
            if default_policy.is_file():
                policy = load_policy(default_policy)

        cli_overrides = {
            "max_steps": max_steps,
            "step_tolerance": step_tolerance,
            "max_tool_calls": max_tool_calls,
            "tool_call_tolerance": tool_call_tolerance,
            "no_new_tools": no_new_tools,
            "no_loops": no_loops,
            "no_guardrails": no_guardrails,
            "max_cost_tokens": max_cost_tokens,
            "cost_tolerance": cost_tolerance,
            "max_duration_ms": max_duration_ms,
            "duration_tolerance": duration_tolerance,
            "expect_status": expect_status,
        }
        policy = merge_policy(policy, cli_overrides)

        # Load baseline if provided
        bl = None
        if baseline_path is not None:
            try:
                bl = load_baseline(baseline_path)
            except FileNotFoundError:
                typer.echo(f"Baseline not found: {baseline_path}", err=True)
                raise Exit(EXIT_NOT_FOUND)
            except json.JSONDecodeError:
                typer.echo(f"Invalid baseline file: {baseline_path}", err=True)
                raise Exit(EXIT_NOT_FOUND)

        report = run_assertions(run_id, policy, baseline=bl, config=config)

        if output_format == "json":
            typer.echo(format_report_json(report))
        elif output_format == "markdown":
            typer.echo(format_report_markdown(report))
        else:
            typer.echo(format_report_text(report))

        if not report.passed:
            raise Exit(1)
    except Exit:
        raise
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


@app.command("diff")
def diff_cmd(
    run_a: str = typer.Argument(..., help="First run ID or prefix"),
    run_b: str | None = typer.Argument(None, help="Second run ID (or use --baseline)"),
    baseline_path: Path | None = typer.Option(
        None, "--baseline", "-b", help="Baseline JSON file to compare against"
    ),
    output_format: str = typer.Option(
        "text", "--format", "-f", help="Output format: text"
    ),
) -> None:
    """Compare two runs or a run against a baseline."""
    from maida.baseline import load_baseline
    from maida.diff import compute_diff, format_diff_text

    try:
        config = load_config()
        try:
            run_a = storage.resolve_trace_id(run_a, config)
        except FileNotFoundError:
            typer.echo(f"Run not found: {run_a}", err=True)
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
                resolved_b = storage.resolve_trace_id(run_b, config)
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
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


def main() -> None:
    """CLI entrypoint (console script maida.cli:main)."""
    app()


if __name__ == "__main__":
    main()
