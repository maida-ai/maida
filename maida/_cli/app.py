"""Construct and register the CLI without command-to-app imports."""

import typer

from maida._cli.capture import (
    capture_claude_code_cmd,
    capture_claude_hook_cmd,
    capture_codex_hook_cmd,
    import_claude_code_cmd,
    import_langfuse_cmd,
)
from maida._cli.common import validate_trace_cmd
from maida._cli.demo import demo_cmd
from maida._cli.execution import drift_cmd, extract_cmd, run_cmd, scenario_run_cmd
from maida._cli.gating import accept_cmd, assert_cmd, baseline_cmd, check_cmd
from maida._cli.inspection import diff_cmd, export_cmd, list_cmd, view_cmd
from maida._cli.setup import detach_cmd, init_cmd
from maida._cli.version import version_callback
from maida.onboarding import app as onboarding_app

app = typer.Typer(help="Capture, inspect, and gate agent behavior.")
capture_app = typer.Typer(help="Capture external agent behavior locally.")

import_app = typer.Typer(help="Import existing traces into local Maida storage.")

scenario_app = typer.Typer(help="Run isolated capture-backed agent scenarios.")

app.add_typer(capture_app, name="capture")

app.add_typer(import_app, name="import")

app.add_typer(scenario_app, name="scenario")

app.add_typer(onboarding_app, name="onboarding")


app.callback()(version_callback)
scenario_app.command("run")(scenario_run_cmd)
app.command("validate-trace")(validate_trace_cmd)
capture_app.command("claude-code")(capture_claude_code_cmd)
capture_app.command("claude-hook")(capture_claude_hook_cmd)
capture_app.command("codex-hook")(capture_codex_hook_cmd)
import_app.command("claude-code")(import_claude_code_cmd)
import_app.command("langfuse")(import_langfuse_cmd)
app.command(name="run")(run_cmd)
app.command(name="drift")(drift_cmd)
app.command(name="extract")(extract_cmd)
app.command("list")(list_cmd)
app.command("export")(export_cmd)
app.command("view")(view_cmd)
app.command("baseline")(baseline_cmd)
app.command("accept")(accept_cmd)
app.command(name="assert")(assert_cmd)
app.command("check")(check_cmd)
app.command("init")(init_cmd)
app.command("detach")(detach_cmd)
app.command("demo")(demo_cmd)
app.command("diff")(diff_cmd)
