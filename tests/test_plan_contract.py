"""Core plan artifacts, policy vocabulary, evidence, and baseline populations."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from maida.baseline_sample import create_baseline_from_report
from maida.plan_contract import (
    PlanArtifact,
    PlanContractError,
    PlanDiffKind,
    PlanEvidence,
    PlanGraphChange,
    PlanValidationIssue,
    plan_artifact_from_resolved_signature,
    plan_invariant_outcomes,
    plan_metric_values,
)
from maida.policy import load_policy
from maida.runner_v2 import TrialRunReport
from maida.schema_versions import (
    BASELINE_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
)


ROOT = Path(__file__).parents[1]


def _digest(character: str) -> str:
    return character * 64


def _resolved_signature() -> dict:
    """Relevant fields from Task 02's real thorough-plan signature."""
    return {
        "version": "0.3.0",
        "region_id": "request-plan",
        "aggregate_budget": {
            "cost_usd": 0.0,
            "model_tokens": 0,
            "tool_calls": 2,
            "wall_time_ms": 4000,
        },
        "max_depth": 4,
        "max_fanout": 2,
        "node_count": 5,
        "module_composition": [
            {"count": 1, "module_digest": _digest("a"), "module_id": "demo.audit"},
            {
                "count": 1,
                "module_digest": _digest("b"),
                "module_id": "demo.context",
            },
            {
                "count": 1,
                "module_digest": _digest("c"),
                "module_id": "demo.deliver",
            },
            {"count": 1, "module_digest": _digest("d"), "module_id": "demo.draft"},
            {
                "count": 1,
                "module_digest": _digest("e"),
                "module_id": "demo.normalize",
            },
        ],
        "required_grant": {
            "capabilities": ["records.context.read"],
            "effects": ["messages.deliver"],
        },
        "region_grant": {
            "capabilities": ["records.context.read"],
            "effects": ["messages.deliver"],
        },
        "approval_requirements": [],
        "topology_digest": _digest("f"),
        "output_schema_digests": [_digest("1")],
        "alias_provenance": [
            {"alias": "text.audit", "node_key": "audit"},
            {"alias": "records.context", "node_key": "context"},
            {"alias": "messages.deliver", "node_key": "deliver"},
            {"alias": "text.draft", "node_key": "draft"},
            {"alias": "text.normalize", "node_key": "normalize"},
        ],
        "resolved_nodes": [
            {"key": "audit", "module_id": "demo.audit", "effects": []},
            {"key": "context", "module_id": "demo.context", "effects": []},
            {
                "key": "deliver",
                "module_id": "demo.deliver",
                "effects": [{"name": "messages.deliver"}],
            },
            {"key": "draft", "module_id": "demo.draft", "effects": []},
            {"key": "normalize", "module_id": "demo.normalize", "effects": []},
        ],
    }


