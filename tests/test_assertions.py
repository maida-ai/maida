"""Tests for maida.assertions: policy checks, exit codes, report formatting."""

import json
from textwrap import dedent

import pytest

from maida import record_llm_call, record_tool_call, traced_run
from maida.assertions import (
    AssertionPolicy,
    AssertionReport,
    AssertionResult,
    RegressionReasonCode,
    _check_threshold,
    format_report_json,
    format_report_markdown,
    format_report_text,
    run_assertions,
)
from maida.baseline import create_baseline
from maida.config import load_config
from maida.events import EventType
from tests.conftest import get_latest_run_id


def test_regression_reason_code_vocabulary_is_stable():
    assert {code.value for code in RegressionReasonCode} == {
        "no_regression",
        "step_count_exceeded",
        "new_tool_path",
        "tool_call_count_exceeded",
        "loop_detected",
        "cycle_detected",
        "terminal_state_missing",
        "guardrail_event_changed",
        "latency_envelope_exceeded",
        "cost_envelope_exceeded",
    }


def _make_run(config, *, name="test_run", events=None, status="ok"):
    """Helper: create a run via traced_run + recorders, return run_id."""
    if status == "error":
        with pytest.raises(RuntimeError):
            with traced_run(name=name):
                for ev_type, ev_name, payload in events or []:
                    if ev_type == EventType.TOOL_CALL:
                        record_tool_call(
                            ev_name,
                            args=payload.get("args", {}),
                            result=payload.get("result"),
                        )
                    elif ev_type == EventType.LLM_CALL:
                        record_llm_call(
                            ev_name,
                            prompt="p",
                            response="r",
                            usage=payload.get("usage"),
                        )
                    elif ev_type == EventType.ERROR:
                        record_tool_call(
                            ev_name,
                            args={},
                            result=None,
                            status="error",
                            error=ValueError(payload.get("message", "err")),
                        )
                    elif ev_type == EventType.LOOP_WARNING:
                        record_tool_call(ev_name, args={}, result=None)
                raise RuntimeError("simulated error")
    else:
        with traced_run(name=name):
            for ev_type, ev_name, payload in events or []:
                if ev_type == EventType.TOOL_CALL:
                    record_tool_call(
                        ev_name,
                        args=payload.get("args", {}),
                        result=payload.get("result"),
                    )
                elif ev_type == EventType.LLM_CALL:
                    record_llm_call(
                        ev_name,
                        prompt="p",
                        response="r",
                        usage=payload.get("usage"),
                    )
                elif ev_type == EventType.ERROR:
                    record_tool_call(
                        ev_name,
                        args={},
                        result=None,
                        status="error",
                        error=ValueError(payload.get("message", "err")),
                    )
                elif ev_type == EventType.LOOP_WARNING:
                    record_tool_call(ev_name, args={}, result=None)
    return get_latest_run_id(config)


# ---------------------------------------------------------------------------
# Standalone threshold checks
# ---------------------------------------------------------------------------


def test_max_steps_passes_at_n(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(5)]
    run_id = _make_run(config, events=events)

    policy = AssertionPolicy(max_steps=5)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True


def test_max_steps_fails_at_n_plus_one(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(6)]
    run_id = _make_run(config, events=events)

    policy = AssertionPolicy(max_steps=5)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False
    assert any(r.check_name == "step_count" and not r.passed for r in report.results)
    step_result = next(r for r in report.results if r.check_name == "step_count")
    assert step_result.reason_code == RegressionReasonCode.STEP_COUNT_EXCEEDED.value


def test_max_tool_calls_boundary(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(10)]
    run_id = _make_run(config, events=events)

    assert (
        run_assertions(run_id, AssertionPolicy(max_tool_calls=10), config=config).passed
        is True
    )
    assert (
        run_assertions(run_id, AssertionPolicy(max_tool_calls=9), config=config).passed
        is False
    )


