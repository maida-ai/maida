"""Canonical plan artifacts and typed plan evidence for Maida contracts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, Mapping

from maida.schema_versions import PLAN_SCHEMA_VERSION

if TYPE_CHECKING:
    from maida.assertions import AssertionPolicy
    from maida.policy_types import MetricPolicy


_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class PlanContractError(ValueError):
    """A plan artifact or evidence value violated the core contract."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> PlanContractError:
    return PlanContractError(code, message)


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an object")
    return value


def _exact_fields(
    value: Mapping[str, Any], expected: set[str], field_name: str
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            f"{field_name} fields do not match the contract ({'; '.join(details)})",
        )


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be a non-empty string")
    return value


def _digest(value: object, field_name: str) -> str:
    result = _string(value, field_name)
    if _DIGEST_RE.fullmatch(result) is None:
        raise _fail(
            "PLAN_ARTIFACT_INVALID", f"{field_name} must be a lowercase SHA-256 digest"
        )
    return result


def _integer(value: object, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            f"{field_name} must be an integer of at least {minimum}",
        )
    return value


def _number(value: object, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
    ):
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            f"{field_name} must be a finite non-negative number",
        )
    return float(value)


def _string_set(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an array")
    items = tuple(_string(item, field_name) for item in value)
    if len(set(items)) != len(items):
        raise _fail(
            "PLAN_ARTIFACT_INVALID", f"{field_name} must not contain duplicates"
        )
    return tuple(sorted(items))


def _digest_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an array")
    return tuple(_digest(item, field_name) for item in value)


@dataclass(frozen=True)
class _PlanBudget:
    cost_usd: float
    model_tokens: int
    tool_calls: int
    wall_time_ms: int

    def to_dict(self) -> dict[str, int | float]:
        return {
            "cost_usd": self.cost_usd,
            "model_tokens": self.model_tokens,
            "tool_calls": self.tool_calls,
            "wall_time_ms": self.wall_time_ms,
        }


def _budget(value: object, field_name: str) -> _PlanBudget:
    data = _mapping(value, field_name)
    _exact_fields(
        data,
        {"cost_usd", "model_tokens", "tool_calls", "wall_time_ms"},
        field_name,
    )
    return _PlanBudget(
        cost_usd=_number(data["cost_usd"], f"{field_name}.cost_usd"),
        model_tokens=_integer(data["model_tokens"], f"{field_name}.model_tokens"),
        tool_calls=_integer(data["tool_calls"], f"{field_name}.tool_calls"),
        wall_time_ms=_integer(data["wall_time_ms"], f"{field_name}.wall_time_ms"),
    )


@dataclass(frozen=True, order=True)
class _PlanModule:
    module_id: str
    module_digest: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "module_digest": self.module_digest,
            "module_id": self.module_id,
        }


def _modules(value: object, field_name: str) -> tuple[_PlanModule, ...]:
    if not isinstance(value, list) or not value:
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be a non-empty array")
    modules = []
    for index, item in enumerate(value):
        data = _mapping(item, f"{field_name}[{index}]")
        _exact_fields(
            data,
            {"count", "module_digest", "module_id"},
            f"{field_name}[{index}]",
        )
        modules.append(
            _PlanModule(
                module_id=_string(
                    data["module_id"], f"{field_name}[{index}].module_id"
                ),
                module_digest=_digest(
                    data["module_digest"], f"{field_name}[{index}].module_digest"
                ),
                count=_integer(
                    data["count"], f"{field_name}[{index}].count", minimum=1
                ),
            )
        )
    identities = [(item.module_id, item.module_digest) for item in modules]
    if len(set(identities)) != len(identities):
        raise _fail(
            "PLAN_ARTIFACT_INVALID", f"{field_name} must not contain duplicates"
        )
    return tuple(sorted(modules))


@dataclass(frozen=True)
class _PlanGrant:
    capabilities: tuple[str, ...]
    effects: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "capabilities": list(self.capabilities),
            "effects": list(self.effects),
        }

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted({*self.capabilities, *self.effects}))


def _grant(value: object, field_name: str) -> _PlanGrant:
    data = _mapping(value, field_name)
    _exact_fields(data, {"capabilities", "effects"}, field_name)
    return _PlanGrant(
        capabilities=_string_set(data["capabilities"], f"{field_name}.capabilities"),
        effects=_string_set(data["effects"], f"{field_name}.effects"),
    )


