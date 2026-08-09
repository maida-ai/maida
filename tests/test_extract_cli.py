"""CLI contract for extracting inactive gate drafts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click import unstyle
from typer.testing import CliRunner

from maida.cli import app
from tests.test_extract import _copy_trace


runner = CliRunner()


def _cli_window(tmp_path: Path) -> Path:
    runs_dir = tmp_path / "native" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="8" * 32,
        run_name="Orders",
        started_at="2026-08-01T00:00:00.000Z",
    )
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="9" * 32,
        run_name="Billing",
        started_at="2026-08-02T00:00:00.000Z",
    )
    return runs_dir


def test_extract_cli_help_documents_required_and_repeatable_options() -> None:
    result = runner.invoke(app, ["extract", "--help"])

    assert result.exit_code == 0, result.output
    help_text = unstyle(result.stdout)
    assert "--window" in help_text
    assert "--out" in help_text
    assert "--workflow" in help_text
    assert "repeat" in help_text.lower()
    assert "--json" in help_text


def test_extract_cli_json_stdout_is_machine_readable_and_notices_use_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir = _cli_window(tmp_path)
    project = tmp_path / "project"
    active = project / ".maida"
    active.mkdir(parents=True)
    policy_before = b"active policy must not change\n"
    (active / "policy.yaml").write_bytes(policy_before)
    monkeypatch.chdir(project)
    out_dir = tmp_path / "draft"

    result = runner.invoke(
        app,
        [
            "extract",
            "--window",
            str(runs_dir),
            "--out",
            str(out_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["draft_version"] == "1.0.0"
    assert [item["run_name"] for item in payload["workflows"]] == [
        "Billing",
        "Orders",
    ]
    assert "Draft written to" in result.stderr
    assert "review" in result.stderr.lower()
    assert "Draft written to" not in result.stdout
    assert (active / "policy.yaml").read_bytes() == policy_before
    assert not (active / "baselines").exists()
    assert not (active / "runs").exists()


def test_extract_cli_repeated_workflows_select_exact_groups(tmp_path: Path) -> None:
    runs_dir = _cli_window(tmp_path)
    out_dir = tmp_path / "draft"

    result = runner.invoke(
        app,
        [
            "extract",
            "--window",
            str(runs_dir),
            "--out",
            str(out_dir),
            "--workflow",
            "Orders",
            "--workflow",
            "Billing",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [item["run_name"] for item in payload["workflows"]] == [
        "Billing",
        "Orders",
    ]


def test_extract_cli_human_summary_is_compact(tmp_path: Path) -> None:
    runs_dir = _cli_window(tmp_path)

    result = runner.invoke(
        app,
        [
            "extract",
            "--window",
            str(runs_dir),
            "--out",
            str(tmp_path / "draft"),
            "--workflow",
            "Billing",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.startswith("Extracted 1 workflow draft")
    assert "Billing -> workflows/" in result.stdout
    assert "review" in result.stderr.lower()


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        (["--workflow", "Missing"], "no traces for workflow"),
        (
            ["--workflow", "Orders", "--workflow", "Orders"],
            "duplicate --workflow",
        ),
    ],
)
def test_extract_cli_invalid_selection_exits_two_without_output(
    tmp_path: Path, extra: list[str], message: str
) -> None:
    runs_dir = _cli_window(tmp_path)
    out_dir = tmp_path / "draft"

    result = runner.invoke(
        app,
        ["extract", "--window", str(runs_dir), "--out", str(out_dir), *extra],
    )

    assert result.exit_code == 2
    assert message in result.stderr
    assert result.stdout == ""
    assert not out_dir.exists()


def test_extract_cli_unexpected_failure_exits_ten_and_keeps_json_stdout_clean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir = _cli_window(tmp_path)

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("persistence failed")

    monkeypatch.setattr("maida.cli.extract_window", fail)
    result = runner.invoke(
        app,
        [
            "extract",
            "--window",
            str(runs_dir),
            "--out",
            str(tmp_path / "draft"),
            "--json",
        ],
    )

    assert result.exit_code == 10
    assert result.stdout == ""
    assert "extraction failed" in result.stderr.lower()
    assert "persistence failed" in result.stderr
