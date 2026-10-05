"""Canonical plan artifacts and resolved-signature mapping."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from maida._plan_contract.errors import _fail
from maida._plan_contract.parsing import (
    _PlanBudget,
    _PlanGrant,
    _PlanModule,
    _alias_provenance,
    _approval_requirements,
    _budget,
    _canonical_digest,
    _digest,
    _digest_list,
    _exact_fields,
    _grant,
    _integer,
    _mapping,
    _modules,
    _string,
    _string_set,
)
from maida.schema_versions import PLAN_SCHEMA_VERSION


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
                    {"alias": alias, "node_key": node_key} for node_key, alias in self.alias_provenance
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
            max_depth=_integer(signature["max_depth"], "plan artifact.signature.max_depth", minimum=1),
            max_fanout=_integer(signature["max_fanout"], "plan artifact.signature.max_fanout"),
            module_composition=_modules(
                signature["module_composition"],
                "plan artifact.signature.module_composition",
            ),
            node_count=_integer(signature["node_count"], "plan artifact.signature.node_count", minimum=1),
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
            region_grant=_grant(context["region_grant"], "plan artifact.trusted_context.region_grant"),
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
    if not set(required_grant.capabilities) <= set(region_grant.capabilities) or not set(required_grant.effects) <= set(
        region_grant.effects
    ):
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
            effect_data = _mapping(effect, f"resolved_nodes[{index}].effects[{effect_index}]")
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
        approval_requirements=_approval_requirements(data["approval_requirements"], "approval_requirements"),
        effectful_modules=tuple(sorted(effectful_modules)),
        max_depth=_integer(data["max_depth"], "max_depth", minimum=1),
        max_fanout=_integer(data["max_fanout"], "max_fanout"),
        module_composition=modules,
        node_count=node_count,
        output_schema_digests=_digest_list(data["output_schema_digests"], "output_schema_digests"),
        required_grant=_grant(data["required_grant"], "required_grant"),
        topology_digest=_digest(data["topology_digest"], "topology_digest"),
        region_grant=_grant(data["region_grant"], "region_grant"),
        alias_provenance=_alias_provenance(data["alias_provenance"], "alias_provenance"),
    )
