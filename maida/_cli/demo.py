"""CLI command implementation: demo."""

import json
from datetime import datetime, timedelta, timezone
from importlib import import_module
from pathlib import Path

import typer
from typer import Exit

import maida.storage as storage
from maida._assertions.engine import run_assertions
from maida._assertions.types import AssertionPolicy
from maida._cli.common import (
    _DEMO_TRACE_DURATION_MS,
    _PLAN_BACKEND_INSTALL_COMMAND,
    EXIT_INTERNAL,
    EXIT_NOT_FOUND,
    _exit_run_validation_error,
)
from maida.baseline import (
    create_baseline,
    save_baseline,
)
from maida.config import load_config
from maida.constants import LOCAL_DIR_NAME
from maida.demo import (
    ensure_demo_env,
    restore_demo_env,
    run_good_agent,
    run_refactored_agent,
)
from maida.diff import compute_diff
from maida.report import format_report_markdown, format_report_text
from maida.scaffold import (
    POLICY_RELPATH,
)


def _iso_add_ms(start_time: str | None, duration_ms: int) -> str | None:
    if not start_time:
        return None
    try:
        started = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    ended = (started + timedelta(milliseconds=duration_ms)).astimezone(timezone.utc)
    return ended.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _normalize_demo_trace_duration(
    trace_id: str,
    config,
    duration_ms: int = _DEMO_TRACE_DURATION_MS,
) -> None:
    """Make canned demo duration output stable without touching user traces."""
    paths = storage.get_run_paths(trace_id, config)
    meta_path = Path(paths["meta_json"])
    spans_path = Path(paths["spans_jsonl"])

    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["duration_ms"] = duration_ms
    ended_at = _iso_add_ms(meta.get("started_at"), duration_ms)
    if ended_at is not None:
        meta["ended_at"] = ended_at
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    spans = []
    for line in spans_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        span = json.loads(line)
        if span.get("parent_span_id") is None:
            span["duration_ms"] = duration_ms
            end_time = _iso_add_ms(span.get("start_time"), duration_ms)
            if end_time is not None:
                span["end_time"] = end_time
        spans.append(span)

    spans_path.write_text(
        "".join(json.dumps(span, ensure_ascii=False, default=str) + "\n" for span in spans),
        encoding="utf-8",
    )


def _demo_single_run(config) -> None:
    """Run the good demo agent once and print summary + next steps."""
    typer.echo("Running the bundled demo agent (simulated; nothing leaves")
    typer.echo("your machine, no API keys needed)...")
    run_good_agent()

    trace_id = storage.resolve_latest_trace_id(config)
    meta = storage.load_run_meta(trace_id, config)
    counts = meta.get("counts") or {}
    typer.echo("")
    typer.echo(f"✓ Run recorded: {trace_id[:8]} (status: {meta.get('status')})")
    typer.echo(
        f"  llm_calls={counts.get('llm_calls', 0)} "
        f"tool_calls={counts.get('tool_calls', 0)} "
        f"errors={counts.get('errors', 0)}"
    )
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  maida view              # inspect the timeline in your browser")
    typer.echo("  maida baseline          # snapshot this run as a baseline")
    typer.echo("  maida demo --regression # watch Maida catch a bad refactor")


def _demo_generated_plan(policy_path: Path | None) -> None:
    """Run the optional workflows backend's deterministic plan-refusal story."""
    try:
        backend = import_module("maida.workflows.demo")
    except ModuleNotFoundError as exc:
        if exc.name not in {"maida.workflows", "maida.workflows.demo"}:
            raise
        typer.echo(
            "maida-workflows is required for generated-plan gating.\n"
            "Install the optional backend with:\n"
            f"{_PLAN_BACKEND_INSTALL_COMMAND}",
            err=True,
        )
        raise Exit(EXIT_NOT_FOUND)

    selected_policy = policy_path
    if selected_policy is None and POLICY_RELPATH.is_file():
        selected_policy = POLICY_RELPATH
    result = backend.run_plan_demo(selected_policy)
    evidence = result["evidence"]
    schemas = result["schemas"]
    typer.echo("Maida generated-plan demo: everything below is simulated and local.")
    typer.echo("No API keys, database, network calls, or repo clone.")
    typer.echo("")
    typer.echo("── Step 1/2 | A simulated planner generates a runtime plan")
    typer.echo(f"   topology: {result['topology']}")
    typer.echo(f"   resolved: {result['node_count']} nodes | max fan-out {result['max_fanout']}")
    typer.echo(f"   schemas: policy {schemas['policy']} | plan {schemas['plan']} | report {schemas['report']}")
    typer.echo("")
    typer.echo("── Step 2/2 | Gate the trusted plan before execution")
    policy_source = str(selected_policy) if selected_policy is not None else "bundled demo refusal policy"
    typer.echo(f"   policy source: {policy_source}")
    typer.echo("")
    typer.echo(result["rendered"])
    typer.echo("")
    if evidence.valid:
        typer.echo("The supplied policy accepted this plan; the demo still does not execute it.")
    else:
        typer.echo("No generated module executed.")
        recovery_policy = selected_policy or POLICY_RELPATH
        typer.echo(f"Fix the plan or update {recovery_policy} after review, then gate again.")


