"""CLI command implementation: execution."""

import json
import os
import subprocess
from pathlib import Path

import typer
from typer import Exit

from maida._assertions.types import AssertionPolicy
from maida._cli.common import EXIT_INTERNAL, EXIT_NOT_FOUND
from maida.baseline import (
    load_baseline,
)
from maida.config import load_config
from maida.constants import LOCAL_DIR_NAME
from maida.drift import DriftWindowError, run_drift
from maida.extract import ExtractionInputError, extract_window
from maida.policy import load_policy, merge_policy
from maida.runner import RunExecutionError, run_trials
from maida.scenario import (
    DEFAULT_SCENARIO_MANIFEST,
    ScenarioInputError,
    run_scenario_file,
)
from maida.statistics import GateVerdict
from maida.usage import report_usage


def scenario_run_cmd(
    manifest: Path = typer.Argument(
        DEFAULT_SCENARIO_MANIFEST,
        help="Scenario manifest (default: .maida/scenarios.yaml)",
    ),
    scenario_id: str | None = typer.Option(
        None,
        "--scenario",
        help="Run only the selected scenario ID",
    ),
    output_format: str = typer.Option(
        "text",
        "--format",
        "-f",
        help="Output format: text, json, or markdown",
    ),
) -> None:
    """Run headless Claude Code scenarios in isolated workspaces."""
    if output_format not in {"text", "json", "markdown"}:
        typer.echo("error: format must be text, json, or markdown", err=True)
        raise Exit(EXIT_NOT_FOUND)
    try:
        report = run_scenario_file(manifest, scenario_id=scenario_id)
        typer.echo(report.render(output_format))
        if report.exit_code:
            raise Exit(report.exit_code)
    except Exit:
        raise
    except ScenarioInputError as exc:
        typer.echo(
            f"Invalid scenario manifest/environment: {exc}",
            err=True,
        )
        raise Exit(EXIT_NOT_FOUND)
    except Exception:
        typer.echo("error: scenario execution failed", err=True)
        raise Exit(EXIT_INTERNAL)