def _approval_requirements(
    value: object, field_name: str
) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an array")
    result = []
    for index, item in enumerate(value):
        data = _mapping(item, f"{field_name}[{index}]")
        _exact_fields(data, {"effect_name", "node_key"}, f"{field_name}[{index}]")
        result.append(
            (
                _string(data["node_key"], f"{field_name}[{index}].node_key"),
                _string(data["effect_name"], f"{field_name}[{index}].effect_name"),
            )
        )
    if len(set(result)) != len(result):
        raise _fail(
            "PLAN_ARTIFACT_INVALID", f"{field_name} must not contain duplicates"
        )
    return tuple(sorted(result))


def _alias_provenance(value: object, field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an array")
    result = []
    for index, item in enumerate(value):
        data = _mapping(item, f"{field_name}[{index}]")
        _exact_fields(data, {"alias", "node_key"}, f"{field_name}[{index}]")
        result.append(
            (
                _string(data["node_key"], f"{field_name}[{index}].node_key"),
                _string(data["alias"], f"{field_name}[{index}].alias"),
            )
        )
    if len(set(result)) != len(result):
        raise _fail(
            "PLAN_ARTIFACT_INVALID", f"{field_name} must not contain duplicates"
        )
    return tuple(sorted(result))


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class PlanArtifact:
    """A canonical, addressable signature of a trusted resolved plan."""

    artifact_id: str
    plan_id: str
    source_signature_version: str
    aggregate_budget: _PlanBudget
    approval_requirements: tuple[tuple[str, str], ...]
    effectful_modules: tuple[str, ...]
    max_depth: int
    max_fanout: int
    module_composition: tuple[_PlanModule, ...]
    node_count: int
    output_schema_digests: tuple[str, ...]
    required_grant: _PlanGrant
    topology_digest: str
    region_grant: _PlanGrant
    alias_provenance: tuple[tuple[str, str], ...]

    def _signature_dict(self) -> dict[str, Any]:
        return {
            "aggregate_budget": self.aggregate_budget.to_dict(),
            "approval_requirements": [
                {"effect_name": effect_name, "node_key": node_key}
                for node_key, effect_name in self.approval_requirements
            ],
            "effectful_modules": list(self.effectful_modules),
            "max_depth": self.max_depth,
            "max_fanout": self.max_fanout,
            "module_composition": [item.to_dict() for item in self.module_composition],
            "node_count": self.node_count,
            "output_schema_digests": list(self.output_schema_digests),
            "required_grant": self.required_grant.to_dict(),
            "topology_digest": self.topology_digest,
        }

    def _identity_dict(self) -> dict[str, Any]:
        return {"plan_id": self.plan_id, "signature": self._signature_dict()}

    def to_dict(self) -> dict[str, Any]:
        """Return the versioned JSON-compatible contract representation."""
        return {
            "schema_version": PLAN_SCHEMA_VERSION,
            "artifact_id": self.artifact_id,
            "plan_id": self.plan_id,
            "source_signature_version": self.source_signature_version,
            "signature": self._signature_dict(),
            "trusted_context": {
                "alias_provenance": [
                    {"alias": alias, "node_key": node_key}
                    for node_key, alias in self.alias_provenance
                ],
                "region_grant": self.region_grant.to_dict(),
            },
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlanArtifact":
        """Parse and authenticate a serialized core plan artifact."""
        data = _mapping(value, "plan artifact")
        _exact_fields(
            data,
            {
                "schema_version",
                "artifact_id",
                "plan_id",
                "source_signature_version",
                "signature",
                "trusted_context",
            },
            "plan artifact",
        )
        if data["schema_version"] != PLAN_SCHEMA_VERSION:
            raise _fail(
                "PLAN_ARTIFACT_VERSION_UNSUPPORTED",
                f"unsupported plan schema version {data['schema_version']!r}; "
                f"this Maida supports {PLAN_SCHEMA_VERSION}",
            )
        signature = _mapping(data["signature"], "plan artifact.signature")
        _exact_fields(
            signature,
            {
                "aggregate_budget",
                "approval_requirements",
                "effectful_modules",
                "max_depth",
                "max_fanout",
                "module_composition",
                "node_count",
                "output_schema_digests",
                "required_grant",
                "topology_digest",
            },
            "plan artifact.signature",
        )
        context = _mapping(data["trusted_context"], "plan artifact.trusted_context")
        _exact_fields(
            context,
            {"alias_provenance", "region_grant"},
            "plan artifact.trusted_context",
        )
        artifact = _make_artifact(
            artifact_id=_digest(data["artifact_id"], "plan artifact.artifact_id"),
            plan_id=_string(data["plan_id"], "plan artifact.plan_id"),
            source_signature_version=_string(
                data["source_signature_version"],
                "plan artifact.source_signature_version",
            ),
            aggregate_budget=_budget(
                signature["aggregate_budget"],
                "plan artifact.signature.aggregate_budget",
            ),
            approval_requirements=_approval_requirements(
                signature["approval_requirements"],
                "plan artifact.signature.approval_requirements",
            ),
            effectful_modules=_string_set(
                signature["effectful_modules"],
                "plan artifact.signature.effectful_modules",
            ),
            max_depth=_integer(
                signature["max_depth"], "plan artifact.signature.max_depth", minimum=1
            ),
            max_fanout=_integer(
                signature["max_fanout"], "plan artifact.signature.max_fanout"
            ),
            module_composition=_modules(
                signature["module_composition"],
                "plan artifact.signature.module_composition",
            ),
            node_count=_integer(
                signature["node_count"], "plan artifact.signature.node_count", minimum=1
            ),
            output_schema_digests=_digest_list(
                signature["output_schema_digests"],
                "plan artifact.signature.output_schema_digests",
            ),
            required_grant=_grant(
                signature["required_grant"],
                "plan artifact.signature.required_grant",
            ),
            topology_digest=_digest(
                signature["topology_digest"],
                "plan artifact.signature.topology_digest",
            ),
            region_grant=_grant(
                context["region_grant"], "plan artifact.trusted_context.region_grant"
            ),
            alias_provenance=_alias_provenance(
                context["alias_provenance"],
                "plan artifact.trusted_context.alias_provenance",
            ),
        )
        if artifact.to_dict() != dict(data):
            raise _fail(
                "PLAN_ARTIFACT_NON_CANONICAL",
                "plan artifact arrays must use canonical ordering",
            )
        return artifact


def _make_artifact(
    *,
    artifact_id: str | None,
    plan_id: str,
    source_signature_version: str,
    aggregate_budget: _PlanBudget,
    approval_requirements: tuple[tuple[str, str], ...],
    effectful_modules: tuple[str, ...],
    max_depth: int,
    max_fanout: int,
    module_composition: tuple[_PlanModule, ...],
    node_count: int,
    output_schema_digests: tuple[str, ...],
    required_grant: _PlanGrant,
    topology_digest: str,
    region_grant: _PlanGrant,
    alias_provenance: tuple[tuple[str, str], ...],
) -> PlanArtifact:
    if not set(required_grant.capabilities) <= set(
        region_grant.capabilities
    ) or not set(required_grant.effects) <= set(region_grant.effects):
        raise _fail(
            "PLAN_REQUIRED_GRANT_EXCEEDS_REGION",
            "plan required grant exceeds its trusted region grant",
        )
    approved_effects = {effect_name for _node_key, effect_name in approval_requirements}
    if not approved_effects <= set(required_grant.effects):
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            "plan approval requirements must name required effects",
        )
    module_count = sum(item.count for item in module_composition)
    if module_count != node_count:
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            "plan module composition count must equal node_count",
        )
    prototype = PlanArtifact(
        artifact_id="",
        plan_id=plan_id,
        source_signature_version=source_signature_version,
        aggregate_budget=aggregate_budget,
        approval_requirements=approval_requirements,
        effectful_modules=effectful_modules,
        max_depth=max_depth,
        max_fanout=max_fanout,
        module_composition=module_composition,
        node_count=node_count,
        output_schema_digests=output_schema_digests,
        required_grant=required_grant,
        topology_digest=topology_digest,
        region_grant=region_grant,
        alias_provenance=alias_provenance,
    )
    expected_id = _canonical_digest(prototype._identity_dict())
    if artifact_id is not None and artifact_id != expected_id:
        raise _fail(
            "PLAN_ARTIFACT_DIGEST_MISMATCH",
            "plan artifact_id does not authenticate its behavior signature",
        )
    return PlanArtifact(**{**prototype.__dict__, "artifact_id": expected_id})


