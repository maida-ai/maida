"""Local attempt measurement keeps incomplete and assisted attempts visible."""

from tests.support.onboarding import v1_payload as v1_payload
import json
import os
from concurrent.futures import ThreadPoolExecutor
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
    for milestone in ("setup-ready", "own-task-captured", "first-report"):
        command("record", "--milestone", milestone)
    report = json.loads(command("report", "--json").stdout)
    assert report["attempts"] == 1
    assert report["activated"] == 1
    assert report["unassisted"] == 0
    assert report["unknown_assistance"] == 1
    assert not Path(".maida").exists()


def test_attempts_measure_founder_maintenance_separately_and_keep_failures(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    command("start", "--assistance", "none")
    command("record", "--phase", "setup", "--minutes", "4", "--actor", "user")
    for milestone in ("setup-ready", "own-task-captured", "first-report"):
        command("record", "--milestone", milestone)
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
        ["start", "--task-kind", "private task text"],
        ["record", "--minutes", "-1", "--phase", "setup"],
        ["record", "--minutes", "nan", "--phase", "setup"],
        ["record", "--minutes", "2"],
        ["record", "--milestone", "demo-pass"],
        ["record", "--phase", "private phase", "--minutes", "1"],
        ["record", "--milestone", "setup-ready", "--actor", "private name"],
        ["record", "--outcome", "unsupported"],
        ["record", "--outcome", "blocked", "--milestone", "setup-ready"],
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
    for milestone in ("setup-ready", "own-task-captured", "first-report"):
        command("record", "--milestone", milestone)
    report = json.loads(command("report", "--json").stdout)
    assert report["unassisted"] == 0
    assert report["assisted"] == 1


def test_local_verification_waits_for_capture_and_repair_with_release_identity(temp_data_dir, tmp_path, monkeypatch):
    from maida import __version__

    monkeypatch.chdir(tmp_path)
    command("start", "--assistance", "none", "--task-kind", "coding-agent")
    result = runner.invoke(app, ["onboarding", "record", "--milestone", "gate-pass"])
    assert result.exit_code == 2
    assert "gate-configured" in result.output
    command("record", "--milestone", "own-task-captured")
    command("record", "--milestone", "gate-configured")
    command("record", "--milestone", "gate-pass")
    result = runner.invoke(app, ["onboarding", "record", "--milestone", "repair-pass"])
    assert result.exit_code == 2
    assert "regression-caught" in result.output
    command("record", "--milestone", "regression-caught")
    assert json.loads(command("report", "--json").stdout)["activated"] == 0
    command("record", "--milestone", "repair-pass")
    report = json.loads(command("report", "--json").stdout)
    assert report["activated"] == 0
    assert report["local_gate_verified"] == 1
    assert report["engine_versions"] == [__version__]
    assert report["task_kinds"] == {"coding-agent": 1}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda a: a.pop("outcome"),
        lambda a: a.update(events=[{}]),
        lambda a: a.update(started_at="yesterday"),
        lambda a: a.update(started_at="2026-09-28T12:00:00"),
        lambda a: a.update(assistance="secret payload"),
        lambda a: a.update(task_kind="private task text"),
        lambda a: a.update(
            events=[{"at": a["started_at"], "actor": "user", "phase": "setup", "minutes": float("nan")}]
        ),
        lambda a: a.update(events=[{"at": a["started_at"], "actor": "user", "phase": "setup", "minutes": -1}]),
        lambda a: a.update(events=[{"at": a["started_at"], "actor": "user", "milestone": "repair-pass"}]),
    ],
)
def test_invalid_nested_journal_is_rejected_without_rewriting(temp_data_dir, tmp_path, monkeypatch, mutation):
    monkeypatch.chdir(tmp_path)
    command("start")
    journal = next((temp_data_dir / "onboarding").glob("*.json"))
    payload = json.loads(journal.read_text())
    mutation(payload["attempts"][0])
    journal.write_text(json.dumps(payload))
    before = journal.read_bytes()
    for args in (["report", "--json"], ["start"], ["record", "--milestone", "own-task-captured"]):
        result = runner.invoke(app, ["onboarding", *args])
        assert result.exit_code == 2
        assert "journal" in result.output
        assert journal.read_bytes() == before


