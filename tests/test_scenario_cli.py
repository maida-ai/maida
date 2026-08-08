"""CLI tests for ``maida scenario run``."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.cli import app
from maida.scenario import (
    ScenarioInputError,
    ScenarioResult,
    ScenarioRunReport,
    ScenarioStatus,
)


@pytest.mark.parametrize("exit_code", [0, 1, 10])
def test_scenario_cli_preserves_report_exit_and_stdout(monkeypatch, exit_code):
    report = ScenarioRunReport(
        results=[
            ScenarioResult(
                scenario_id="example",
                status=ScenarioStatus(
                    "pass"
                    if exit_code == 0
                    else "assertion_failed"
                    if exit_code == 1
                    else "agent_failed"
                ),
            )
        ]
    )
    monkeypatch.setattr("maida.cli.run_scenario_file", lambda *args, **kwargs: report)

    result = CliRunner().invoke(
        app,
        ["scenario", "run", "custom.yaml", "--scenario", "example", "--format", "json"],
    )

    assert result.exit_code == exit_code
    assert '"scenario_id": "example"' in result.stdout
    assert result.stderr == ""


def test_scenario_cli_defaults_manifest_and_maps_preflight_to_exit_two(monkeypatch):
    observed: dict[str, object] = {}

    def fail(path, **kwargs):
        observed["path"] = path
        observed.update(kwargs)
        raise ScenarioInputError("Claude Code version mismatch")

    monkeypatch.setattr("maida.cli.run_scenario_file", fail)
    result = CliRunner().invoke(app, ["scenario", "run"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Invalid scenario manifest/environment" in result.stderr
    assert "version mismatch" in result.stderr
    assert observed["path"] == Path(".maida/scenarios.yaml")


def test_scenario_cli_invalid_format_is_exit_two(monkeypatch):
    monkeypatch.setattr(
        "maida.cli.run_scenario_file",
        lambda *args, **kwargs: pytest.fail("runner should not be called"),
    )
    result = CliRunner().invoke(app, ["scenario", "run", "--format", "xml"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "format must be text, json, or markdown" in result.stderr
