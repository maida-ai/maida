"""Tests for the reusable stored-run evaluation service."""

import pytest

from maida import record_tool_call, traced_run
from maida.assertions import (
    AssertionPolicy,
    format_report_json,
    format_report_markdown,
    format_report_text,
)
from maida.baseline import create_baseline
from maida.config import load_config
from maida.evaluation import evaluate_stored_run_against_baseline
from tests.conftest import get_latest_run_id


def test_evaluate_stored_run_returns_report_diff_and_formatter_parity(temp_data_dir):
    config = load_config()
    with traced_run(name="baseline"):
        record_tool_call("Read", args={}, result=None)
    baseline_id = get_latest_run_id(config)
    baseline = create_baseline(baseline_id, config)

    with traced_run(name="regression"):
        record_tool_call("Read", args={}, result=None)
        record_tool_call("Bash", args={}, result=None)
    current_id = get_latest_run_id(config)
    policy = AssertionPolicy(no_new_tools=True)

    evaluation = evaluate_stored_run_against_baseline(
        current_id,
        baseline,
        policy,
        config,
    )

    assert evaluation.passed is False
    assert evaluation.report.run_id == current_id
    assert evaluation.diff.run_a_id == current_id
    assert evaluation.render("text") == format_report_text(
        evaluation.report, diff=evaluation.diff
    )
    assert evaluation.render("json") == format_report_json(evaluation.report)
    assert evaluation.render(
        "markdown", baseline_path="baseline.json"
    ) == format_report_markdown(
        evaluation.report,
        diff=evaluation.diff,
        baseline_path="baseline.json",
    )


def test_evaluation_rejects_unknown_output_format(temp_data_dir):
    config = load_config()
    with traced_run(name="same"):
        pass
    trace_id = get_latest_run_id(config)
    evaluation = evaluate_stored_run_against_baseline(
        trace_id,
        create_baseline(trace_id, config),
        AssertionPolicy(),
    )

    with pytest.raises(ValueError, match="text, json, or markdown"):
        evaluation.render("xml")
