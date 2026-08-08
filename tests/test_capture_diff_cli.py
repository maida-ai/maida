"""CLI coverage for capture-backed local behavioral gates."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from maida.baseline import create_baseline, save_baseline
from maida.cli import app
from maida.config import load_config
from maida.integrations.claude_code import (
    ClaudeCaptureImportError,
    import_claude_capture,
)
from maida.storage import RunValidationError


FIXTURES = Path(__file__).parent / "fixtures" / "traces" / "claude-code" / "2.1.220"
SESSIONS = {
    "normal": "fixture-normal",
    "regression": "fixture-regression",
    "malformed": "fixture-malformed",
}


def _install_fixture(name: str, data_dir: Path) -> None:
    session_id = SESSIONS[name]
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    destination = data_dir / "captures" / "claude-code" / session_hash / "0001"
    shutil.copytree(FIXTURES / name, destination)


def _capture_gate_inputs(data_dir: Path) -> tuple[Path, Path]:
    _install_fixture("normal", data_dir)
    _install_fixture("regression", data_dir)
    config = load_config()
    normal = import_claude_capture(SESSIONS["normal"], config)
    baseline_path = data_dir / "baseline.json"
    save_baseline(create_baseline(normal.trace_id, config), baseline_path)
    policy_path = data_dir / "policy.yaml"
    policy_path.write_text(
        "assert:\n"
        "  no_new_tools: true\n"
        "  no_loops: true\n"
        "  max_steps: 4\n"
        "  max_tool_calls: 2\n"
        "  max_cost_tokens: 30\n",
        encoding="utf-8",
    )
    return baseline_path, policy_path


@pytest.mark.parametrize("output_format", ["text", "json", "markdown"])
def test_capture_diff_failure_matches_assert_output(temp_data_dir, output_format: str):
    baseline_path, policy_path = _capture_gate_inputs(temp_data_dir)
    runner = CliRunner()

    capture = runner.invoke(
        app,
        [
            "diff",
            "--capture",
            SESSIONS["regression"],
            "--baseline",
            str(baseline_path),
            "--policy",
            str(policy_path),
            "--format",
            output_format,
        ],
    )
    imported = import_claude_capture(SESSIONS["regression"], load_config())
    asserted = runner.invoke(
        app,
        [
            "assert",
            imported.trace_id,
            "--baseline",
            str(baseline_path),
            "--policy",
            str(policy_path),
            "--format",
            output_format,
        ],
    )

    assert capture.exit_code == asserted.exit_code == 1
    assert capture.stdout == asserted.stdout
    assert "Using Claude Code capture segment: 0001" in capture.stderr
    assert "Imported Claude Code capture" in capture.stderr
    if output_format == "json":
        assert json.loads(capture.stdout)["passed"] is False
    elif output_format == "markdown":
        assert "Maida verdict: fail" in capture.stdout
        assert "Top behavior changes" in capture.stdout
    else:
        assert "RESULT: FAILED" in capture.stdout
        assert "Run comparison:" in capture.stdout


def test_capture_diff_pass_is_zero_and_keeps_notices_off_stdout(temp_data_dir):
    baseline_path, _ = _capture_gate_inputs(temp_data_dir)

    result = CliRunner().invoke(
        app,
        [
            "diff",
            "--capture",
            SESSIONS["normal"],
            "--baseline",
            str(baseline_path),
            "--format",
            "json",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["passed"] is True
    assert "Claude Code capture" not in result.stdout
    assert "Using Claude Code capture segment: 0001" in result.stderr
    assert "already imported" in result.stderr


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (["--capture", "missing"], "--baseline is required with --capture"),
        (
            ["existing-run", "--capture", "missing", "--baseline", "missing.json"],
            "positional run IDs cannot be used with --capture",
        ),
        (
            [
                "--capture",
                "missing",
                "--baseline",
                "missing.json",
                "--format",
                "xml",
            ],
            "--format must be text, json, or markdown",
        ),
    ],
)
def test_capture_diff_rejects_invalid_option_combinations(
    temp_data_dir, arguments: list[str], message: str
):
    result = CliRunner().invoke(app, ["diff", *arguments])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert message in result.stderr


def test_capture_diff_invalid_capture_baseline_and_policy_are_exit_two(temp_data_dir):
    baseline_path, policy_path = _capture_gate_inputs(temp_data_dir)
    runner = CliRunner()

    missing_capture = runner.invoke(
        app,
        [
            "diff",
            "--capture",
            "does-not-exist",
            "--baseline",
            str(baseline_path),
            "--policy",
            str(policy_path),
            "--format",
            "json",
        ],
    )
    assert missing_capture.exit_code == 2
    assert missing_capture.stdout == ""
    assert "Invalid Claude Code capture" in missing_capture.stderr

    malformed_baseline = temp_data_dir / "malformed.json"
    malformed_baseline.write_text("{", encoding="utf-8")
    invalid_baseline = runner.invoke(
        app,
        [
            "diff",
            "--capture",
            SESSIONS["regression"],
            "--baseline",
            str(malformed_baseline),
        ],
    )
    assert invalid_baseline.exit_code == 2
    assert invalid_baseline.stdout == ""
    assert "Invalid baseline file" in invalid_baseline.stderr

    missing_policy = runner.invoke(
        app,
        [
            "diff",
            "--capture",
            SESSIONS["regression"],
            "--baseline",
            str(baseline_path),
            "--policy",
            str(temp_data_dir / "missing-policy.yaml"),
        ],
    )
    assert missing_policy.exit_code == 2
    assert missing_policy.stdout == ""
    assert "Invalid policy file" in missing_policy.stderr

    malformed_policy = temp_data_dir / "malformed-policy.yaml"
    malformed_policy.write_text("assert: [\n", encoding="utf-8")
    invalid_policy = runner.invoke(
        app,
        [
            "diff",
            "--capture",
            SESSIONS["regression"],
            "--baseline",
            str(baseline_path),
            "--policy",
            str(malformed_policy),
        ],
    )
    assert invalid_policy.exit_code == 2
    assert invalid_policy.stdout == ""
    assert "Invalid policy file" in invalid_policy.stderr


def test_capture_diff_malformed_capture_is_exit_two(temp_data_dir):
    baseline_path, _ = _capture_gate_inputs(temp_data_dir)
    _install_fixture("malformed", temp_data_dir)

    result = CliRunner().invoke(
        app,
        [
            "diff",
            "--capture",
            SESSIONS["malformed"],
            "--baseline",
            str(baseline_path),
        ],
    )

    assert result.exit_code == 2
    assert result.stdout == ""
    assert "Invalid Claude Code capture" in result.stderr


def test_capture_diff_ingestion_and_runtime_failures_are_exit_ten(
    monkeypatch, temp_data_dir
):
    baseline_path, _ = _capture_gate_inputs(temp_data_dir)
    runner = CliRunner()

    def fail_import(*args, **kwargs):
        raise ClaudeCaptureImportError("ingestion failed")

    monkeypatch.setattr("maida.cli.import_claude_capture", fail_import)
    ingestion = runner.invoke(
        app,
        [
            "diff",
            "--capture",
            SESSIONS["regression"],
            "--baseline",
            str(baseline_path),
        ],
    )
    assert ingestion.exit_code == 10
    assert ingestion.stdout == ""
    assert "Claude Code import failed: ingestion failed" in ingestion.stderr

    monkeypatch.undo()

    def fail_evaluation(*args, **kwargs):
        raise RunValidationError("a" * 32, "evaluation failed")

    monkeypatch.setattr(
        "maida.cli.evaluate_stored_run_against_baseline", fail_evaluation
    )
    runtime = runner.invoke(
        app,
        [
            "diff",
            "--capture",
            SESSIONS["regression"],
            "--baseline",
            str(baseline_path),
        ],
    )
    assert runtime.exit_code == 10
    assert runtime.stdout == ""
    assert "error: Run validation failed" in runtime.stderr
    assert "evaluation failed" in runtime.stderr


def test_legacy_diff_still_inspects_regressions_with_exit_zero(temp_data_dir):
    baseline_path, _ = _capture_gate_inputs(temp_data_dir)
    regression = import_claude_capture(SESSIONS["regression"], load_config())

    result = CliRunner().invoke(
        app, ["diff", regression.trace_id, "--baseline", str(baseline_path)]
    )

    assert result.exit_code == 0
    assert "Run comparison:" in result.stdout
    assert "Bash" in result.stdout
