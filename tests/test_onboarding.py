"""Local attempt measurement keeps incomplete and assisted attempts visible."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.cli import app


runner = CliRunner()


def command(*args):
    result = runner.invoke(app, ["onboarding", *args])
    assert result.exit_code == 0, result.output
    return result


def test_unknown_assistance_is_not_unassisted_activation(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    command("start")
    for milestone in ("baseline-reviewed", "gate-pass", "regression-caught"):
        command("record", "--milestone", milestone)
    report = json.loads(command("report", "--json").stdout)
    assert report["attempts"] == 1
    assert report["activated"] == 1
    assert report["unassisted"] == 0
    assert report["unknown_assistance"] == 1
    assert report["evidence"] == "self-reported local workflow milestones"
    assert not Path(".maida").exists()


def test_attempts_measure_founder_maintenance_separately_and_keep_failures(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    command("start", "--assistance", "none")
    command("record", "--phase", "setup", "--minutes", "4", "--actor", "user")
    command("record", "--milestone", "baseline-reviewed")
    command("record", "--milestone", "gate-pass")
    command("record", "--milestone", "regression-caught")
    before = json.loads(command("report", "--json").stdout)
    assert before["unassisted"] == 1
    command("record", "--phase", "maintenance", "--minutes", "12", "--actor", "founder")
    command("start", "--assistance", "founder")
    command("record", "--phase", "setup", "--minutes", "30", "--actor", "founder", "--outcome", "blocked")
    report = json.loads(command("report", "--json").stdout)
    assert report["attempts"] == 2
    assert report["activated"] == 1
    assert report["unassisted"] == 1  # later maintenance cannot rewrite first-activation assistance
    assert report["blocked"] == 1
    assert report["activation_rate"] == 0.5
    assert report["effort_minutes"]["founder"]["setup"] == 30
    assert report["effort_minutes"]["founder"]["maintenance"] == 12
    assert report["effort_minutes"]["user"]["setup"] == 4
    assert len(report["time_to_activation_seconds"]) == 1


@pytest.mark.parametrize(
    "args",
    [
        ["start", "--assistance", "maybe"],
        ["record", "--minutes", "-1", "--phase", "setup"],
        ["record", "--minutes", "nan", "--phase", "setup"],
        ["record", "--minutes", "2"],
        ["record", "--milestone", "demo-pass"],
        ["record"],
    ],
)
def test_invalid_measurement_never_creates_or_changes_journal(temp_data_dir, tmp_path, monkeypatch, args):
    monkeypatch.chdir(tmp_path)
    command("start")
    before = {p: p.read_bytes() for p in temp_data_dir.rglob("*.json")}
    result = runner.invoke(app, ["onboarding", *args])
    assert result.exit_code == 2
    assert {p: p.read_bytes() for p in temp_data_dir.rglob("*.json")} == before


def test_report_empty_and_record_without_attempt_are_actionable(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    report = json.loads(command("report", "--json").stdout)
    assert report["attempts"] == 0
    assert report["activation_rate"] is None
    result = runner.invoke(app, ["onboarding", "record", "--milestone", "gate-pass"])
    assert result.exit_code == 2
    assert "onboarding start" in result.output


def test_founder_help_before_activation_prevents_unassisted_label(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    command("start", "--assistance", "none")
    command("record", "--phase", "setup", "--actor", "founder", "--minutes", "1")
    for milestone in ("baseline-reviewed", "gate-pass", "regression-caught"):
        command("record", "--milestone", milestone)
    report = json.loads(command("report", "--json").stdout)
    assert report["unassisted"] == 0
    assert report["assisted"] == 1
