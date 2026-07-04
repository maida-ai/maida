"""YAML policy file loading and merging with CLI overrides.

A policy file (``.maida/policy.yaml``) lets teams check assertion
thresholds into version control.  CLI flags always take precedence.
"""

from dataclasses import fields
from pathlib import Path

from maida.assertions import AssertionPolicy

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


def load_policy(path: Path) -> AssertionPolicy:
    """Load an ``AssertionPolicy`` from a YAML file.

    Expected structure::

        assert:
          step_tolerance: 0.5
          no_loops: true
          ...

    Raises ``FileNotFoundError`` if *path* does not exist, or
    ``RuntimeError`` if PyYAML is not installed.
    """
    if yaml is None:
        raise RuntimeError("PyYAML is required to load policy files")
    if not path.is_file():
        raise FileNotFoundError(f"Policy file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        return AssertionPolicy()
    section = data.get("assert")
    if not isinstance(section, dict):
        return AssertionPolicy()
    return _policy_from_dict(section)


def _policy_from_dict(data: dict) -> AssertionPolicy:
    """Build an ``AssertionPolicy`` from a flat dict, ignoring unknown keys."""
    kwargs: dict = {}
    field_names = {f.name for f in fields(AssertionPolicy)}
    for key, value in data.items():
        if key in field_names:
            kwargs[key] = value
    return AssertionPolicy(**kwargs)


def merge_policy(
    file_policy: AssertionPolicy,
    cli_overrides: dict,
) -> AssertionPolicy:
    """Merge *file_policy* with *cli_overrides*.  CLI values win when present.

    *cli_overrides* maps field names to their CLI-provided values.  A value of
    ``None`` (or absent key) means "not specified on CLI — keep file value".
    For bool flags the CLI sends ``False`` as default, so we only override when
    the CLI explicitly sets ``True``.
    """
    merged = AssertionPolicy(
        max_steps=file_policy.max_steps,
        min_steps=file_policy.min_steps,
        step_tolerance=file_policy.step_tolerance,
        max_tool_calls=file_policy.max_tool_calls,
        min_tool_calls=file_policy.min_tool_calls,
        tool_call_tolerance=file_policy.tool_call_tolerance,
        no_new_tools=file_policy.no_new_tools,
        no_loops=file_policy.no_loops,
        no_guardrails=file_policy.no_guardrails,
        max_cost_tokens=file_policy.max_cost_tokens,
        min_cost_tokens=file_policy.min_cost_tokens,
        cost_tolerance=file_policy.cost_tolerance,
        max_duration_ms=file_policy.max_duration_ms,
        min_duration_ms=file_policy.min_duration_ms,
        duration_tolerance=file_policy.duration_tolerance,
        expect_status=file_policy.expect_status,
    )
    for key, value in cli_overrides.items():
        if not hasattr(merged, key):
            continue
        if value is None:
            continue
        if isinstance(value, bool) and not value:
            continue
        setattr(merged, key, value)
    return merged