def run_cmd(
    agent_script: Path = typer.Argument(..., help="Traced Python agent script to run"),
    trials: int | None = typer.Option(None, "--trials", min=1, help="Number of isolated trials (policy default: 3)"),
    confidence_level: float | None = typer.Option(
        None,
        "--confidence-level",
        help="Wilson confidence level (policy default: 0.95)",
    ),
    pass_rate_threshold: float | None = typer.Option(
        None,
        "--pass-rate-threshold",
        help="Required underlying pass rate (policy default: 0.90)",
    ),
    baseline_path: Path | None = typer.Option(None, "--baseline", "-b", help="Baseline JSON file to compare against"),
    policy_path: Path | None = typer.Option(None, "--policy", help="Policy YAML file"),
    max_steps: int | None = typer.Option(None, "--max-steps", help="Max total events allowed"),
    fail_fast: bool | None = typer.Option(
        None,
        "--fail-fast/--no-fail-fast",
        help="Stop once a blocking failure is irreversible",
    ),
    output_format: str = typer.Option("text", "--format", "-f", help="Output format: text, json, or markdown"),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Also write the machine-readable report to this path"
    ),
) -> None:
    """Run a traced agent repeatedly in isolated workspace copies."""
    try:
        if not agent_script.is_file():
            typer.echo(f"Agent script not found: {agent_script}", err=True)
            raise Exit(EXIT_NOT_FOUND)
        if output_format not in {"text", "json", "markdown"}:
            raise ValueError("format must be 'text', 'json', or 'markdown'")

        config = load_config()
        policy = AssertionPolicy()
        selected_policy = policy_path
        if selected_policy is None:
            default_policy = LOCAL_DIR_NAME / "policy.yaml"
            if default_policy.is_file():
                selected_policy = default_policy
        if selected_policy is not None:
            policy = load_policy(selected_policy)
        policy = merge_policy(
            policy,
            {
                "max_steps": max_steps,
                "trials": trials,
                "confidence_level": confidence_level,
                "pass_rate_threshold": pass_rate_threshold,
                "fail_fast": fail_fast,
            },
        )

        baseline = None
        if baseline_path is not None:
            try:
                baseline = load_baseline(baseline_path)
            except (FileNotFoundError, json.JSONDecodeError, ValueError):
                typer.echo(f"Invalid or missing baseline: {baseline_path}", err=True)
                raise Exit(EXIT_NOT_FOUND)

        report = run_trials(
            agent_script,
            trials=policy.trials,
            policy=policy,
            config=config,
            project_root=Path.cwd(),
            baseline=baseline,
            confidence_level=policy.confidence_level,
            pass_rate_threshold=policy.pass_rate_threshold,
        )
        if json_out is not None:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            temporary = json_out.with_name(f".{json_out.name}.{os.getpid()}.tmp")
            temporary.write_text(report.to_json() + "\n", encoding="utf-8")
            os.replace(temporary, json_out)

        if output_format == "json":
            rendered = report.to_json()
        elif output_format == "markdown":
            rendered = report.to_markdown(baseline_path=str(baseline_path) if baseline_path is not None else None)
        else:
            rendered = report.to_text()
        typer.echo(rendered)
        typer.echo(report_usage(report.verdict.value), err=True)
        if report.verdict is GateVerdict.FAIL:
            raise Exit(1)
    except Exit:
        raise
    except ValueError as error:
        typer.echo(f"Invalid configuration: {error}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except (RunExecutionError, subprocess.SubprocessError) as error:
        typer.echo(f"error: {error}", err=True)
        raise Exit(EXIT_INTERNAL)
    except Exception as error:
        typer.echo(f"error: {error}", err=True)
        raise Exit(EXIT_INTERNAL)


def drift_cmd(
    window: Path = typer.Option(..., "--window", help="Native Maida runs directory to evaluate"),
    baseline_path: Path = typer.Option(..., "--baseline", "-b", help="Baseline JSON file for one agent"),
    policy_path: Path | None = typer.Option(None, "--policy", help="Policy YAML file"),
    agent_name: str | None = typer.Option(
        None,
        "--agent",
        help="Agent run name; required when the baseline does not record one",
    ),
    output_format: str = typer.Option("text", "--format", "-f", help="Output format: text, json, or markdown"),
    json_out: Path | None = typer.Option(
        None, "--json-out", help="Also write the machine-readable report to this path"
    ),
) -> None:
    """Check a persisted production trace window for behavioral regressions."""
    try:
        if output_format not in {"text", "json", "markdown"}:
            raise ValueError("format must be 'text', 'json', or 'markdown'")
        if not baseline_path.is_file():
            typer.echo(
                f"Baseline must be a JSON file: {baseline_path}. "
                "Run once per baseline; baseline-directory fanout is not yet supported.",
                err=True,
            )
            raise Exit(EXIT_NOT_FOUND)
        try:
            baseline = load_baseline(baseline_path)
        except (json.JSONDecodeError, ValueError, OSError) as error:
            typer.echo(f"Invalid baseline {baseline_path}: {error}", err=True)
            raise Exit(EXIT_NOT_FOUND)

        policy = AssertionPolicy()
        selected_policy = policy_path
        if selected_policy is None:
            default_policy = LOCAL_DIR_NAME / "policy.yaml"
            if default_policy.is_file():
                selected_policy = default_policy
        if selected_policy is not None:
            policy = load_policy(selected_policy)

        report = run_drift(
            window,
            baseline=baseline,
            policy=policy,
            config=load_config(),
            agent_name=agent_name,
        )
        if json_out is not None:
            json_out.parent.mkdir(parents=True, exist_ok=True)
            temporary = json_out.with_name(f".{json_out.name}.{os.getpid()}.tmp")
            temporary.write_text(report.to_json() + "\n", encoding="utf-8")
            os.replace(temporary, json_out)

        if output_format == "json":
            rendered = report.to_json()
        elif output_format == "markdown":
            rendered = report.to_markdown(baseline_path=str(baseline_path))
        else:
            rendered = report.to_text()
        typer.echo(rendered)
        if report.verdict is GateVerdict.FAIL:
            raise Exit(1)
    except Exit:
        raise
    except (DriftWindowError, FileNotFoundError, ValueError) as error:
        typer.echo(f"Invalid drift input: {error}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as error:
        typer.echo(f"error: {error}", err=True)
        raise Exit(EXIT_INTERNAL)


def extract_cmd(
    window: Path = typer.Option(..., "--window", help="Native Maida runs directory to extract"),
    out_dir: Path = typer.Option(..., "--out", help="New directory for the inactive gate draft"),
    workflow: list[str] | None = typer.Option(
        None,
        "--workflow",
        help="Exact run_name to include; repeat to select multiple workflows",
    ),
    json_out: bool = typer.Option(False, "--json", help="Print the machine-readable draft manifest"),
) -> None:
    """Extract review-required gate drafts from completed native traces."""
    try:
        draft = extract_window(
            window,
            out_dir=out_dir,
            config=load_config(),
            workflows=workflow,
        )
        if json_out:
            typer.echo(json.dumps(draft, ensure_ascii=False, indent=2))
        else:
            count = len(draft["workflows"])
            suffix = "" if count == 1 else "s"
            typer.echo(f"Extracted {count} workflow draft{suffix}:")
            for item in draft["workflows"]:
                typer.echo(f"- {item['run_name']} -> {item['artifact_dir']}")
        typer.echo(
            f"Draft written to {out_dir}. Human review is required before activation.",
            err=True,
        )
    except Exit:
        raise
    except ExtractionInputError as error:
        typer.echo(f"Invalid extraction input: {error}", err=True)
        raise Exit(EXIT_NOT_FOUND)
    except Exception as error:
        typer.echo(f"Extraction failed: {error}", err=True)
        raise Exit(EXIT_INTERNAL)
