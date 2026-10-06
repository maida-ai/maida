"""CLI command implementation: gating."""

import json
from pathlib import Path

import typer
from typer import Exit

import maida.storage as storage
from maida import __version__
from maida._assertions.engine import run_assertions
from maida._assertions.types import AssertionPolicy
from maida._cli.common import (
    EXIT_INTERNAL,
    EXIT_NOT_FOUND,
    _acceptance_source_from_environment,
    _exit_run_validation_error,
    _exit_unsupported_trace_format,
    _read_config,
    _resolve_run_or_latest,
)
from maida.acceptance import accept_baseline_update
from maida.baseline import (
    create_baseline,
    create_baseline_from_report,
    load_baseline,
    save_baseline,
)
from maida.capture.providers import attached_providers
from maida.config import load_config
from maida.constants import LOCAL_DIR_NAME
from maida.diff import format_diff_text
from maida.evaluation import evaluate_stored_run_against_baseline
from maida.first_run import maida_command
from maida.onboarding import record_automatically
from maida.policy import load_policy, merge_policy
from maida.project_local import captured_task, installation
from maida.report import format_report_json, format_report_markdown, format_report_text
from maida.usage import report_usage


def baseline_cmd(
    run_id: str | None = typer.Argument(None, help="Run ID or prefix to snapshot (default: latest run)"),
    from_report: Path | None = typer.Option(
        None,
        "--from-report",
        help="Capture the immutable trial sample from a report v2 JSON file",
    ),
    out: Path | None = typer.Option(None, "--out", "-o", help="Output path for baseline JSON"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing baseline"),
) -> None:
    """Capture a baseline snapshot from a completed run."""
    try:
        config = _read_config(run_id)
        if from_report is not None:
            if run_id is not None:
                raise ValueError("RUN_ID and --from-report are mutually exclusive")
            report_payload = json.loads(from_report.read_text(encoding="utf-8"))
            bl = create_baseline_from_report(report_payload)
            run_id = bl.get("source_run_id") or "baseline"
        else:
            try:
                run_id = _resolve_run_or_latest(run_id, config)
            except FileNotFoundError as e:
                typer.echo(f"Run not found: {run_id or e}", err=True)
                raise Exit(EXIT_NOT_FOUND)
            bl = create_baseline(run_id, config)

        if out is None:
            name_part = bl.get("source_run_name") or run_id
            out = LOCAL_DIR_NAME / "baselines" / f"{name_part}.json"

        save_baseline(bl, out, force=force)
        typer.echo(f"Baseline saved to {out}")
    except Exit:
        raise
    except storage.UnsupportedTraceFormatError as e:
        _exit_unsupported_trace_format(e)
    except storage.RunValidationError as e:
        _exit_run_validation_error(e)
    except FileExistsError as e:
        typer.echo(str(e), err=True)
        raise Exit(EXIT_NOT_FOUND)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        typer.echo(f"Invalid baseline input: {e}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


def accept_cmd(
    run_id: str | None = typer.Argument(None, help="Run ID or prefix to accept (default: latest run)"),
    baseline_path: Path = typer.Option(..., "--baseline", "-b", help="Baseline JSON file to update"),
    reason: str | None = typer.Option(
        None,
        "--reason",
        "--message",
        "-m",
        help="Required acceptance reason for the baseline update",
    ),
) -> None:
    """Accept an intentional behavior change by updating a baseline."""
    try:
        if reason is None or not reason.strip():
            typer.echo(
                "Acceptance reason required: pass --reason TEXT or -m TEXT.",
                err=True,
            )
            raise Exit(EXIT_NOT_FOUND)
        reason = reason.strip()

        config = _read_config(run_id)
        try:
            run_id = _resolve_run_or_latest(run_id, config)
        except FileNotFoundError as e:
            typer.echo(f"Run not found: {run_id or e}", err=True)
            raise Exit(EXIT_NOT_FOUND)

        try:
            existing_baseline = load_baseline(baseline_path)
        except FileNotFoundError:
            typer.echo(f"Baseline not found: {baseline_path}", err=True)
            raise Exit(EXIT_NOT_FOUND)
        except (json.JSONDecodeError, ValueError):
            typer.echo(f"Invalid baseline file: {baseline_path}", err=True)
            raise Exit(EXIT_NOT_FOUND)

        if not isinstance(existing_baseline, dict):
            typer.echo(f"Invalid baseline file: {baseline_path}", err=True)
            raise Exit(EXIT_NOT_FOUND)

        result = accept_baseline_update(
            run_id=run_id,
            baseline_path=baseline_path,
            existing_baseline=existing_baseline,
            reason=reason,
            maida_version=__version__,
            config=config,
            source=_acceptance_source_from_environment(),
        )

        if result.updated:
            typer.echo(f"Baseline updated: {baseline_path}")
            typer.echo(f"Accepted run: {result.source_run_id[:8]}")
            typer.echo(f"Previous baseline: {str(result.previous_source_run_id or 'unknown')[:8]}")
            typer.echo("")
            typer.echo(format_diff_text(result.diff))
        else:
            typer.echo(f"Baseline already matches run; no update written: {baseline_path}")
            typer.echo(f"Matched run: {result.source_run_id[:8]}")
    except Exit:
        raise
    except storage.UnsupportedTraceFormatError as e:
        _exit_unsupported_trace_format(e)
    except storage.RunValidationError as e:
        _exit_run_validation_error(e)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


def assert_cmd(
    run_id: str | None = typer.Argument(None, help="Run ID or prefix to check (default: latest run)"),
    baseline_path: Path | None = typer.Option(None, "--baseline", "-b", help="Baseline JSON file to compare against"),
    policy_path: Path | None = typer.Option(None, "--policy", help="Policy YAML file"),
    max_steps: int | None = typer.Option(None, "--max-steps", help="Max total events allowed"),
    step_tolerance: float | None = typer.Option(None, "--step-tolerance", help="Fractional tolerance for step count"),
    max_tool_calls: int | None = typer.Option(None, "--max-tool-calls", help="Max tool calls allowed"),
    tool_call_tolerance: float | None = typer.Option(
        None, "--tool-call-tolerance", help="Fractional tolerance for tool calls"
    ),
    no_new_tools: bool = typer.Option(False, "--no-new-tools", help="Fail if run uses tools not in baseline"),
    no_loops: bool = typer.Option(False, "--no-loops", help="Fail if any LOOP_WARNING present"),
    no_guardrails: bool = typer.Option(False, "--no-guardrails", help="Fail if any guardrail was triggered"),
    max_cost_tokens: int | None = typer.Option(None, "--max-cost-tokens", help="Max total tokens allowed"),
    cost_tolerance: float | None = typer.Option(None, "--cost-tolerance", help="Fractional tolerance for token cost"),
    max_duration_ms: int | None = typer.Option(None, "--max-duration-ms", help="Max run duration in ms"),
    duration_tolerance: float | None = typer.Option(
        None, "--duration-tolerance", help="Fractional tolerance for duration"
    ),
    expect_status: str | None = typer.Option(None, "--expect-status", help="Expected run status (ok or error)"),
    ignore_check: list[str] = typer.Option([], "--ignore-check", help="Skip a check (repeatable)"),
    output_format: str = typer.Option("text", "--format", "-f", help="Output format: text, json, markdown"),
) -> None:
    """Assert that a run meets behavioral policy checks. Exit 0 = pass, 1 = fail."""
    try:
        config = _read_config(run_id)
        try:
            run_id = _resolve_run_or_latest(run_id, config)
        except FileNotFoundError as e:
            typer.echo(f"Run not found: {run_id or e}", err=True)
            raise Exit(EXIT_NOT_FOUND)

        # Build policy: start from file, then overlay CLI flags
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
            "ignored_checks": ignore_check or None,
        }
        policy = merge_policy(policy, cli_overrides)

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

        if bl is not None:
            evaluation = evaluate_stored_run_against_baseline(run_id, bl, policy, config)
            report = evaluation.report
            render_format = output_format if output_format in {"text", "json", "markdown"} else "text"
            typer.echo(evaluation.render(render_format, baseline_path=baseline_path))
        else:
            report = run_assertions(run_id, policy, config=config)
            if output_format == "json":
                typer.echo(format_report_json(report))
            elif output_format == "markdown":
                typer.echo(format_report_markdown(report))
            else:
                typer.echo(format_report_text(report))
                if config.project_id:
                    typer.echo(f"\nRepository: {config.project_root}")
                    typer.echo(
                        "Coverage: observed tool activity and session lifecycle; answer correctness and "
                        "complete model-call, token, and latency coverage are outside this check."
                    )
                    if not report.passed:
                        typer.echo(f"Next: inspect this task with maida view {run_id}")

        typer.echo(report_usage("pass" if report.passed else "fail"), err=True)
        if not report.passed:
            raise Exit(1)
    except Exit:
        raise
    except storage.UnsupportedTraceFormatError as e:
        _exit_unsupported_trace_format(e)
    except storage.RunValidationError as e:
        _exit_run_validation_error(e)
    except ValueError as e:
        typer.echo(f"Invalid configuration: {e}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)


def check_cmd(
    output_format: str = typer.Option("text", "--format", "-f", help="Output format: text, json, markdown"),
    agent: str | None = typer.Option(None, "--agent", help="Capture provider: claude-code or codex"),
) -> None:
    """Check the latest captured task and show how to inspect the same evidence."""
    command = f"{maida_command()} check"
    if output_format not in {"text", "json", "markdown"}:
        typer.echo(f"Unknown report format. Use {command} --format text, json or markdown.", err=True)
        raise Exit(EXIT_NOT_FOUND)
    try:
        local = installation(Path.cwd(), command=f"{maida_command()} init")
        if local is None or not attached_providers(local[1]):
            typer.echo(
                f"Capture is not attached here. Run {maida_command()} init in this repository to attach it.",
                err=True,
            )
            raise Exit(EXIT_NOT_FOUND)
        config = load_config(project_root=local[0], capture=True)
        try:
            run_id, runtime = captured_task(config, local[1], agent=agent, command=command)
        except FileNotFoundError as exc:
            typer.echo(str(exc), err=True)
            raise Exit(EXIT_NOT_FOUND)
        policy = AssertionPolicy(expect_status="ok", no_loops=True, no_guardrails=True)
        try:
            report = run_assertions(run_id, policy, config=config)
        except (FileNotFoundError, storage.RunValidationError, storage.UnsupportedTraceFormatError) as exc:
            typer.echo(
                "The captured task cannot be read. Start a new "
                + (
                    "Claude Code session here, run one bounded task, exit"
                    if runtime == "claude-code"
                    else "Codex session, run one bounded task, let it finish and exit Codex"
                )
                + f", then rerun {command}.",
                err=True,
            )
            raise Exit(EXIT_NOT_FOUND) from exc
        render = {"text": format_report_text, "json": format_report_json, "markdown": format_report_markdown}
        typer.echo(render[output_format](report))
        if output_format == "text":
            typer.echo(f"\nRepository: {config.project_root}")
            if runtime == "codex":
                typer.echo("Source: native Codex hooks (turn completion and paired observed tools).")
            typer.echo(
                "Coverage: observed tool activity and lifecycle; answer correctness and "
                "complete tool, model-call, token, cost, and latency coverage are outside this check."
            )
        typer.echo(f"Trace: {run_id}\nView: {maida_command()} view {run_id}", err=output_format != "text")
        record_automatically("own-task-captured", root=local[0])
        record_automatically("first-report", root=local[0])
        typer.echo(report_usage("pass" if report.passed else "fail"), err=True)
        if not report.passed:
            raise Exit(1)
    except Exit:
        raise
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise Exit(EXIT_NOT_FOUND)
    except OSError as exc:
        typer.echo(
            f"Cannot read this repository's captured task. Check its file permissions, then rerun {command}.", err=True
        )
        raise Exit(EXIT_NOT_FOUND) from exc
    except Exception as exc:
        typer.echo(f"Could not check the captured task due to an internal error. Rerun {command}.", err=True)
        raise Exit(EXIT_INTERNAL) from exc
