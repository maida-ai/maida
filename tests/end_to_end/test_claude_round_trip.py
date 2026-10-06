"""Offline claude_code claude_round_trip tests."""

from __future__ import annotations
from tests.support.paths import FIXTURES_ROOT
from maida.assertions import AssertionPolicy, RegressionReasonCode, run_assertions
from maida.baseline import create_baseline
from maida.config import load_config
from maida.integrations.claude_code import (
    load_capture_segment,
    normalize_claude_capture,
)
from maida.storage import install_validated_run


FIXTURES = FIXTURES_ROOT / "traces" / "claude-code" / "2.1.220"

SESSIONS = {
    "normal": "fixture-normal",
    "regression": "fixture-regression",
    "log-only": "fixture-log-only",
    "malformed": "fixture-malformed",
}


def test_capture_to_baseline_to_assertions_round_trip(temp_data_dir):
    config = load_config()
    good = normalize_claude_capture(load_capture_segment(FIXTURES / "normal"), config)
    regression = normalize_claude_capture(load_capture_segment(FIXTURES / "regression"), config)
    install_validated_run(good.meta, good.spans, config)
    install_validated_run(regression.meta, regression.spans, config)
    baseline = create_baseline(good.trace_id, config)

    report = run_assertions(
        regression.trace_id,
        AssertionPolicy(
            no_new_tools=True,
            no_loops=True,
            max_steps=4,
            max_tool_calls=2,
            max_cost_tokens=30,
        ),
        baseline,
        config,
    )
    assert report.passed is False
    assert RegressionReasonCode.NEW_TOOL_PATH in report.reason_codes
    assert RegressionReasonCode.LOOP_DETECTED in report.reason_codes
