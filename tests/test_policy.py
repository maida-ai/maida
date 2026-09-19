"""Tests for maida.policy: YAML loading and CLI merge."""

import pytest

from maida.assertions import AssertionPolicy
from maida.policy import load_policy, merge_policy


# ---------------------------------------------------------------------------
# load_policy
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("version", ["2", "2.0", "2.1", '"2"', '"2.1"'])
def test_load_policy_valid_yaml(tmp_path, version):
    p = tmp_path / "policy.yaml"
    p.write_text(
        f"version: {version}\nmetrics:\n  no_loops: {{kind: invariant, require: true}}\n"
    )
    policy = load_policy(p)
    assert policy.source_format == "v2"
    assert policy.policy_version[0] == 2
    assert policy.metrics["no_loops"].require is True


@pytest.mark.parametrize("text", ["", "assert: {}\n", "metrics: {}\n", "other: {}\n"])
def test_policy_requires_explicit_version_without_warning(tmp_path, text):
    import warnings

    p = tmp_path / "policy.yaml"
    p.write_text(text)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="version is required.*version: 2"):
            load_policy(p)
    assert not caught


@pytest.mark.parametrize("version", ["1", "1.0", '"1"', "1.1", "0"])
def test_unsupported_old_policy_is_rejected_without_warning(tmp_path, version):
    import warnings

    p = tmp_path / "policy.yaml"
    p.write_text(f"version: {version}\nassert: {{no_loops: true}}\n")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="unsupported.*version: 2"):
            load_policy(p)
    assert not caught


@pytest.mark.parametrize(
    "version, message",
    [
        ("3", "unsupported"),
        ("2.99", "newer Maida"),
        ("2.0.0", "patch versions are invalid"),
        ("true", "policy version"),
    ],
)
def test_invalid_or_future_version_fails_closed(tmp_path, version, message):
    p = tmp_path / "policy.yaml"
    p.write_text(f"version: {version}\nmetrics: {{}}\n")
    with pytest.raises(ValueError, match=message):
        load_policy(p)


def test_v2_rejects_old_fields(tmp_path):
    p = tmp_path / "policy.yaml"
    p.write_text("version: 2\nmetrics: {}\nassert: {no_loops: true}\n")
    with pytest.raises(ValueError, match="unknown field.*assert"):
        load_policy(p)


def test_load_policy_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_policy(tmp_path / "nonexistent.yaml")


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


@pytest.mark.parametrize(
    "kwargs", [{"policy_version": (1, 0)}, {"source_format": "v1"}]
)
def test_programmatic_policy_cannot_select_removed_format(kwargs):
    with pytest.raises(ValueError):
        AssertionPolicy(**kwargs)


@pytest.mark.parametrize(
    "baseline, tools, expected",
    [
        ({"tool_path": ["lookup", "reply"]}, ["reply", "lookup", "lookup"], True),
        ({"tool_path": ["lookup", "reply"]}, ["lookup", "unexpected"], False),
        ({"tool_path": []}, [], True),
        ({"tool_path": []}, ["unexpected"], False),
    ],
)
def test_no_new_tools_checks_every_candidate_tool(tmp_path, baseline, tools, expected):
    from maida.gate import invariant_outcomes

    p = tmp_path / "policy.yaml"
    p.write_text(
        "version: 2\nmetrics:\n  no_new_tools: {kind: invariant, require: true}\n"
    )
    policy = load_policy(p)
    assert (
        invariant_outcomes({"summary": {}, "tool_path": tools}, policy, baseline)[
            "no_new_tools"
        ]
        is expected
    )


@pytest.mark.parametrize(
    "baseline", [None, {}, {"tool_path": None}, {"tool_path": "lookup"}]
)
def test_no_new_tools_requires_an_explicit_baseline_tool_path(tmp_path, baseline):
    from maida.baseline_bind import validate_policy_against_baseline

    p = tmp_path / "policy.yaml"
    p.write_text(
        "version: 2\nmetrics:\n  no_new_tools: {kind: invariant, require: true}\n"
    )
    with pytest.raises(ValueError, match="baseline tool_path"):
        validate_policy_against_baseline(load_policy(p), baseline)


@pytest.mark.parametrize(
    "rule", ["require: false", "none_of: [known_bad]", "all_of: [lookup]"]
)
def test_no_new_tools_rejects_non_enforcing_configuration(tmp_path, rule):
    p = tmp_path / "policy.yaml"
    p.write_text(f"version: 2\nmetrics:\n  no_new_tools: {{kind: invariant, {rule}}}\n")
    with pytest.raises(ValueError):
        load_policy(p)


def test_no_new_tools_is_enforced_in_every_trial(tmp_path):
    from maida.gate import aggregate_metrics, invariant_outcomes
    from maida.statistics import GateVerdict

    path = tmp_path / "policy.yaml"
    path.write_text(
        "version: 2\nmetrics:\n  no_new_tools: {kind: invariant, require: true}\n"
    )
    policy = load_policy(path)
    baseline = {"tool_path": ["lookup"]}
    outcomes = [
        invariant_outcomes({"summary": {}, "tool_path": tools}, policy, baseline)
        for tools in [["lookup"], ["unlisted"], ["lookup"]]
    ]
    results = aggregate_metrics(
        policy=policy,
        trial_values=[{}, {}, {}],
        trial_invariants=outcomes,
        process_outcomes=[True, True, True],
        baseline=baseline,
        trials_budgeted=3,
        stopping_rule="fixed_n",
    )
    assert (
        next(
            result for result in results if result.check_name == "no_new_tools"
        ).verdict
        is GateVerdict.FAIL
    )


def test_no_new_tools_schema_matches_loader(tmp_path):
    import json
    from pathlib import Path
    import jsonschema

    schema = json.loads(
        (Path(__file__).parents[1] / "schemas/policy.schema.json").read_text()
    )
    valid = {
        "version": 2,
        "metrics": {"no_new_tools": {"kind": "invariant", "require": True}},
    }
    jsonschema.validate(valid, schema)
    valid["metrics"]["no_new_tools"]["require"] = False
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(valid, schema)