# ---------------------------------------------------------------------------
# Baseline + tolerance checks
# ---------------------------------------------------------------------------


def test_step_tolerance_passes_at_50_percent(temp_data_dir):
    config = load_config()
    baseline_events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(10)]
    baseline_rid = _make_run(config, events=baseline_events, name="baseline")
    bl = create_baseline(baseline_rid, config)

    run_events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(15)]
    run_id = _make_run(config, events=run_events, name="check")

    policy = AssertionPolicy(
        max_steps=100,
        step_tolerance=0.5,
        # Large multipliers to avoid flakiness
        duration_tolerance=50.0,
        tool_call_tolerance=50.0,
        cost_tolerance=50.0,
    )
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    step_result = next(r for r in report.results if r.check_name == "step_count")
    assert step_result.passed is True


def test_step_tolerance_fails_above_50_percent(temp_data_dir):
    config = load_config()
    baseline_events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(10)]
    baseline_rid = _make_run(config, events=baseline_events, name="baseline")
    bl = create_baseline(baseline_rid, config)

    run_events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(16)]
    run_id = _make_run(config, events=run_events, name="check")

    policy = AssertionPolicy(
        max_steps=100,
        step_tolerance=0.5,
        # Large multipliers to avoid flakiness
        duration_tolerance=50.0,
        tool_call_tolerance=50.0,
        cost_tolerance=50.0,
    )
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    step_result = next(r for r in report.results if r.check_name == "step_count")
    assert step_result.passed is False


def test_zero_baseline_with_standalone_cap_uses_cap_as_limit():
    result = _check_threshold(
        actual=1,
        baseline_value=0,
        tolerance=0.5,
        standalone_max=10,
        check_name="step_count",
        reason_code=RegressionReasonCode.STEP_COUNT_EXCEEDED,
        unit="steps",
    )

    assert result is not None
    assert result.passed is True
    assert result.expected == "10"


def test_zero_baseline_without_standalone_cap_allows_no_growth():
    result = _check_threshold(
        actual=1,
        baseline_value=0,
        tolerance=0.5,
        standalone_max=None,
        check_name="step_count",
        reason_code=RegressionReasonCode.STEP_COUNT_EXCEEDED,
        unit="steps",
    )

    assert result is not None
    assert result.passed is False
    assert result.expected == "0"


# ---------------------------------------------------------------------------
# no_new_tools
# ---------------------------------------------------------------------------


def test_no_new_tools_passes_when_subset(temp_data_dir):
    config = load_config()
    bl_events = [
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "parse", {}),
    ]
    bl_rid = _make_run(config, events=bl_events, name="baseline")
    bl = create_baseline(bl_rid, config)

    run_events = [(EventType.TOOL_CALL, "search", {})]
    run_id = _make_run(config, events=run_events, name="check")

    policy = AssertionPolicy(
        no_new_tools=True,
        # Large multipliers to avoid flakiness
        step_tolerance=50.0,
        duration_tolerance=50.0,
        tool_call_tolerance=50.0,
        cost_tolerance=50.0,
    )
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    assert report.passed is True


def test_run_assertions_carries_baseline_acceptance_to_report(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[], name="accepted")
    baseline = create_baseline(run_id, config)
    baseline["acceptance"] = {
        "accepted_by": "reviewer-login",
        "accepted_at": "2026-07-22T20:15:00.000Z",
    }

    report = run_assertions(
        run_id,
        AssertionPolicy(max_steps=10),
        baseline=baseline,
        config=config,
    )

    assert report.baseline_acceptance == baseline["acceptance"]