def plan_artifact_from_resolved_signature(value: Mapping[str, Any]) -> PlanArtifact:
    """Map a trusted resolved workflow signature into the core plan vocabulary."""
    data = _mapping(value, "resolved signature")
    required = {
        "version",
        "region_id",
        "aggregate_budget",
        "approval_requirements",
        "max_depth",
        "max_fanout",
        "module_composition",
        "node_count",
        "output_schema_digests",
        "required_grant",
        "region_grant",
        "topology_digest",
        "alias_provenance",
        "resolved_nodes",
    }
    missing = sorted(required - set(data))
    if missing:
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            f"resolved signature is missing {', '.join(missing)}",
        )

    modules = _modules(data["module_composition"], "module_composition")
    module_ids = {item.module_id for item in modules}
    resolved_nodes = data["resolved_nodes"]
    if not isinstance(resolved_nodes, list) or not resolved_nodes:
        raise _fail("PLAN_ARTIFACT_INVALID", "resolved_nodes must be a non-empty array")
    node_count = _integer(data["node_count"], "node_count", minimum=1)
    if len(resolved_nodes) != node_count:
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            "resolved_nodes length must equal node_count",
        )
    effectful_modules = set()
    resolved_module_counts: dict[str, int] = {}
    for index, item in enumerate(resolved_nodes):
        node = _mapping(item, f"resolved_nodes[{index}]")
        module_id = _string(node.get("module_id"), f"resolved_nodes[{index}].module_id")
        if module_id not in module_ids:
            raise _fail(
                "PLAN_ARTIFACT_INVALID",
                f"resolved_nodes[{index}].module_id is absent from module_composition",
            )
        resolved_module_counts[module_id] = resolved_module_counts.get(module_id, 0) + 1
        effects = node.get("effects")
        if not isinstance(effects, list):
            raise _fail(
                "PLAN_ARTIFACT_INVALID",
                f"resolved_nodes[{index}].effects must be an array",
            )
        for effect_index, effect in enumerate(effects):
            effect_data = _mapping(
                effect, f"resolved_nodes[{index}].effects[{effect_index}]"
            )
            _string(
                effect_data.get("name"),
                f"resolved_nodes[{index}].effects[{effect_index}].name",
            )
        if effects:
            effectful_modules.add(module_id)
    expected_module_counts = {item.module_id: item.count for item in modules}
    if resolved_module_counts != expected_module_counts:
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            "resolved_nodes do not match module_composition counts",
        )

    return _make_artifact(
        artifact_id=None,
        plan_id=_string(data["region_id"], "region_id"),
        source_signature_version=_string(data["version"], "version"),
        aggregate_budget=_budget(data["aggregate_budget"], "aggregate_budget"),
        approval_requirements=_approval_requirements(
            data["approval_requirements"], "approval_requirements"
        ),
        effectful_modules=tuple(sorted(effectful_modules)),
        max_depth=_integer(data["max_depth"], "max_depth", minimum=1),
        max_fanout=_integer(data["max_fanout"], "max_fanout"),
        module_composition=modules,
        node_count=node_count,
        output_schema_digests=_digest_list(
            data["output_schema_digests"], "output_schema_digests"
        ),
        required_grant=_grant(data["required_grant"], "required_grant"),
        topology_digest=_digest(data["topology_digest"], "topology_digest"),
        region_grant=_grant(data["region_grant"], "region_grant"),
        alias_provenance=_alias_provenance(
            data["alias_provenance"], "alias_provenance"
        ),
    )


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
    values: set[str], metric: "MetricPolicy", *, approved: set[str]
) -> bool:
    return (
        (not metric.allowed or values <= set(metric.allowed))
        and not bool(values & set(metric.none_of))
        and set(metric.all_of) <= values
        and set(metric.approval_required_for) <= approved
    )


