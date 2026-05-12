"""Tests for maida.assertions: policy checks, exit codes, report formatting."""

import json

from maida.assertions import (
    AssertionPolicy,
    format_report_json,
    format_report_markdown,
    format_report_text,
    run_assertions,
)
from maida.baseline import create_baseline
from maida.config import load_config
from maida.events import EventType, new_event
from maida.storage import append_event, create_run, finalize_run


def _make_run(config, *, name="test_run", events=None, status="ok"):
    """Helper: create a run, append events, finalize, return run_id."""
    run = create_run(name, config)
    run_id = run["run_id"]
    counts = {"llm_calls": 0, "tool_calls": 0, "errors": 0, "loop_warnings": 0}
    for ev_type, ev_name, payload in events or []:
        ev = new_event(ev_type, run_id, ev_name, payload)
        append_event(run_id, ev, config)
        if ev_type == EventType.TOOL_CALL:
            counts["tool_calls"] += 1
        elif ev_type == EventType.LLM_CALL:
            counts["llm_calls"] += 1
        elif ev_type == EventType.ERROR:
            counts["errors"] += 1
        elif ev_type == EventType.LOOP_WARNING:
            counts["loop_warnings"] += 1
    finalize_run(run_id, status, counts, config)
    return run_id


# ---------------------------------------------------------------------------
# Standalone threshold checks
# ---------------------------------------------------------------------------


def test_max_steps_passes_at_n(temp_data_dir):
    """max_steps=N should pass when total_events == N."""
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(5)]
    run_id = _make_run(config, events=events)

    policy = AssertionPolicy(max_steps=5)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True


def test_max_steps_fails_at_n_plus_one(temp_data_dir):
    """max_steps=N should fail when total_events == N+1."""
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(6)]
    run_id = _make_run(config, events=events)

    policy = AssertionPolicy(max_steps=5)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False
    assert any(r.check_name == "step_count" and not r.passed for r in report.results)


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
    """Baseline of 10 events with 50% tolerance should pass at 15."""
    config = load_config()
    baseline_events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(10)]
    baseline_rid = _make_run(config, events=baseline_events, name="baseline")
    bl = create_baseline(baseline_rid, config)

    run_events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(15)]
    run_id = _make_run(config, events=run_events, name="check")

    policy = AssertionPolicy(max_steps=100)
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    step_result = next(r for r in report.results if r.check_name == "step_count")
    assert step_result.passed is True


def test_step_tolerance_fails_above_50_percent(temp_data_dir):
    """Baseline of 10 events with 50% tolerance should fail at 16."""
    config = load_config()
    baseline_events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(10)]
    baseline_rid = _make_run(config, events=baseline_events, name="baseline")
    bl = create_baseline(baseline_rid, config)

    run_events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(16)]
    run_id = _make_run(config, events=run_events, name="check")

    policy = AssertionPolicy(max_steps=100)
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    step_result = next(r for r in report.results if r.check_name == "step_count")
    assert step_result.passed is False


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

    # Passing a baseline auto-enables a duration sub-check (default tolerance
    # 50%). On fast CI runners the baseline duration can be 1-2 ms, so the
    # implicit limit collapses to ~1.5 ms and trivial jitter on the check run
    # flakes the report. This test asserts no_new_tools semantics only, so
    # neutralize the duration sub-check with a huge tolerance.
    policy = AssertionPolicy(no_new_tools=True, duration_tolerance=10000.0)
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    assert report.passed is True


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

    policy = AssertionPolicy(no_new_tools=True)
    report = run_assertions(run_id, policy, baseline=bl, config=config)
    assert report.passed is False
    tool_result = next(r for r in report.results if r.check_name == "new_tools")
    assert "salesforce_api" in tool_result.message


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
    events = [(EventType.LOOP_WARNING, "loop", {"pattern": "A->B"})]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(no_loops=True)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False


# ---------------------------------------------------------------------------
# no_guardrails
# ---------------------------------------------------------------------------


def test_no_guardrails_passes_when_clean(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(no_guardrails=True)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True


def test_no_guardrails_fails_when_guardrail_error(temp_data_dir):
    config = load_config()
    events = [
        (
            EventType.ERROR,
            "guardrail",
            {"guardrail": "max_llm_calls", "message": "exceeded"},
        ),
    ]
    run_id = _make_run(config, events=events, status="error")
    policy = AssertionPolicy(no_guardrails=True)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False


# ---------------------------------------------------------------------------
# Cost tokens
# ---------------------------------------------------------------------------


def test_max_cost_tokens_passes(temp_data_dir):
    config = load_config()
    events = [
        (EventType.LLM_CALL, "gpt-4", {"usage": {"total_tokens": 100}}),
    ]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_cost_tokens=100)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True


def test_max_cost_tokens_fails(temp_data_dir):
    config = load_config()
    events = [
        (EventType.LLM_CALL, "gpt-4", {"usage": {"total_tokens": 101}}),
    ]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_cost_tokens=100)
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False


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


# ---------------------------------------------------------------------------
# Multi-check aggregation
# ---------------------------------------------------------------------------


def test_mixed_pass_fail_reports_correctly(temp_data_dir):
    config = load_config()
    events = [
        (EventType.TOOL_CALL, "search", {}),
        (EventType.LOOP_WARNING, "loop", {"pattern": "A->B"}),
    ]
    run_id = _make_run(config, events=events)

    policy = AssertionPolicy(
        max_tool_calls=10,
        no_loops=True,
    )
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is False
    passed_checks = [r for r in report.results if r.passed]
    failed_checks = [r for r in report.results if not r.passed]
    assert len(passed_checks) >= 1
    assert len(failed_checks) >= 1


def test_all_checks_disabled_passes(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy()
    report = run_assertions(run_id, policy, config=config)
    assert report.passed is True
    assert len(report.results) == 0


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


def test_format_report_json_valid(temp_data_dir):
    config = load_config()
    run_id = _make_run(config, events=[])
    policy = AssertionPolicy(max_steps=100)
    report = run_assertions(run_id, policy, config=config)
    data = json.loads(format_report_json(report))
    assert data["passed"] is True
    assert "results" in data


def test_format_report_markdown_table(temp_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, "t", {})]
    run_id = _make_run(config, events=events)
    policy = AssertionPolicy(max_steps=100)
    report = run_assertions(run_id, policy, config=config)
    md = format_report_markdown(report)
    assert "Maida Assertion Report" in md
    assert "| Check |" in md
