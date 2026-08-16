"""Strict policy-v2 loading plus visible migration from the legacy policy."""

from __future__ import annotations

import copy
import math
import re
import warnings
from dataclasses import fields
from pathlib import Path
from statistics import NormalDist
from typing import Any

from maida.assertions import AssertionPolicy
from maida.policy_types import (
    CANONICAL_METRIC_NAMES,
    INVARIANT_METRIC_NAMES,
    NUMERIC_METRIC_NAMES,
    MetricDirection,
    MetricKind,
    MetricMode,
    MetricPolicy,
    PLAN_METRIC_NAMES,
)
from maida.schema_versions import POLICY_SCHEMA_VERSION

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]


class PolicyDeprecationWarning(UserWarning):
    """A legacy policy was loaded through the compatibility migration."""


_POLICY_VERSION = tuple(int(part) for part in POLICY_SCHEMA_VERSION.split("."))
_VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")
_TOP_LEVEL_KEYS = frozenset({"version", "trials", "fail_fast", "metrics"})
_PREDICATES = frozenset({"all_invariants_passed"})


def _finite_number(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{field} must be a finite number")
    return float(value)


def _fraction(value: object, field: str, *, inclusive: bool = False) -> float:
    result = _finite_number(value, field)
    valid = 0.0 <= result <= 1.0 if inclusive else 0.0 < result < 1.0
    if not valid:
        qualifier = "between 0 and 1" if inclusive else "greater than 0 and less than 1"
        raise ValueError(f"{field} must be {qualifier}")
    return result


def _policy_version_lexeme(text: str, data: dict[str, Any]) -> str | None:
    if "version" not in data:
        return None
    match = re.search(r"(?m)^version\s*:\s*([^#\s]+)", text)
    if match is None:
        raise ValueError("version must be a top-level scalar")
    return match.group(1).strip("\"'")


def _parse_policy_version(text: str, data: dict[str, Any]) -> tuple[int, int] | None:
    lexeme = _policy_version_lexeme(text, data)
    if lexeme is None:
        return None
    if not _VERSION_RE.fullmatch(lexeme):
        raise ValueError(
            "policy version must use major or major.minor form "
            "(for example `version: 2` or `version: 2.1`); patch versions are invalid"
        )
    parts = tuple(int(part) for part in lexeme.split("."))
    version = (parts[0], parts[1] if len(parts) == 2 else 0)
    if version[0] not in {1, 2}:
        raise ValueError(
            f"policy major {version[0]} is unsupported; upgrade Maida to a version "
            f"that supports policy {lexeme}"
        )
    if version[0] == 2 and version[1] > _POLICY_VERSION[1]:
        raise ValueError(
            f"policy version {lexeme} requires a newer Maida policy loader; "
            f"this version supports 2.{_POLICY_VERSION[1]}"
        )
    return version


def _one_sided_wilson_bounds(
    successes: int, trials: int, confidence: float
) -> tuple[float, float]:
    z = NormalDist().inv_cdf(confidence)
    proportion = successes / trials
    z2 = z * z
    denominator = 1.0 + z2 / trials
    center = (proportion + z2 / (2.0 * trials)) / denominator
    margin = (
        z
        * math.sqrt(
            proportion * (1.0 - proportion) / trials + z2 / (4.0 * trials * trials)
        )
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def minimum_trials_for_pass(
    threshold: float | tuple[float, float],
    confidence: float,
    direction: MetricDirection,
) -> int:
    """Smallest fixed N for which a statistical PASS is reachable."""
    z2 = NormalDist().inv_cdf(confidence) ** 2
    if direction is MetricDirection.LOWER:
        theta = float(threshold)
        if theta >= 1.0:
            raise ValueError(
                "a lower statistical threshold of 1 makes PASS unreachable"
            )
        return max(1, math.ceil(theta * z2 / (1.0 - theta)))
    if direction is MetricDirection.UPPER:
        theta = float(threshold)
        if theta <= 0.0:
            raise ValueError(
                "an upper statistical threshold of 0 makes PASS unreachable"
            )
        return max(1, math.ceil((1.0 - theta) * z2 / theta))

    lower, upper = threshold
    for trials in range(1, 100_001):
        for successes in range(trials + 1):
            bound_lower, bound_upper = _one_sided_wilson_bounds(
                successes, trials, confidence
            )
            if bound_lower >= lower and bound_upper <= upper:
                return trials
    raise ValueError(
        f"statistical threshold range [{lower}, {upper}] cannot produce a PASS"
    )


def _parse_direction(value: object, field: str) -> MetricDirection:
    try:
        return MetricDirection(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be upper, lower, or both") from error


def _parse_mode(value: object, field: str) -> MetricMode | None:
    if value is None:
        return None
    try:
        return MetricMode(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field} must be gating or report_only") from error


def _reject_unknown(data: dict[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{field} contains unknown field(s): {', '.join(unknown)}")


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ValueError(f"{field} must be a list of non-empty strings")
    return tuple(value)


def _parse_limit(
    value: object, field: str, direction: MetricDirection
) -> float | tuple[float, float] | None:
    if value is None:
        return None
    if direction is MetricDirection.BOTH:
        if not isinstance(value, dict):
            raise ValueError(f"{field} must contain lower and upper")
        _reject_unknown(value, {"lower", "upper"}, field)
        if set(value) != {"lower", "upper"}:
            raise ValueError(f"{field} must contain lower and upper")
        lower = _finite_number(value["lower"], f"{field}.lower")
        upper = _finite_number(value["upper"], f"{field}.upper")
        if lower > upper:
            raise ValueError(f"{field}.lower must not exceed upper")
        return lower, upper
    return _finite_number(value, field)


def _parse_invariant(name: str, data: dict[str, Any]) -> MetricPolicy:
    allowed = {"kind", "require", "none_of", "all_of"}
    if name in {"plan_effectful_modules", "plan_grants"}:
        allowed.add("allowed")
    if name == "plan_grants":
        allowed.add("approval_required_for")
    _reject_unknown(data, allowed, f"metrics.{name}")
    metric = MetricPolicy(name=name, kind=MetricKind.INVARIANT, aggregate="")
    if name == "forbidden_tools":
        if "none_of" not in data:
            raise ValueError("metrics.forbidden_tools requires none_of")
        metric.none_of = _string_tuple(
            data["none_of"], "metrics.forbidden_tools.none_of"
        )
    elif name == "required_tools":
        if "all_of" not in data:
            raise ValueError("metrics.required_tools requires all_of")
        metric.all_of = _string_tuple(data["all_of"], "metrics.required_tools.all_of")
    elif name in {"plan_effectful_modules", "plan_grants"}:
        configured = {
            field_name
            for field_name in ("none_of", "all_of", "allowed", "approval_required_for")
            if field_name in data
        }
        if not configured:
            raise ValueError(
                f"metrics.{name} requires none_of, all_of, allowed, or "
                "approval_required_for"
            )
        metric.none_of = _string_tuple(
            data.get("none_of", []), f"metrics.{name}.none_of"
        )
        metric.all_of = _string_tuple(data.get("all_of", []), f"metrics.{name}.all_of")
        metric.allowed = _string_tuple(
            data.get("allowed", []), f"metrics.{name}.allowed"
        )
        if name == "plan_grants":
            metric.approval_required_for = _string_tuple(
                data.get("approval_required_for", []),
                "metrics.plan_grants.approval_required_for",
            )
    else:
        require = data.get("require", True)
        if not isinstance(require, bool):
            raise ValueError(f"metrics.{name}.require must be a boolean")
        metric.require = require
    return metric


def _parse_measured(name: str, data: dict[str, Any]) -> MetricPolicy:
    allowed = {"kind", "direction", "tolerance", "limit", "aggregate"}
    _reject_unknown(data, allowed, f"metrics.{name}")
    direction = _parse_direction(data.get("direction"), f"metrics.{name}.direction")
    aggregate = data.get("aggregate", "median")
    if aggregate not in {"median", "max", "p90"}:
        raise ValueError(f"metrics.{name}.aggregate must be median, max, or p90")
    if aggregate in {"max", "p90"} and direction is not MetricDirection.UPPER:
        raise ValueError(
            f"metrics.{name}.aggregate {aggregate} is only valid for direction upper"
        )
    relative = absolute = None
    tolerance = data.get("tolerance")
    if tolerance is not None:
        if not isinstance(tolerance, dict):
            raise ValueError(f"metrics.{name}.tolerance must be an object")
        _reject_unknown(
            tolerance, {"relative", "absolute"}, f"metrics.{name}.tolerance"
        )
        if not tolerance:
            raise ValueError(f"metrics.{name}.tolerance must not be empty")
        if "relative" in tolerance:
            relative = _finite_number(
                tolerance["relative"], f"metrics.{name}.tolerance.relative"
            )
            if relative < 0:
                raise ValueError(
                    f"metrics.{name}.tolerance.relative must be non-negative"
                )
        if "absolute" in tolerance:
            absolute = _finite_number(
                tolerance["absolute"], f"metrics.{name}.tolerance.absolute"
            )
            if absolute < 0:
                raise ValueError(
                    f"metrics.{name}.tolerance.absolute must be non-negative"
                )
    limit = _parse_limit(data.get("limit"), f"metrics.{name}.limit", direction)
    if tolerance is None and limit is None:
        raise ValueError(f"metrics.{name} requires tolerance or limit")
    return MetricPolicy(
        name=name,
        kind=MetricKind.MEASURED,
        direction=direction,
        tolerance_relative=relative,
        tolerance_absolute=absolute,
        limit=limit,
        aggregate=aggregate,
    )


def _parse_distributional(name: str, data: dict[str, Any]) -> MetricPolicy:
    allowed = {"kind", "direction", "coverage", "confidence", "mode"}
    _reject_unknown(data, allowed, f"metrics.{name}")
    direction = _parse_direction(data.get("direction"), f"metrics.{name}.direction")
    if direction is MetricDirection.BOTH:
        raise ValueError(
            f"metrics.{name} distributional direction both is not supported; "
            "declare a measured tolerance or choose upper/lower"
        )
    coverage = _fraction(data.get("coverage", 0.95), f"metrics.{name}.coverage")
    confidence = _fraction(data.get("confidence", 0.95), f"metrics.{name}.confidence")
    return MetricPolicy(
        name=name,
        kind=MetricKind.DISTRIBUTIONAL,
        direction=direction,
        coverage=coverage,
        confidence=confidence,
        mode=_parse_mode(data.get("mode"), f"metrics.{name}.mode"),
        aggregate="",
    )


def _parse_threshold(
    value: object, field: str, direction: MetricDirection
) -> float | tuple[float, float]:
    if direction is MetricDirection.BOTH:
        if not isinstance(value, dict) or set(value) != {"lower", "upper"}:
            raise ValueError(f"{field} must contain lower and upper")
        lower = _fraction(value["lower"], f"{field}.lower", inclusive=True)
        upper = _fraction(value["upper"], f"{field}.upper", inclusive=True)
        if lower > upper:
            raise ValueError(f"{field}.lower must not exceed upper")
        return lower, upper
    return _fraction(value, field, inclusive=True)


def _parse_statistical(name: str, data: dict[str, Any], *, trials: int) -> MetricPolicy:
    allowed = {
        "kind",
        "direction",
        "threshold",
        "confidence",
        "mode",
        "success_predicate",
    }
    _reject_unknown(data, allowed, f"metrics.{name}")
    direction = _parse_direction(data.get("direction"), f"metrics.{name}.direction")
    if "threshold" not in data:
        raise ValueError(f"metrics.{name} requires threshold")
    threshold = _parse_threshold(
        data["threshold"], f"metrics.{name}.threshold", direction
    )
    confidence = _fraction(data.get("confidence", 0.95), f"metrics.{name}.confidence")
    predicate = data.get("success_predicate", "all_invariants_passed")
    if predicate not in _PREDICATES:
        raise ValueError(
            f"metrics.{name}.success_predicate must be all_invariants_passed"
        )
    mode = _parse_mode(data.get("mode"), f"metrics.{name}.mode")
    n_min = minimum_trials_for_pass(threshold, confidence, direction)
    if mode is MetricMode.GATING and trials < n_min:
        raise ValueError(
            f"metrics.{name} cannot PASS with threshold={threshold}, "
            f"confidence={confidence:g}: n_min={n_min}, configured trials={trials}. "
            f"Raise trials to at least {n_min}, or set mode: report_only."
        )
    return MetricPolicy(
        name=name,
        kind=MetricKind.STATISTICAL,
        direction=direction,
        threshold=threshold,
        confidence=confidence,
        mode=mode,
        success_predicate=predicate,
        aggregate="",
    )


def _parse_v2(data: dict[str, Any], version: tuple[int, int]) -> AssertionPolicy:
    _reject_unknown(data, set(_TOP_LEVEL_KEYS), "policy")
    trials = data.get("trials", 3)
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError("trials must be an integer of at least 1")
    fail_fast = data.get("fail_fast", True)
    if not isinstance(fail_fast, bool):
        raise ValueError("fail_fast must be a boolean")
    raw_metrics = data.get("metrics")
    if not isinstance(raw_metrics, dict):
        raise ValueError("metrics must be an object")

    metrics: dict[str, MetricPolicy] = {}
    for name, raw_metric in raw_metrics.items():
        if name not in CANONICAL_METRIC_NAMES:
            raise ValueError(f"unknown metric: {name}")
        if not isinstance(raw_metric, dict):
            raise ValueError(f"metrics.{name} must be an object")
        if name in PLAN_METRIC_NAMES and version < (2, 1):
            raise ValueError(f"metric {name} requires policy version 2.1")
        try:
            kind = MetricKind(raw_metric.get("kind"))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"metrics.{name}.kind must be invariant, measured, "
                "distributional, or statistical"
            ) from error
        if kind is MetricKind.INVARIANT:
            if name not in INVARIANT_METRIC_NAMES:
                raise ValueError(f"metric {name} cannot use kind invariant")
            metric = _parse_invariant(name, raw_metric)
        elif kind is MetricKind.MEASURED:
            if name not in NUMERIC_METRIC_NAMES:
                raise ValueError(f"metric {name} cannot use kind measured")
            metric = _parse_measured(name, raw_metric)
        elif kind is MetricKind.DISTRIBUTIONAL:
            if name not in NUMERIC_METRIC_NAMES:
                raise ValueError(f"metric {name} cannot use kind distributional")
            metric = _parse_distributional(name, raw_metric)
        else:
            if name != "task_pass_rate":
                raise ValueError(
                    "task_pass_rate is the only supported statistical predicate"
                )
            metric = _parse_statistical(name, raw_metric, trials=trials)
        metrics[name] = metric

    task_metric = metrics.get("task_pass_rate")
    confidence = task_metric.confidence if task_metric else 0.95
    threshold = (
        float(task_metric.threshold)
        if task_metric and not isinstance(task_metric.threshold, tuple)
        else 0.90
    )
    return AssertionPolicy(
        trials=trials,
        confidence_level=confidence,
        pass_rate_threshold=threshold,
        fail_fast=fail_fast,
        policy_version=version,
        source_format="v2",
        metrics=metrics,
    )


_LEGACY_FIELDS = {field.name for field in fields(AssertionPolicy)}
_LEGACY_FIELDS -= {"policy_version", "source_format", "metrics", "fail_fast"}


def _legacy_metrics(policy: AssertionPolicy) -> dict[str, MetricPolicy]:
    metrics: dict[str, MetricPolicy] = {
        "step_count": MetricPolicy(
            name="step_count",
            kind=MetricKind.MEASURED,
            direction=MetricDirection.UPPER,
            tolerance_relative=policy.step_tolerance,
            limit=float(policy.max_steps) if policy.max_steps is not None else None,
        ),
        "tool_call_count": MetricPolicy(
            name="tool_call_count",
            kind=MetricKind.MEASURED,
            direction=MetricDirection.UPPER,
            tolerance_relative=policy.tool_call_tolerance,
            limit=(
                float(policy.max_tool_calls)
                if policy.max_tool_calls is not None
                else None
            ),
        ),
        "cost_tokens": MetricPolicy(
            name="cost_tokens",
            kind=MetricKind.MEASURED,
            direction=MetricDirection.UPPER,
            tolerance_relative=policy.cost_tolerance,
            limit=(
                float(policy.max_cost_tokens)
                if policy.max_cost_tokens is not None
                else None
            ),
        ),
        "latency_ms": MetricPolicy(
            name="latency_ms",
            kind=MetricKind.MEASURED,
            direction=MetricDirection.UPPER,
            tolerance_relative=policy.duration_tolerance,
            limit=(
                float(policy.max_duration_ms)
                if policy.max_duration_ms is not None
                else None
            ),
        ),
    }
    if policy.no_loops:
        metrics["no_loops"] = MetricPolicy(
            name="no_loops", kind=MetricKind.INVARIANT, require=True, aggregate=""
        )
    if policy.no_guardrails:
        metrics["no_guardrails"] = MetricPolicy(
            name="no_guardrails",
            kind=MetricKind.INVARIANT,
            require=True,
            aggregate="",
        )
    if policy.no_new_tools:
        metrics["forbidden_tools"] = MetricPolicy(
            name="forbidden_tools",
            kind=MetricKind.INVARIANT,
            none_of=(),
            aggregate="",
        )
    if policy.expect_status is not None:
        metrics["stop_condition_reached"] = MetricPolicy(
            name="stop_condition_reached",
            kind=MetricKind.INVARIANT,
            require=policy.expect_status == "ok",
            aggregate="",
        )
    metrics["task_pass_rate"] = MetricPolicy(
        name="task_pass_rate",
        kind=MetricKind.STATISTICAL,
        direction=MetricDirection.LOWER,
        threshold=policy.pass_rate_threshold,
        confidence=policy.confidence_level,
        mode=None,
        success_predicate="all_invariants_passed",
        aggregate="",
    )
    return metrics


def _parse_v1(data: dict[str, Any]) -> AssertionPolicy:
    section = data.get("assert")
    if not isinstance(section, dict):
        section = {}
    unknown = sorted(set(section) - _LEGACY_FIELDS)
    warning = (
        "Policy v1 is deprecated and was migrated in memory to v2; "
        "run `maida init` or update the file to declare version: 2 and metric kinds."
    )
    if unknown:
        warning += f" Ignored unknown v1 field(s): {', '.join(unknown)}."
    warnings.warn(warning, PolicyDeprecationWarning, stacklevel=3)
    kwargs = {key: value for key, value in section.items() if key in _LEGACY_FIELDS}
    policy = AssertionPolicy(**kwargs)
    policy.policy_version = (1, 0)
    policy.source_format = "v1"
    policy.metrics = _legacy_metrics(policy)
    policy.validate()
    return policy


def load_policy(path: Path) -> AssertionPolicy:
    """Load a strict policy v2 or visibly migrate the legacy v1 shape."""
    if yaml is None:
        raise RuntimeError("PyYAML is required to load policy files")
    if not path.is_file():
        raise FileNotFoundError(f"Policy file not found: {path}")
    text = path.read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("policy root must be an object")
    version = _parse_policy_version(text, data)
    if version is None or version[0] == 1:
        return _parse_v1(data)
    return _parse_v2(data, version)


def merge_policy(
    file_policy: AssertionPolicy,
    cli_overrides: dict[str, Any],
) -> AssertionPolicy:
    """Overlay legacy CLI flags without weakening strict file validation."""
    merged = copy.deepcopy(file_policy)
    bool_fields = {"no_new_tools", "no_loops", "no_guardrails"}
    for key, value in cli_overrides.items():
        if value is None or not hasattr(merged, key):
            continue
        if key in bool_fields and isinstance(value, bool) and not value:
            continue
        if key == "ignored_checks":
            existing = set(merged.ignored_checks or [])
            merged.ignored_checks = sorted(existing | set(value))
        else:
            setattr(merged, key, value)

    if merged.source_format == "v2":
        task = merged.metrics.get("task_pass_rate")
        if task is not None:
            if cli_overrides.get("confidence_level") is not None:
                task.confidence = merged.confidence_level
            if cli_overrides.get("pass_rate_threshold") is not None:
                task.threshold = merged.pass_rate_threshold
            if task.mode is MetricMode.GATING:
                n_min = minimum_trials_for_pass(
                    task.threshold, task.confidence, task.direction
                )
                if merged.trials < n_min:
                    raise ValueError(
                        "metrics.task_pass_rate cannot PASS with "
                        f"threshold={task.threshold}, confidence={task.confidence:g}: "
                        f"n_min={n_min}, configured trials={merged.trials}. "
                        f"Raise trials to at least {n_min}, or set mode: report_only."
                    )
        legacy_map = {
            "max_steps": ("step_count", "limit"),
            "step_tolerance": ("step_count", "tolerance_relative"),
            "max_tool_calls": ("tool_call_count", "limit"),
            "tool_call_tolerance": ("tool_call_count", "tolerance_relative"),
            "max_cost_tokens": ("cost_tokens", "limit"),
            "cost_tolerance": ("cost_tokens", "tolerance_relative"),
            "max_duration_ms": ("latency_ms", "limit"),
            "duration_tolerance": ("latency_ms", "tolerance_relative"),
        }
        for override, (metric_name, attribute) in legacy_map.items():
            value = cli_overrides.get(override)
            metric = merged.metrics.get(metric_name)
            if value is not None and metric is not None:
                setattr(metric, attribute, float(value))
    else:
        merged.metrics = _legacy_metrics(merged)
    merged.validate()
    return merged