def plan_invariant_outcomes(
    artifact: PlanArtifact, policy: "AssertionPolicy"
) -> dict[str, bool]:
    """Evaluate policy 2.1's exact set rules for one plan artifact."""
    plan_sets = plan_set_values(artifact)
    approved = {
        effect_name for _node_key, effect_name in artifact.approval_requirements
    }
    outcomes = {}
    for name, values in plan_sets.items():
        metric = policy.metrics.get(name)
        if metric is None or getattr(metric.kind, "value", metric.kind) != "invariant":
            continue
        outcomes[name] = _set_invariant_passes(set(values), metric, approved=approved)
    return outcomes


class PlanDiffKind(str, Enum):
    """Stable graph-change taxonomy shared with generated-plan producers."""

    MODULE_DIGEST_CHANGED = "MODULE_DIGEST_CHANGED"
    BUDGET_CHANGED = "BUDGET_CHANGED"
    CAPABILITY_CHANGED = "CAPABILITY_CHANGED"
    EFFECT_CHANGED = "EFFECT_CHANGED"
    CONNECTOR_CHANGED = "CONNECTOR_CHANGED"
    POLICY_CHANGED = "POLICY_CHANGED"
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    INSERTION = "INSERTION"
    DELETION = "DELETION"
    REORDER = "REORDER"
    TOPOLOGY_CHANGED = "TOPOLOGY_CHANGED"
    CONTROL_FLOW_CHANGED = "CONTROL_FLOW_CHANGED"


