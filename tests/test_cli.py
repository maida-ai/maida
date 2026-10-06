"""The public CLI facade retains its command registration contract."""

from typer.main import get_command

from maida.cli import app, capture_app, import_app, scenario_app


def test_public_cli_registers_all_command_groups():
    command = get_command(app)
    assert set(command.commands) == {
        "accept",
        "assert",
        "baseline",
        "capture",
        "check",
        "demo",
        "detach",
        "diff",
        "drift",
        "export",
        "extract",
        "import",
        "init",
        "list",
        "onboarding",
        "run",
        "scenario",
        "validate-trace",
        "view",
    }
    assert set(get_command(capture_app).commands) == {"claude-code", "claude-hook", "codex-hook"}
    assert set(get_command(import_app).commands) == {"claude-code", "langfuse"}
    assert set(command.commands["scenario"].commands) == {"run"}
    assert [registered.name for registered in scenario_app.registered_commands] == ["run"]