@pytest.mark.parametrize("version", [1, 3, None])
@pytest.mark.parametrize(
    "args",
    [
        ["report"],
        ["report", "--json"],
        ["start"],
        ["record", "--milestone", "setup-ready"],
        ["record", "--phase", "maintenance", "--minutes", "2"],
    ],
)
def test_unsupported_journals_fail_safely_with_recovery(temp_data_dir, tmp_path, monkeypatch, version, args):
    monkeypatch.chdir(tmp_path)
    command("start")
    path = next((temp_data_dir / "onboarding").glob("*.json"))
    payload = v1_payload()
    payload["journal_version"] = version
    path.write_text(json.dumps(payload))
    before = path.read_bytes()
    result = runner.invoke(app, ["onboarding", *args])
    assert result.exit_code == 2
    assert "measurement format" in result.output
    assert str(path) in result.output
    assert "left unchanged" in result.output
    assert "archive" in result.output
    assert "maida onboarding start" in result.output
    assert path.read_bytes() == before


def test_funnel_times_conversion_and_abandoned_denominator(temp_data_dir, tmp_path, monkeypatch):
    from maida._onboarding.utils import _load, _path
    from maida.onboarding import summarize

    monkeypatch.chdir(tmp_path)
    command("start", "--assistance", "none")
    for stage in ("setup-ready", "own-task-captured", "first-report"):
        command("record", "--milestone", stage)
    command("start", "--assistance", "none")
    command("record", "--milestone", "setup-ready")
    command("start", "--assistance", "founder")
    command("record", "--outcome", "blocked")
    data = _load(_path())
    first = data["attempts"][0]
    first["started_at"] = "2026-10-01T12:00:00+00:00"
    for event, seconds in zip(first["events"], (10, 20, 30)):
        event["at"] = f"2026-10-01T12:00:{seconds:02d}+00:00"
    first["activated_at"] = first["events"][-1]["at"]
    report = summarize(data)
    assert report["attempts"] == 3
    assert report["abandoned"] == report["blocked"] == 1
    assert report["activated"] == 1
    assert report["activation_rate"] == 1 / 3
    assert report["funnel"][0]["percentage_of_attempts"] == pytest.approx(200 / 3)
    assert report["funnel"][1]["conversion_from_previous"] == 0.5
    assert report["funnel"][2]["conversion_from_previous"] == 1
    assert report["time_to_first_report_seconds"] == [30]
    assert report["funnel"][1]["elapsed_seconds"] == [20]
    assert report["setup_without_first_report"] == 1
    assert "Setup without a first report: 1" in command("report").stdout


def test_explicit_milestones_are_idempotent_and_demo_is_optional(temp_data_dir, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    command("start", "--assistance", "none")
    command("record", "--milestone", "demo-seen")
    for stage in ("setup-ready", "own-task-captured", "first-report"):
        command("record", "--milestone", stage)
    before = json.loads(command("report", "--json").stdout)
    command("record", "--milestone", "first-report", "--actor", "founder")
    assert json.loads(command("report", "--json").stdout) == before


def test_concurrent_auto_records_are_idempotent(temp_data_dir, tmp_path, monkeypatch):
    from maida.onboarding import record_automatically

    monkeypatch.chdir(tmp_path)
    command("start")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: record_automatically("setup-ready"), range(30)))
    path = next((temp_data_dir / "onboarding").glob("*.json"))
    assert len(json.loads(path.read_text())["attempts"][0]["events"]) == 1
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_downstream_verification_cannot_be_recorded_automatically(temp_data_dir, tmp_path, monkeypatch):
    from maida.onboarding import record_automatically

    monkeypatch.chdir(tmp_path)
    command("start")
    path = next((temp_data_dir / "onboarding").glob("*.json"))
    before = path.read_bytes()
    with pytest.raises(ValueError, match="mechanically provable"):
        record_automatically("pr-gate-verified")
    assert path.read_bytes() == before