@dataclass(frozen=True)
class PlanGraphChange:
    """One typed structural difference between accepted and candidate plans."""

    kind: PlanDiffKind
    location: str
    before: Any
    after: Any
    resolvable: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "location": self.location,
            "before": self.before,
            "after": self.after,
            "resolvable": self.resolvable,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlanGraphChange":
        try:
            kind = PlanDiffKind(value.get("kind"))
        except (TypeError, ValueError) as error:
            raise _fail(
                "PLAN_EVIDENCE_INVALID", "unknown plan graph change kind"
            ) from error
        resolvable = value.get("resolvable")
        if not isinstance(resolvable, bool):
            raise _fail(
                "PLAN_EVIDENCE_INVALID", "graph change resolvable must be boolean"
            )
        return cls(
            kind=kind,
            location=_string(value.get("location"), "graph change location"),
            before=value.get("before"),
            after=value.get("after"),
            resolvable=resolvable,
        )


@dataclass(frozen=True)
class PlanValidationIssue:
    """One stable machine code and actionable plan-validation explanation."""

    code: str
    message: str
    location: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {"code": self.code, "message": self.message}
        if self.location is not None:
            payload["location"] = self.location
        return payload

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlanValidationIssue":
        location = value.get("location")
        if location is not None:
            location = _string(location, "plan issue location")
        return cls(
            code=_string(value.get("code"), "plan issue code"),
            message=_string(value.get("message"), "plan issue message"),
            location=location,
        )


@dataclass(frozen=True)
class PlanEvidence:
    """Typed pre-execution validation and comparison evidence for report v2."""

    artifact: PlanArtifact | None
    valid: bool
    issues: tuple[PlanValidationIssue, ...] = ()
    graph_changes: tuple[PlanGraphChange, ...] = ()
    trial: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.valid, bool):
            raise _fail("PLAN_EVIDENCE_INVALID", "plan evidence valid must be boolean")
        if self.trial is not None and (
            isinstance(self.trial, bool)
            or not isinstance(self.trial, int)
            or self.trial < 1
        ):
            raise _fail(
                "PLAN_EVIDENCE_INVALID", "plan evidence trial must be at least 1"
            )
        if self.valid and self.artifact is None:
            raise _fail(
                "PLAN_EVIDENCE_INVALID", "valid plan evidence requires an artifact"
            )
        if self.valid and self.issues:
            raise _fail(
                "PLAN_EVIDENCE_INVALID", "valid plan evidence cannot contain issues"
            )
        if not self.valid and not self.issues:
            raise _fail(
                "PLAN_EVIDENCE_INVALID", "invalid plan evidence requires an issue"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trial": self.trial,
            "checked_before_execution": True,
            "valid": self.valid,
            "artifact": self.artifact.to_dict() if self.artifact is not None else None,
            "issues": [issue.to_dict() for issue in self.issues],
            "graph_changes": [change.to_dict() for change in self.graph_changes],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PlanEvidence":
        data = _mapping(value, "plan evidence")
        artifact_data = data.get("artifact")
        artifact = None
        if artifact_data is not None:
            artifact = PlanArtifact.from_dict(
                _mapping(artifact_data, "plan evidence artifact")
            )
        issues_data = data.get("issues")
        changes_data = data.get("graph_changes")
        if data.get("checked_before_execution") is not True:
            raise _fail(
                "PLAN_EVIDENCE_INVALID",
                "plan evidence must be checked before execution",
            )
        if not isinstance(issues_data, list) or not isinstance(changes_data, list):
            raise _fail(
                "PLAN_EVIDENCE_INVALID",
                "plan evidence issues and graph_changes must be arrays",
            )
        return cls(
            artifact=artifact,
            valid=data.get("valid"),
            issues=tuple(
                PlanValidationIssue.from_dict(_mapping(item, "plan evidence issue"))
                for item in issues_data
            ),
            graph_changes=tuple(
                PlanGraphChange.from_dict(_mapping(item, "plan graph change"))
                for item in changes_data
            ),
            trial=data.get("trial"),
        )


__all__ = [
    "PlanArtifact",
    "PlanContractError",
    "PlanDiffKind",
    "PlanEvidence",
    "PlanGraphChange",
    "PlanValidationIssue",
    "plan_artifact_from_resolved_signature",
    "plan_invariant_outcomes",
    "plan_metric_values",
    "plan_set_values",
]