def test_real_resolved_signature_normalizes_to_versioned_plan_artifact() -> None:
    artifact = plan_artifact_from_resolved_signature(_resolved_signature())

    payload = artifact.to_dict()
    assert payload["schema_version"] == PLAN_SCHEMA_VERSION
    assert payload["plan_id"] == "request-plan"
    assert payload["source_signature_version"] == "0.3.0"
    assert payload["signature"]["effectful_modules"] == ["demo.deliver"]
    assert payload["signature"]["required_grant"] == {
        "capabilities": ["records.context.read"],
        "effects": ["messages.deliver"],
    }
    assert payload["trusted_context"]["alias_provenance"][0] == {
        "alias": "text.audit",
        "node_key": "audit",
    }
    assert PlanArtifact.from_dict(payload) == artifact
    assert plan_metric_values(artifact) == {
        "plan_depth": 4.0,
        "plan_fanout": 2.0,
        "plan_budget_cost_usd": 0.0,
        "plan_budget_model_tokens": 0.0,
        "plan_budget_tool_calls": 2.0,
        "plan_budget_wall_time_ms": 4000.0,
    }
    schema = json.loads(
        (ROOT / "schemas" / "plan-artifact.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)


def test_plan_artifact_id_ignores_alias_provenance_but_tracks_behavior() -> None:
    original = _resolved_signature()
    alias_only = copy.deepcopy(original)
    alias_only["alias_provenance"][0]["alias"] = "cosmetic.audit.alias"
    deeper = copy.deepcopy(original)
    deeper["max_depth"] = 5

    original_artifact = plan_artifact_from_resolved_signature(original)
    alias_artifact = plan_artifact_from_resolved_signature(alias_only)
    deeper_artifact = plan_artifact_from_resolved_signature(deeper)

    assert alias_artifact.artifact_id == original_artifact.artifact_id
    assert alias_artifact.to_dict() != original_artifact.to_dict()
    assert deeper_artifact.artifact_id != original_artifact.artifact_id


def test_plan_artifact_fails_closed_on_grant_escalation_and_tampering() -> None:
    escalated = _resolved_signature()
    escalated["required_grant"]["capabilities"].append("admin.delete")

    with pytest.raises(PlanContractError) as excinfo:
        plan_artifact_from_resolved_signature(escalated)
    assert excinfo.value.code == "PLAN_REQUIRED_GRANT_EXCEEDS_REGION"

    artifact = plan_artifact_from_resolved_signature(_resolved_signature()).to_dict()
    artifact["signature"]["max_fanout"] = 20
    with pytest.raises(PlanContractError) as excinfo:
        PlanArtifact.from_dict(artifact)
    assert excinfo.value.code == "PLAN_ARTIFACT_DIGEST_MISMATCH"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.pop("topology_digest"),
        lambda value: value.update(node_count=4),
        lambda value: value["resolved_nodes"][0].update(module_id="unknown.module"),
        lambda value: value["resolved_nodes"][0].update(effects=None),
        lambda value: value["resolved_nodes"][0].update(effects=[{}]),
        lambda value: value["module_composition"].append(
            copy.deepcopy(value["module_composition"][0])
        ),
        lambda value: value["required_grant"].update(capabilities=[""]),
        lambda value: value["aggregate_budget"].update(cost_usd=-1),
        lambda value: value.update(max_depth=True),
        lambda value: value.update(topology_digest="not-a-digest"),
        lambda value: value.update(alias_provenance=None),
        lambda value: value["alias_provenance"].append(
            copy.deepcopy(value["alias_provenance"][0])
        ),
        lambda value: value.update(approval_requirements=None),
        lambda value: value.update(
            approval_requirements=[
                {"effect_name": "admin.delete", "node_key": "deliver"}
            ]
        ),
    ],
)
def test_plan_artifact_rejects_malformed_resolved_signatures(mutate) -> None:
    signature = _resolved_signature()
    mutate(signature)

    with pytest.raises(PlanContractError):
        plan_artifact_from_resolved_signature(signature)


def test_plan_artifact_rejects_unsupported_and_noncanonical_serialization() -> None:
    artifact = plan_artifact_from_resolved_signature(_resolved_signature()).to_dict()
    unsupported = copy.deepcopy(artifact)
    unsupported["schema_version"] = "9.0.0"
    with pytest.raises(PlanContractError) as excinfo:
        PlanArtifact.from_dict(unsupported)
    assert excinfo.value.code == "PLAN_ARTIFACT_VERSION_UNSUPPORTED"

    noncanonical = copy.deepcopy(artifact)
    noncanonical["trusted_context"]["alias_provenance"].reverse()
    with pytest.raises(PlanContractError) as excinfo:
        PlanArtifact.from_dict(noncanonical)
    assert excinfo.value.code == "PLAN_ARTIFACT_NON_CANONICAL"