def _demo_regression(config) -> None:
    """Baseline a good run, run a regressed one, and show the failing gate."""
    typer.echo("Maida regression demo: everything below is simulated and local.")
    typer.echo("Local-only canned data: no API keys, no network calls, no repo clone.")
    typer.echo("Story: baseline -> regressed refactor -> failed gate -> PR-comment preview.")
    typer.echo("")

    typer.echo("── Step 1/3 | Run the known-good agent and capture a baseline")
    typer.echo("   baseline behavior: lookup_customer -> search_kb -> send_reply")
    run_good_agent()
    good_id = storage.resolve_latest_trace_id(config)
    _normalize_demo_trace_duration(good_id, config)
    bl = create_baseline(good_id, config)
    bl_path = LOCAL_DIR_NAME / "baselines" / "demo-support-agent.json"
    save_baseline(bl, bl_path, force=True)
    typer.echo(f"   ✓ good run {good_id[:8]} | baseline saved to {bl_path}")
    typer.echo("")

    typer.echo('── Step 2/3 | A "refactor" ships: new prompt, cheaper model')
    typer.echo("   regression: demo-gpt-4-mini loops on search_kb, then escalates")
    run_refactored_agent()
    bad_id = storage.resolve_latest_trace_id(config)
    _normalize_demo_trace_duration(bad_id, config)
    typer.echo(f"   ✓ new run {bad_id[:8]} | finished with status ok; behavior still changed")
    typer.echo("")

    typer.echo("── Step 3/3 | Gate the new run against the baseline")
    typer.echo("   policy: no new tools, no loops, status ok, and cost near baseline")
    policy = AssertionPolicy(
        no_new_tools=True,
        no_loops=True,
        expect_status="ok",
        duration_tolerance=5.0,
    )
    report = run_assertions(bad_id, policy, baseline=bl, config=config)
    diff = compute_diff(bad_id, baseline=bl, config=config)
    typer.echo("")
    typer.echo(format_report_text(report, diff=diff))
    typer.echo("")

    if report.passed:
        typer.echo("Unexpected: the regression was not caught. Please report this!")
    else:
        typer.echo("In CI this blocks the merge. The PR comment would read:")
        typer.echo("")
        typer.echo("┄┄┄ PR comment preview ┄┄┄")
        typer.echo(format_report_markdown(report, diff=diff, baseline_path=str(bl_path)))
        typer.echo("┄┄┄ end preview ┄┄┄")
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo(f"  maida view {bad_id[:8]}      # inspect the regressed timeline")
    typer.echo(f"  maida diff {bad_id[:8]} --baseline {bl_path}")


def demo_cmd(
    regression: bool = typer.Option(
        False,
        "--regression",
        help="Full story: baseline a good run, then catch a bad refactor.",
    ),
    plan: bool = typer.Option(
        False,
        "--plan",
        help="Generate and refuse a runtime plan before execution.",
    ),
    policy: Path | None = typer.Option(
        None,
        "--policy",
        help=("Core policy 2.1 file for --plan (default: .maida/policy.yaml when present, otherwise bundled)."),
    ),
) -> None:
    """Run a bundled simulated agent and trace it. No network, no API keys."""
    try:
        if plan and regression:
            typer.echo("Choose either --plan or --regression, not both.", err=True)
            raise Exit(EXIT_NOT_FOUND)
        if policy is not None and not plan:
            typer.echo("--policy is only valid with --plan.", err=True)
            raise Exit(EXIT_NOT_FOUND)
        if plan:
            _demo_generated_plan(policy)
            return
        previous_demo_env = ensure_demo_env()
        try:
            config = load_config()
            if regression:
                _demo_regression(config)
            else:
                _demo_single_run(config)
        finally:
            restore_demo_env(previous_demo_env)
    except Exit:
        raise
    except storage.RunValidationError as e:
        _exit_run_validation_error(e)
    except Exception as e:
        typer.echo(f"error: {e}", err=True)
        raise Exit(EXIT_INTERNAL)