def test_no_new_tools_fails_on_new_tool(temp_data_dir):
    config = load_config()
    bl_events = [(EventType.TOOL_CALL, "search", {})]
    bl_rid = _make_run(config, events=bl_events, name="baseline")
    bl = create_baseline(bl_rid, config)

    run_events = [
        (EventType.TOOL_CALL, "search", {}),
        (EventType.TOOL_CALL, "salesforce_api", {}),
    ]
    run_id = _make_run(config, events=run_events, name="check")

    policy = AssertionPolicy(
        no_new_tools=True,
        # Large multipliers to avoid flakiness
        step_tolerance=50.0,
        duration_tolerance=50.0,
        tool_call_tolerance=50.0,
        cost_tolerance=50.0,
    )
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    assert report.passed is False
    tool_result = next(r for r in report.results if r.check_name == "new_tools")
    assert "salesforce_api" in tool_result.message
    assert tool_result.reason_code == RegressionReasonCode.NEW_TOOL_PATH.value


# ---------------------------------------------------------------------------
# no_loops
# ---------------------------------------------------------------------------


def test_no_loops_passes_when_none(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(no_loops=True)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True


def test_no_loops_fails_when_present(temp_data_dir):
    config = load_config()
    events = [
        (EventType.TOOL_CALL, "t", {}),
        (EventType.LLM_CALL, "m", {}),
        (EventType.TOOL_CALL, "t", {}),
        (EventType.LLM_CALL, "m", {}),
        (EventType.TOOL_CALL, "t", {}),
        (EventType.LLM_CALL, "m", {}),
    ]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(no_loops=True)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False
    loop_result = next(r for r in report.results if r.check_name == "no_loops")
    assert loop_result.actual == "1"
    assert "cycle x3" in loop_result.message
    assert "TOOL_CALL:t args:{} -> LLM_CALL:m" in loop_result.message
    assert loop_result.reason_code == RegressionReasonCode.CYCLE_DETECTED.value


def test_no_loops_repeated_call_keeps_loop_reason_code(temp_data_dir):
    config = load_config()
    events = [
        (
            EventType.TOOL_CALL,
            "poll_status",
            {"args": {"request_id": f"req-{i}"}, "result": {"ok": True}},
        )
        for i in range(3)
    ]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(no_loops=True)
    report = run_assertions(run_id, policy, config=config)

    assert report.passed is False
    loop_result = next(r for r in report.results if r.check_name == "no_loops")
    assert loop_result.actual == "1"
    assert "repeated_call x3" in loop_result.message
    assert "TOOL_CALL:poll_status args:{request_id:str}" in loop_result.message
    assert loop_result.reason_code == RegressionReasonCode.LOOP_DETECTED.value


# ---------------------------------------------------------------------------
# no_guardrails
# ---------------------------------------------------------------------------


def test_no_guardrails_passes_when_clean(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(no_guardrails=True)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True


# ---------------------------------------------------------------------------
# Cost tokens
# ---------------------------------------------------------------------------


def test_max_cost_tokens_passes(temp_data_dir):
    config = load_config()
    events = [
        (
            EventType.LLM_CALL,
            "gpt-4",
            {
                "usage": {
                    "prompt_tokens": 50,
                    "completion_tokens": 50,
                    "total_tokens": 100,
                }
            },
        ),
    ]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_cost_tokens=100)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True


def test_max_cost_tokens_fails(temp_data_dir):
    config = load_config()
    events = [
        (
            EventType.LLM_CALL,
            "gpt-4",
            {
                "usage": {
                    "prompt_tokens": 51,
                    "completion_tokens": 50,
                    "total_tokens": 101,
                }
            },
        ),
    ]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_cost_tokens=100)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False
    cost_result = next(r for r in report.results if r.check_name == "cost_tokens")
    assert cost_result.reason_code == RegressionReasonCode.COST_ENVELOPE_EXCEEDED.value


# ---------------------------------------------------------------------------
# Duration
# ---------------------------------------------------------------------------