def test_plan_artifact_rejects_malformed_serialized_fields() -> None:
    with pytest.raises(PlanContractError, match="must be an object"):
        plan_artifact_from_resolved_signature(None)  # type: ignore[arg-type]

    artifact = plan_artifact_from_resolved_signature(_resolved_signature()).to_dict()
    malformed = []
    missing_field = copy.deepcopy(artifact)
    missing_field["signature"].pop("topology_digest")
    malformed.append(missing_field)
    non_array_grants = copy.deepcopy(artifact)
    non_array_grants["signature"]["required_grant"]["capabilities"] = None
    malformed.append(non_array_grants)
    duplicate_grants = copy.deepcopy(artifact)
    duplicate_grants["signature"]["required_grant"]["capabilities"] *= 2
    malformed.append(duplicate_grants)
    non_array_digests = copy.deepcopy(artifact)
    non_array_digests["signature"]["output_schema_digests"] = None
    malformed.append(non_array_digests)
    empty_modules = copy.deepcopy(artifact)
    empty_modules["signature"]["module_composition"] = []
    malformed.append(empty_modules)

    for payload in malformed:
        with pytest.raises(PlanContractError):
            PlanArtifact.from_dict(payload)


def test_policy_2_1_loads_only_the_plan_rules_the_guardrail_will_evaluate(
    tmp_path,
) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        """\
version: 2.1
metrics:
  plan_depth: {kind: measured, direction: upper, limit: 4}
  plan_fanout: {kind: measured, direction: upper, limit: 2}
  plan_budget_cost_usd: {kind: measured, direction: upper, limit: 1.5}
  plan_budget_model_tokens: {kind: measured, direction: upper, limit: 2000}
  plan_budget_tool_calls: {kind: measured, direction: upper, limit: 10}
  plan_budget_wall_time_ms: {kind: measured, direction: upper, limit: 10000}
  plan_effectful_modules:
    kind: invariant
    allowed: [demo.deliver]
    none_of: [untrusted.shell]
  plan_grants:
    kind: invariant
    allowed: [records.context.read, messages.deliver]
    approval_required_for: [messages.deliver]
""",
        encoding="utf-8",
    )

    policy = load_policy(path)

    assert policy.policy_version == (2, 1)
    assert policy.metrics["plan_effectful_modules"].allowed == ("demo.deliver",)
    assert policy.metrics["plan_grants"].approval_required_for == ("messages.deliver",)
    artifact = plan_artifact_from_resolved_signature(_resolved_signature())
    assert plan_invariant_outcomes(artifact, policy) == {
        "plan_effectful_modules": True,
        "plan_grants": False,
    }


