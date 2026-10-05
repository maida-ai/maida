"""Plan metric and invariant extraction for policy 2.1."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Mapping

from maida._plan_contract.artifact import PlanArtifact

if TYPE_CHECKING:
    from maida.assertions import AssertionPolicy
    from maida.policy_types import MetricPolicy


def plan_metric_values(artifact: PlanArtifact) -> dict[str, float]:
    """Extract the numeric plan metrics accepted by policy 2.1."""
    budget = artifact.aggregate_budget
    return {
        "plan_depth": float(artifact.max_depth),
        "plan_fanout": float(artifact.max_fanout),
        "plan_budget_cost_usd": budget.cost_usd,
        "plan_budget_model_tokens": float(budget.model_tokens),
        "plan_budget_tool_calls": float(budget.tool_calls),
        "plan_budget_wall_time_ms": float(budget.wall_time_ms),
    }


def plan_set_values(artifact: PlanArtifact) -> dict[str, tuple[str, ...]]:
    """Extract the set-valued plan invariants accepted by policy 2.1."""
    return {
        "plan_effectful_modules": artifact.effectful_modules,
        "plan_grants": artifact.required_grant.names,
    }


def _set_invariant_passes(
    values: set[str],
    metric: "MetricPolicy",
    *,
    approved: set[str],
    approval_scope: set[str],
) -> bool:
    required_approvals = set(metric.approval_required_for) & approval_scope
    return (
        (metric.allowed is None or values <= set(metric.allowed))
        and not bool(values & set(metric.none_of))
        and set(metric.all_of) <= values
        and required_approvals <= approved
    )


def _baseline_topology_digests(baseline: Mapping[str, Any] | None, *, plan_id: str) -> set[str]:
    if not isinstance(baseline, Mapping):
        return set()
    sample = baseline.get("plan_sample")
    if not isinstance(sample, Mapping) or sample.get("plan_id") != plan_id:
        return set()
    artifacts = sample.get("artifacts")
    if not isinstance(artifacts, Mapping):
        return set()
    result = set()
    for value in artifacts.values():
        if not isinstance(value, Mapping):
            continue
        accepted = PlanArtifact.from_dict(value)
        if accepted.plan_id == plan_id:
            result.add(accepted.topology_digest)
    return result


def plan_invariant_outcomes(
    artifact: PlanArtifact,
    policy: "AssertionPolicy",
    *,
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, bool]:
    """Evaluate policy 2.1's exact set rules for one plan artifact."""
    plan_sets = plan_set_values(artifact)
    plan_sets["plan_modules"] = tuple(sorted({module.module_id for module in artifact.module_composition}))
    approved = {effect_name for _node_key, effect_name in artifact.approval_requirements}
    outcomes = {}
    for name, values in plan_sets.items():
        metric = policy.metrics.get(name)
        if metric is None or getattr(metric.kind, "value", metric.kind) != "invariant":
            continue
        approval_scope = set(artifact.required_grant.effects) if name == "plan_grants" else set()
        outcomes[name] = _set_invariant_passes(
            set(values),
            metric,
            approved=approved,
            approval_scope=approval_scope,
        )
    shape_metric = policy.metrics.get("plan_shape_seen")
    if shape_metric is not None and getattr(shape_metric.kind, "value", shape_metric.kind) == "invariant":
        seen = artifact.topology_digest in _baseline_topology_digests(baseline, plan_id=artifact.plan_id)
        outcomes["plan_shape_seen"] = seen is bool(shape_metric.require)
    return outcomes