def test_max_duration_standalone(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(max_duration_ms=999999)
    report = run_assertions(run_id, policy, config=config)
    dur_result = next(r for r in report.results if r.check_name == "duration")
    assert dur_result.passed is True


# ---------------------------------------------------------------------------
# expect_status
# ---------------------------------------------------------------------------


def test_expect_status_matches(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[], status="ok")
    policy = AssertionPolicy(expect_status="ok")
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True


def test_expect_status_mismatch(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[], status="error")
    policy = AssertionPolicy(expect_status="ok")
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False
    status_result = next(r for r in report.results if r.check_name == "expect_status")
    assert (
        status_result.reason_code == RegressionReasonCode.TERMINAL_STATE_MISSING.value
    )


# ---------------------------------------------------------------------------
# Multi-check aggregation
# ---------------------------------------------------------------------------


def test_mixed_pass_fail_reports_correctly(temp_data_dir):
    config = load_config()
    events = [
        (EventType.TOOL_CALL, "search", {}),
    ]
    run_id = _make_run(config, events=events)

    policy = AssertionPolicy(
        max_tool_calls=10,
        no_loops=True,
    )
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True
    passed_checks = [r for r in report.results if r.passed]
    assert len(passed_checks) >= 1


def test_all_checks_disabled_passes(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy()
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True
    assert len(report.results) == 0


# ---------------------------------------------------------------------------
# Ignored checks
# ---------------------------------------------------------------------------


def test_ignored_step_count_produces_ignored_result(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(100)]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_steps=1, ignored_checks=["step_count"])
    report = run_assertions(run_id, policy, config=config)
    step = next(r for r in report.results if r.check_name == "step_count")
    assert step.ignored is True
    assert step.passed is True
    assert report.passed is True


def test_ignored_tool_calls_produces_ignored_result(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(100)]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_tool_calls=1, ignored_checks=["tool_calls"])
    report = run_assertions(run_id, policy, config=config)
    result = next(r for r in report.results if r.check_name == "tool_calls")
    assert result.ignored is True


def test_ignored_cost_tokens_produces_ignored_result(temp_data_dir):
    config = load_config()
    events = [
        (
            EventType.LLM_CALL,
            "gpt-4",
            {
                "usage": {
                    "prompt_tokens": 9999,
                    "completion_tokens": 9999,
                    "total_tokens": 19998,
                }
            },
        )
    ]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_cost_tokens=1, ignored_checks=["cost_tokens"])
    report = run_assertions(run_id, policy, config=config)
    result = next(r for r in report.results if r.check_name == "cost_tokens")
    assert result.ignored is True