def test_policy_2_0_rejects_plan_metrics_that_require_2_1(tmp_path) -> None:
    path = tmp_path / "policy.yaml"
    path.write_text(
        "version: 2.0\nmetrics:\n  plan_depth: {kind: measured, direction: upper, limit: 4}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="requires policy version 2.1"):
        load_policy(path)


def test_report_carries_typed_plan_validation_and_graph_diff_evidence() -> None:
    artifact = plan_artifact_from_resolved_signature(_resolved_signature())
    evidence = PlanEvidence(
        artifact=artifact,
        valid=False,
        issues=(
            PlanValidationIssue(
                code="PLAN_FANOUT_EXCEEDED",
                message="Plan fan-out 2 exceeds the allowed maximum 1.",
                location="signature.max_fanout",
            ),
        ),
        graph_changes=(
            PlanGraphChange(
                kind=PlanDiffKind.TOPOLOGY_CHANGED,
                location="signature.topology_digest",
                before=_digest("0"),
                after=_digest("f"),
                resolvable=False,
            ),
        ),
        trial=1,
    )

    payload = TrialRunReport(trials_requested=1, plan_evidence=[evidence]).to_dict()

    assert payload["report_version"] == REPORT_SCHEMA_VERSION
    assert payload["plan_evidence"] == [evidence.to_dict()]
    assert payload["plan_evidence"][0]["checked_before_execution"] is True
    assert payload["plan_evidence"][0]["graph_changes"][0]["kind"] == (
        "TOPOLOGY_CHANGED"
    )
    schema = json.loads(
        (ROOT / "schemas" / "statistical-gate-report.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema["properties"]["plan_evidence"]["items"]).validate(
        evidence.to_dict()
    )


def test_plan_evidence_rejects_inconsistent_or_post_execution_claims() -> None:
    artifact = plan_artifact_from_resolved_signature(_resolved_signature())
    issue = PlanValidationIssue(code="PLAN_REJECTED", message="Rejected.")

    with pytest.raises(PlanContractError, match="requires an artifact"):
        PlanEvidence(artifact=None, valid=True)
    with pytest.raises(PlanContractError, match="requires an issue"):
        PlanEvidence(artifact=artifact, valid=False)
    with pytest.raises(PlanContractError, match="cannot contain issues"):
        PlanEvidence(artifact=artifact, valid=True, issues=(issue,))
    with pytest.raises(PlanContractError, match="trial must be at least 1"):
        PlanEvidence(artifact=artifact, valid=True, trial=0)

    serialized = PlanEvidence(artifact=artifact, valid=True).to_dict()
    serialized["checked_before_execution"] = False
    with pytest.raises(PlanContractError, match="before execution"):
        PlanEvidence.from_dict(serialized)

    serialized = PlanEvidence(artifact=artifact, valid=True).to_dict()
    serialized["graph_changes"] = [
        {
            "kind": "UNKNOWN",
            "location": "signature",
            "before": None,
            "after": None,
            "resolvable": False,
        }
    ]
    with pytest.raises(PlanContractError, match="unknown plan graph change"):
        PlanEvidence.from_dict(serialized)

    serialized["graph_changes"][0]["kind"] = "TOPOLOGY_CHANGED"
    serialized["graph_changes"][0]["resolvable"] = "no"
    with pytest.raises(PlanContractError, match="resolvable must be boolean"):
        PlanEvidence.from_dict(serialized)

    issue_with_location = PlanValidationIssue(
        code="PLAN_REJECTED", message="Rejected.", location="signature.max_depth"
    )
    assert (
        PlanValidationIssue.from_dict(issue_with_location.to_dict())
        == issue_with_location
    )


def test_baseline_adds_a_deduplicated_plan_population() -> None:
    artifact = plan_artifact_from_resolved_signature(_resolved_signature())
    evidence = PlanEvidence(artifact=artifact, valid=True)
    trace_signature = {
        "tool_path": [],
        "tool_call_sequence": [],
        "tool_call_counts": {},
        "llm_models_used": [],
        "event_type_sequence": ["RUN_START", "RUN_END"],
        "final_status": "ok",
    }
    report = {
        "report_version": REPORT_SCHEMA_VERSION,
        "metadata": {
            "trials_used": 2,
            "trials_budgeted": 2,
            "environment_fingerprint": {},
        },
        "trials": [
            {
                "trace_id": f"{index:032x}",
                "run_name": "planned-task",
                "metric_values": {},
                "invariant_outcomes": {},
                "structural_signature": trace_signature,
            }
            for index in (1, 2)
        ],
        "plan_evidence": [{**evidence.to_dict(), "trial": index} for index in (1, 2)],
    }

    baseline = create_baseline_from_report(report)

    assert baseline["schema_version"] == BASELINE_SCHEMA_VERSION
    assert baseline["plan_sample"] == {
        "plans": 2,
        "plan_id": "request-plan",
        "artifact_ids": [artifact.artifact_id, artifact.artifact_id],
        "artifact_counts": {artifact.artifact_id: 2},
        "artifacts": {artifact.artifact_id: artifact.to_dict()},
        "metrics": {
            name: [value, value] for name, value in plan_metric_values(artifact).items()
        },
        "sets": {
            "plan_effectful_modules": [["demo.deliver"], ["demo.deliver"]],
            "plan_grants": [
                ["messages.deliver", "records.context.read"],
                ["messages.deliver", "records.context.read"],
            ],
        },
    }
    schema = json.loads(
        (ROOT / "schemas" / "baseline.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(baseline)
