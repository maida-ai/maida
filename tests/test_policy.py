"""Tests for maida.policy: YAML loading and CLI merge."""

import pytest

from maida.assertions import AssertionPolicy
from maida.policy import load_policy, merge_policy


# ---------------------------------------------------------------------------
# load_policy
# ---------------------------------------------------------------------------


def test_load_policy_valid_yaml(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "assert:\n"
        "  no_loops: true\n"
        "  no_guardrails: true\n"
        "  step_tolerance: 0.3\n"
        "  expect_status: ok\n"
    )
    policy = load_policy(p)
    assert policy.no_loops is True
    assert policy.no_guardrails is True
    assert policy.step_tolerance == 0.3
    assert policy.expect_status == "ok"
    assert policy.max_steps is None


def test_load_policy_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_policy(tmp_path / "nonexistent.yaml")


def test_load_policy_empty_assert_section(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("assert:\n  extra_unknown_key: 42\n")
    policy = load_policy(p)
    assert policy == AssertionPolicy()


def test_load_policy_no_assert_section(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("other:\n  key: value\n")
    policy = load_policy(p)
    assert policy == AssertionPolicy()


def test_load_policy_all_fields(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text(
        "assert:\n"
        "  trials: 7\n"
        "  confidence_level: 0.9\n"
        "  pass_rate_threshold: 0.8\n"
        "  max_steps: 50\n"
        "  step_tolerance: 0.2\n"
        "  max_tool_calls: 20\n"
        "  tool_call_tolerance: 0.3\n"
        "  no_new_tools: true\n"
        "  no_loops: true\n"
        "  no_guardrails: true\n"
        "  max_cost_tokens: 5000\n"
        "  cost_tolerance: 0.4\n"
        "  max_duration_ms: 10000\n"
        "  duration_tolerance: 0.6\n"
        "  expect_status: ok\n"
    )
    policy = load_policy(p)
    assert policy.trials == 7
    assert policy.confidence_level == 0.9
    assert policy.pass_rate_threshold == 0.8
    assert policy.max_steps == 50
    assert policy.step_tolerance == 0.2
    assert policy.max_tool_calls == 20
    assert policy.tool_call_tolerance == 0.3
    assert policy.no_new_tools is True
    assert policy.no_loops is True
    assert policy.no_guardrails is True
    assert policy.max_cost_tokens == 5000
    assert policy.cost_tolerance == 0.4
    assert policy.max_duration_ms == 10000
    assert policy.duration_tolerance == 0.6
    assert policy.expect_status == "ok"


# ---------------------------------------------------------------------------
# merge_policy
# ---------------------------------------------------------------------------


def test_merge_cli_overrides_win(tmp_path):
    file_policy = AssertionPolicy(
        no_loops=True, step_tolerance=0.3, max_steps=50, trials=3
    )
    cli = {
        "max_steps": 100,
        "step_tolerance": None,
        "no_loops": False,
        "trials": 5,
    }
    merged = merge_policy(file_policy, cli)
    assert merged.max_steps == 100
    assert merged.step_tolerance == 0.3
    assert merged.no_loops is True
    assert merged.trials == 5


def test_statistical_policy_defaults_are_cost_conscious():
    policy = AssertionPolicy()

    assert policy.trials == 3
    assert policy.confidence_level == 0.95
    assert policy.pass_rate_threshold == 0.90


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("trials", 0, "trials must be an integer of at least 1"),
        (
            "confidence_level",
            1.0,
            "confidence_level must be greater than 0 and less than 1",
        ),
        ("pass_rate_threshold", -0.1, "pass_rate_threshold must be between 0 and 1"),
        ("step_tolerance", -0.1, "step_tolerance must be a non-negative number"),
    ],
)
def test_invalid_policy_values_fail_with_actionable_field_name(field, value, message):
    with pytest.raises(ValueError, match=message):
        AssertionPolicy(**{field: value})


def test_merge_preserves_file_values_when_cli_none():
    file_policy = AssertionPolicy(max_steps=50, no_loops=True, expect_status="ok")
    cli = {
        "max_steps": None,
        "no_loops": False,
        "expect_status": None,
    }
    merged = merge_policy(file_policy, cli)
    assert merged.max_steps == 50
    assert merged.no_loops is True
    assert merged.expect_status == "ok"


def test_merge_cli_bool_true_overrides():
    file_policy = AssertionPolicy(no_loops=False, no_guardrails=False)
    cli = {"no_loops": True, "no_guardrails": True}
    merged = merge_policy(file_policy, cli)
    assert merged.no_loops is True
    assert merged.no_guardrails is True


def test_merge_cli_string_overrides():
    file_policy = AssertionPolicy(expect_status="ok")
    cli = {"expect_status": "error"}
    merged = merge_policy(file_policy, cli)
    assert merged.expect_status == "error"


def test_merge_ignores_unknown_keys():
    file_policy = AssertionPolicy()
    cli = {"unknown_field": 42, "max_steps": 10}
    merged = merge_policy(file_policy, cli)
    assert merged.max_steps == 10
    assert not hasattr(merged, "unknown_field")


# ---------------------------------------------------------------------------
# ignored_checks
# ---------------------------------------------------------------------------


def test_load_policy_with_ignored_checks(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("assert:\n  ignored_checks:\n    - step_count\n    - cost_tokens\n")
    policy = load_policy(p)
    assert policy.ignored_checks == ["step_count", "cost_tokens"]


def test_load_policy_ignored_checks_null(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("assert:\n  ignored_checks:\n")
    policy = load_policy(p)
    assert policy.ignored_checks == []


def test_load_policy_ignored_checks_empty_list(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("assert:\n  ignored_checks: []\n")
    policy = load_policy(p)
    assert policy.ignored_checks == []


def test_merge_ignored_checks_union_with_cli():
    file_policy = AssertionPolicy(ignored_checks=["step_count", "no_loops"])
    cli = {"ignored_checks": ["cost_tokens"]}
    merged = merge_policy(file_policy, cli)
    assert sorted(merged.ignored_checks) == sorted(
        ["step_count", "no_loops", "cost_tokens"]
    )


def test_merge_ignored_checks_file_only():
    file_policy = AssertionPolicy(ignored_checks=["step_count"])
    cli = {"ignored_checks": None}
    merged = merge_policy(file_policy, cli)
    assert merged.ignored_checks == ["step_count"]


def test_merge_ignored_checks_cli_only():
    file_policy = AssertionPolicy()
    cli = {"ignored_checks": ["duration"]}
    merged = merge_policy(file_policy, cli)
    assert merged.ignored_checks == ["duration"]


def test_merge_ignored_checks_dedup():
    file_policy = AssertionPolicy(ignored_checks=["step_count"])
    cli = {"ignored_checks": ["step_count", "no_loops"]}
    merged = merge_policy(file_policy, cli)
    assert merged.ignored_checks == ["no_loops", "step_count"]