def test_ignored_duration_produces_ignored_result(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(max_duration_ms=1, ignored_checks=["duration"])
    report = run_assertions(run_id, policy, config=config)
    result = next(r for r in report.results if r.check_name == "duration")
    assert result.ignored is True


def test_ignored_new_tools_produces_ignored_result(temp_data_dir):
    config = load_config()
    bl_events = [(EventType.TOOL_CALL, "existing_tool", {})]
    bl_rid = _make_run(config, events=bl_events, name="baseline")
    bl = create_baseline(bl_rid, config)
    run_events = [(EventType.TOOL_CALL, "new_tool", {})]
    run_id = _make_run(config, events=run_events, name="check")
    policy = AssertionPolicy(no_new_tools=True, ignored_checks=["new_tools"])
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    result = next(r for r in report.results if r.check_name == "new_tools")
    assert result.ignored is True


def test_ignored_no_loops_produces_ignored_result(temp_data_dir):
    config = load_config()
    events = [
        (EventType.TOOL_CALL, "t", {}),
        (EventType.LLM_CALL, "m", {}),
        (EventType.TOOL_CALL, "t", {}),
        (EventType.LLM_CALL, "m", {}),
        (EventType.TOOL_CALL, "t", {}),
        (EventType.LLM_CALL, "m", {}),
    ]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(no_loops=True, ignored_checks=["no_loops"])
    report = run_assertions(run_id, policy, config=config)
    result = next(r for r in report.results if r.check_name == "no_loops")
    assert result.ignored is True


def test_ignored_no_guardrails_produces_ignored_result(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(no_guardrails=True, ignored_checks=["no_guardrails"])
    report = run_assertions(run_id, policy, config=config)
    result = next(r for r in report.results if r.check_name == "no_guardrails")
    assert result.ignored is True


def test_ignored_expect_status_produces_ignored_result(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[], status="error")
    policy = AssertionPolicy(expect_status="ok", ignored_checks=["expect_status"])
    report = run_assertions(run_id, policy, config=config)
    result = next(r for r in report.results if r.check_name == "expect_status")
    assert result.ignored is True


def test_unknown_ignored_check_raises_error(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(ignored_checks=["step_counts"])
    with pytest.raises(ValueError, match="Unknown check name"):
        run_assertions(run_id, policy, config=config)


def test_ignored_check_reports_passed(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(100)]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(
        max_steps=1, max_tool_calls=1, ignored_checks=["step_count", "tool_calls"]
    )
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True
    for r in report.results:
        assert r.ignored is True


def test_ignored_checks_in_report_text(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(max_steps=100, ignored_checks=["step_count"])
    report = run_assertions(run_id, policy, config=config)
    text = format_report_text(report)
    assert "[ignored]" in text
    assert "step_count" in text
    assert "1 ignored" in text


def test_ignored_checks_in_report_json(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(max_steps=100, ignored_checks=["step_count"])
    report = run_assertions(run_id, policy, config=config)
    data = json.loads(format_report_json(report))
    step = next(r for r in data["results"] if r["check_name"] == "step_count")
    assert step["ignored"] is True
    assert step["passed"] is True


def test_ignored_checks_in_report_markdown(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(max_steps=100, ignored_checks=["step_count"])
    report = run_assertions(run_id, policy, config=config)
    md = format_report_markdown(report)
    assert "ignored checks" in md.lower()
    assert "step_count" in md


# ---------------------------------------------------------------------------
# Report formatters
# ---------------------------------------------------------------------------


def test_format_report_text_contains_verdict(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(max_steps=100)
    report = run_assertions(run_id, policy, config=config)
    text = format_report_text(report)
    assert "PASSED" in text


def test_format_report_text_includes_reason_code(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(5)]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_steps=2, no_loops=True)
    report = run_assertions(run_id, policy, config=config)

    text = format_report_text(report)

    assert "[step_count_exceeded]" in text
    assert "[no_regression]" in text


def test_format_report_json_valid(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(max_steps=100)
    report = run_assertions(run_id, policy, config=config)
    data = json.loads(format_report_json(report))
    assert data["passed"] is True
    assert "results" in data
    assert data["results"][0]["reason_code"] == RegressionReasonCode.NO_REGRESSION.value


def test_format_report_json_emits_stable_reason_codes(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(3)]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_steps=1)
    report = run_assertions(run_id, policy, config=config)

    data = json.loads(format_report_json(report))

    assert data["reason_codes"] == [RegressionReasonCode.STEP_COUNT_EXCEEDED.value]
    assert data["results"][0]["reason_code"] == (
        RegressionReasonCode.STEP_COUNT_EXCEEDED.value
    )


def test_format_report_markdown_pass_layout(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, "t", {})]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_steps=100)
    report = run_assertions(run_id, policy, config=config)
    md = format_report_markdown(report)
    assert "## ✅ Maida verdict: pass" in md
    assert "<details>" in md  # passing checks are collapsed
    assert "passing checks" in md
    assert "`no_regression`:" not in md
    assert "### Next steps" in md
    assert "Reproduce locally" in md
    assert "maida.ai" in md


def test_format_report_markdown_failed_checks_first(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(5)]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_steps=2, no_loops=True)
    report = run_assertions(run_id, policy, config=config)
    md = format_report_markdown(report)
    assert "## ❌ Maida verdict: fail" in md
    assert "1 of 2 checks failed" in md
    # failed table with expected/actual comes before the collapsed passing block
    assert md.index("#### `step_count_exceeded`") < md.index("<details>")
    assert "### Failed checks by reason code" in md
    assert "| Check | Expected | Actual | Details |" in md
    assert "| ❌ `step_count` |" in md
    assert "✅ `no_loops`" in md
    assert "### Next steps" in md


def test_format_report_markdown_groups_failures_by_reason_code(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(5)]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_steps=2, max_tool_calls=2)
    report = run_assertions(run_id, policy, config=config)

    md = format_report_markdown(report)

    assert "#### `step_count_exceeded`" in md
    assert "#### `tool_call_count_exceeded`" in md
    assert md.index("#### `step_count_exceeded`") < md.index(
        "#### `tool_call_count_exceeded`"
    )


def test_format_report_markdown_includes_diff_section(temp_data_dir):
    from maida.diff import compute_diff

    config = load_config()
    bl_run = _make_run(config, name="bl", events=[(EventType.TOOL_CALL, "t", {})])
    baseline = create_baseline(bl_run, config)

    run_id = _make_run(
        config,
        name="current",
        events=[
            (EventType.TOOL_CALL, "t", {}),
            (EventType.TOOL_CALL, "new_tool", {}),
            (EventType.TOOL_CALL, "new_tool", {}),
        ],
    )
    policy = AssertionPolicy(
        no_new_tools=True,
        # Large multipliers to avoid flakiness
        step_tolerance=50.0,
        duration_tolerance=50.0,
        tool_call_tolerance=50.0,
        cost_tolerance=50.0,
    )
    report = run_assertions(run_id, policy, baseline=baseline, config=config)
    assert not report.passed

    diff = compute_diff(run_id, baseline=baseline, config=config)
    md = format_report_markdown(report, diff=diff, baseline_path="bl.json")
    assert "### Top behavior changes" in md
    assert "`new_tool`" in md
    assert "maida diff" in md
    assert "--baseline bl.json" in md
    assert "maida accept" in md
    assert '--reason "..."' in md
    assert "Review and commit the baseline diff" in md


def test_format_report_markdown_no_diff_section_without_diff(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[(EventType.TOOL_CALL, "t", {})])
    policy = AssertionPolicy(max_steps=100)
    report = run_assertions(run_id, policy, config=config)
    md = format_report_markdown(report)
    assert "Top behavior changes" not in md


def test_format_report_markdown_renders_baseline_provenance():
    report = AssertionReport(
        run_id="a" * 32,
        baseline_run_id="b" * 32,
        baseline_acceptance={
            "accepted_at": "2026-07-22T20:15:00.000Z",
            "accepted_by": "reviewer-login",
            "reason": "Expected retrieval | tool split",
            "source": {
                "repository": "maida-ai/example-agent",
                "pull_request": {
                    "number": 42,
                    "url": "https://github.com/maida-ai/example-agent/pull/42",
                },
                "commit_sha": "abcdef1234567890",
            },
            "verdict": {
                "outcome": "accepted",
                "summary": "Accepted run status ok: 4 events, 2 tool calls.",
            },
        },
    )
    report.add(
        AssertionResult(
            check_name="step_count",
            passed=True,
            message="4 steps (baseline: 4, tolerance: 50%)",
        )
    )

    md = format_report_markdown(report, baseline_path="baseline.json")

    assert "### Baseline provenance" in md
    assert "`reviewer-login`" in md
    assert "2026-07-22T20:15:00.000Z" in md
    assert "[PR #42](https://github.com/maida-ai/example-agent/pull/42)" in md
    assert "`abcdef12`" in md
    assert "Accepted run status ok: 4 events, 2 tool calls." in md
    assert "Expected retrieval \\| tool split" in md


def test_format_report_markdown_renders_legacy_acceptance_safely():
    report = AssertionReport(
        run_id="a" * 32,
        baseline_run_id="b" * 32,
        baseline_acceptance={
            "accepted_at": "2026-07-09T12:00:00.000Z",
            "reason": "Legacy local acceptance",
        },
    )

    md = format_report_markdown(report, baseline_path="baseline.json")

    assert "### Baseline provenance" in md
    assert "`unknown`" in md
    assert "local acceptance" in md
    assert "**Acceptance verdict:** accepted — not recorded" in md
    assert "**Reason:** Legacy local acceptance" in md


def test_format_report_markdown_pass_snapshot():
    report = AssertionReport(run_id="a" * 32, baseline_run_id=None)
    report.add(
        AssertionResult(
            check_name="step_count",
            passed=True,
            message="8 steps (max: 12)",
            expected="12",
            actual="8",
        )
    )

    md = format_report_markdown(report)

    assert (
        md
        == dedent(
            """\
        ## ✅ Maida verdict: pass

        **All 1 checks passed** · run `aaaaaaaa`

        <details>
        <summary>✅ 1 passing checks</summary>

        | Check | Details |
        |---|---|
        | ✅ `step_count` | 8 steps (max: 12) |

        </details>

        ### Next steps

        - No gate action needed; inspect the trace with `maida view aaaaaaaa` if desired.

        <details>
        <summary>Reproduce locally</summary>

        ```bash
        pip install maida-ai
        maida view aaaaaaaa
        ```

        </details>

        ---
        *Gated by [Maida](https://maida.ai) — the local-first behavioral regression gate for AI agents.*
        """
        ).strip()
    )


def test_format_report_markdown_failure_behavior_diff_snapshot():
    from maida.diff import RunDiff

    report = AssertionReport(run_id="a" * 32, baseline_run_id="b" * 32)
    report.add(
        AssertionResult(
            check_name="step_count",
            passed=False,
            message="18 steps (baseline: 8, tolerance: 50%)",
            reason_code=RegressionReasonCode.STEP_COUNT_EXCEEDED,
            expected="12",
            actual="18",
        )
    )
    report.add(
        AssertionResult(
            check_name="no_loops",
            passed=False,
            message="2 loop warning(s) detected: cycle x2 search -> summarize",
            reason_code=RegressionReasonCode.CYCLE_DETECTED,
            actual="2",
        )
    )
    report.add(
        AssertionResult(
            check_name="cost_tokens",
            passed=True,
            message="450 tokens (baseline: 100, tolerance: 400%)",
        )
    )
    diff = RunDiff(
        run_a_id="a" * 32,
        run_b_id="b" * 32,
        summary_diff={
            "total_events": (18, 8),
            "loop_warnings": (2, 0),
            "status": ("error", "ok"),
            "duration_ms": (2500, 1000),
            "total_tokens": (450, 100),
        },
        tool_path_diff={
            "new": ["web_search"],
            "removed": [],
            "repeated": {"web_search": (0, 2)},
            "reordered": True,
            "current_sequence_exact": True,
            "baseline_sequence_exact": True,
        },
        new_tools=["web_search"],
        repeated_tools={"web_search": (0, 2)},
        reordered_tools=True,
        current_tool_sequence=["search", "web_search", "web_search", "summarize"],
        baseline_tool_sequence=["search", "summarize"],
        model_changes={"added": ["gpt-4.1-mini"], "removed": ["gpt-4.1"]},
        guardrail_event_diff=(1, 0),
        terminal_status_diff=("error", "ok"),
    )

    md = format_report_markdown(report, diff=diff, baseline_path="baseline.json")

    assert (
        md
        == dedent(
            """\
        ## ❌ Maida verdict: fail

        **2 of 3 checks failed** · run `aaaaaaaa` vs baseline `bbbbbbbb`

        ### Top behavior changes

        | Behavior | Baseline | Current | Change |
        |---|---|---|---|
        | Steps | 8 | 18 | +125% |
        | Tool path | search -> summarize | search -> web_search -> web_search -> summarize | 1 new; repeated calls; order changed |
        | Loops/cycles | 0 | 2 | NEW |
        | Guardrail events | 0 | 1 | NEW |
        | Terminal state | ok | error | changed |
        | Latency envelope | 1000 ms | 2500 ms | +150% |
        | Cost envelope | 100 tokens | 450 tokens | +350% |
        | Models | gpt-4.1 | gpt-4.1-mini | 1 added; 1 removed |

        **Tool path:**
        - Baseline: `search -> summarize`
        - Current: `search -> web_search -> web_search -> summarize`

        **Tool changes:**
        - ➕ `web_search` — new tool, not in baseline
        - 🔁 `web_search` — repeated 0 -> 2 calls
        - 🔀 Tool order changed for shared calls

        **Model changes:**
        - ➕ `gpt-4.1-mini`
        - ➖ `gpt-4.1`

        ### Failed checks by reason code

        #### `step_count_exceeded`

        | Check | Expected | Actual | Details |
        |---|---|---|---|
        | ❌ `step_count` | 12 | 18 | 18 steps (baseline: 8, tolerance: 50%) |

        #### `cycle_detected`

        | Check | Expected | Actual | Details |
        |---|---|---|---|
        | ❌ `no_loops` | — | 2 | 2 loop warning(s) detected: cycle x2 search -> summarize |

        <details>
        <summary>✅ 1 passing checks</summary>

        | Check | Details |
        |---|---|
        | ✅ `cost_tokens` | 450 tokens (baseline: 100, tolerance: 400%) |

        </details>

        ### Next steps

        - Inspect the full diff: `maida diff aaaaaaaa --baseline baseline.json`
        - Open the trace locally: `maida view aaaaaaaa`
        - If this behavior change is intentional, accept it explicitly: `maida accept aaaaaaaa --baseline baseline.json --reason "..."`
        - Review and commit the baseline diff; otherwise fix the agent behavior and rerun the gate.

        <details>
        <summary>Reproduce locally</summary>

        ```bash
        pip install maida-ai
        maida diff aaaaaaaa --baseline baseline.json
        maida view aaaaaaaa
        ```

        </details>

        ---
        *Gated by [Maida](https://maida.ai) — the local-first behavioral regression gate for AI agents.*
        """
        ).strip()
    )


def test_format_report_text_appends_diff_on_failure(temp_data_dir):
    from maida.diff import compute_diff

    config = load_config()
    bl_run = _make_run(config, name="bl", events=[(EventType.TOOL_CALL, "t", {})])
    baseline = create_baseline(bl_run, config)

    run_id = _make_run(
        config,
        name="current",
        events=[
            (EventType.TOOL_CALL, "t", {}),
            (EventType.TOOL_CALL, "new_tool", {}),
        ],
    )
    policy = AssertionPolicy(
        no_new_tools=True,
        # Large multipliers to avoid flakiness
        step_tolerance=50.0,
        duration_tolerance=50.0,
        tool_call_tolerance=50.0,
        cost_tolerance=50.0,
    )
    report = run_assertions(run_id, policy, baseline=baseline, config=config)
    diff = compute_diff(run_id, baseline=baseline, config=config)

    text = format_report_text(report, diff=diff)
    assert "FAILED" in text
    assert "Run comparison:" in text


def test_format_report_text_omits_diff_on_pass(temp_data_dir):
    from maida.diff import compute_diff

    config = load_config()
    bl_run = _make_run(config, name="bl", events=[(EventType.TOOL_CALL, "t", {})])
    baseline = create_baseline(bl_run, config)
    run_id = _make_run(config, name="current", events=[(EventType.TOOL_CALL, "t", {})])

    policy = AssertionPolicy(
        no_new_tools=True,
        # Large multipliers to avoid flakiness
        step_tolerance=50.0,
        duration_tolerance=50.0,
        tool_call_tolerance=50.0,
        cost_tolerance=50.0,
    )
    report = run_assertions(run_id, policy, baseline=baseline, config=config)
    assert report.passed
    diff = compute_diff(run_id, baseline=baseline, config=config)

    text = format_report_text(report, diff=diff)
    assert "Run comparison:" not in text
