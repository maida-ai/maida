"""Typed policy primitives shared by loading, assertions, and gate execution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class MetricKind(str, Enum):
    """Where a metric's acceptance criterion comes from."""

    INVARIANT = "invariant"
    MEASURED = "measured"
    DISTRIBUTIONAL = "distributional"
    STATISTICAL = "statistical"


class MetricDirection(str, Enum):
    """Which side of a metric can block."""

    UPPER = "upper"
    LOWER = "lower"
    BOTH = "both"


class MetricMode(str, Enum):
    """Whether a metric contributes a blocking verdict."""

    GATING = "gating"
    REPORT_ONLY = "report_only"


@dataclass
class MetricPolicy:
    """Normalized v2 configuration for one named metric."""

    name: str
    kind: MetricKind
    direction: MetricDirection | None = None
    mode: MetricMode | None = None
    require: bool | None = None
    none_of: tuple[str, ...] = ()
    all_of: tuple[str, ...] = ()
    allowed: tuple[str, ...] | None = None
    approval_required_for: tuple[str, ...] = ()
    tolerance_relative: float | None = None
    tolerance_absolute: float | None = None
    limit: float | tuple[float, float] | None = None
    aggregate: str = "median"
    threshold: float | tuple[float, float] | None = None
    confidence: float = 0.95
    coverage: float = 0.95
    success_predicate: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the normalized public representation used in reports."""
        result: dict[str, Any] = {"name": self.name, "kind": self.kind.value}
        if self.direction is not None:
            result["direction"] = self.direction.value
        if self.mode is not None:
            result["mode"] = self.mode.value
        if self.aggregate:
            result["aggregate"] = self.aggregate
        if self.none_of:
            result["none_of"] = list(self.none_of)
        if self.all_of:
            result["all_of"] = list(self.all_of)
        if self.allowed is not None:
            result["allowed"] = list(self.allowed)
        if self.approval_required_for:
            result["approval_required_for"] = list(self.approval_required_for)
        return result


CANONICAL_METRIC_NAMES = frozenset(
    {
        "stop_condition_reached",
        "forbidden_tools",
        "required_tools",
        "no_loops",
        "no_guardrails",
        "step_count",
        "tool_call_count",
        "cost_tokens",
        "latency_ms",
        "task_pass_rate",
        "plan_depth",
        "plan_fanout",
        "plan_budget_cost_usd",
        "plan_budget_model_tokens",
        "plan_budget_tool_calls",
        "plan_budget_wall_time_ms",
        "plan_effectful_modules",
        "plan_grants",
    }
)

NUMERIC_METRIC_NAMES = frozenset(
    {
        "step_count",
        "tool_call_count",
        "cost_tokens",
        "latency_ms",
        "plan_depth",
        "plan_fanout",
        "plan_budget_cost_usd",
        "plan_budget_model_tokens",
        "plan_budget_tool_calls",
        "plan_budget_wall_time_ms",
    }
)

INVARIANT_METRIC_NAMES = frozenset(
    {
        "stop_condition_reached",
        "forbidden_tools",
        "required_tools",
        "no_loops",
        "no_guardrails",
        "plan_effectful_modules",
        "plan_grants",
    }
)

PLAN_METRIC_NAMES = frozenset(
    {
        "plan_depth",
        "plan_fanout",
        "plan_budget_cost_usd",
        "plan_budget_model_tokens",
        "plan_budget_tool_calls",
        "plan_budget_wall_time_ms",
        "plan_effectful_modules",
        "plan_grants",
    }
)
