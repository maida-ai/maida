"""Typed pre-execution plan validation and comparison evidence."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from maida._plan_contract.artifact import PlanArtifact
from maida._plan_contract.errors import _fail
from maida._plan_contract.parsing import _exact_fields, _mapping, _string


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
        data = _mapping(value, "plan graph change")
        _exact_fields(
            data,
            {"kind", "location", "before", "after", "resolvable"},
            "plan graph change",
            code="PLAN_EVIDENCE_INVALID",
        )
        try:
            kind = PlanDiffKind(data.get("kind"))
        except (TypeError, ValueError) as error:
            raise _fail("PLAN_EVIDENCE_INVALID", "unknown plan graph change kind") from error
        resolvable = data.get("resolvable")
        if not isinstance(resolvable, bool):
            raise _fail("PLAN_EVIDENCE_INVALID", "graph change resolvable must be boolean")
        return cls(
            kind=kind,
            location=_string(data.get("location"), "graph change location"),
            before=data.get("before"),
            after=data.get("after"),
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
        data = _mapping(value, "plan validation issue")
        expected = {"code", "message"}
        if "location" in data:
            expected.add("location")
        _exact_fields(
            data,
            expected,
            "plan validation issue",
            code="PLAN_EVIDENCE_INVALID",
        )
        location = data.get("location")
        if location is not None:
            location = _string(location, "plan issue location")
        return cls(
            code=_string(data.get("code"), "plan issue code"),
            message=_string(data.get("message"), "plan issue message"),
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
            isinstance(self.trial, bool) or not isinstance(self.trial, int) or self.trial < 1
        ):
            raise _fail("PLAN_EVIDENCE_INVALID", "plan evidence trial must be at least 1")
        if self.valid and self.artifact is None:
            raise _fail("PLAN_EVIDENCE_INVALID", "valid plan evidence requires an artifact")
        if self.valid and self.issues:
            raise _fail("PLAN_EVIDENCE_INVALID", "valid plan evidence cannot contain issues")
        if not self.valid and not self.issues:
            raise _fail("PLAN_EVIDENCE_INVALID", "invalid plan evidence requires an issue")

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
        _exact_fields(
            data,
            {
                "trial",
                "checked_before_execution",
                "valid",
                "artifact",
                "issues",
                "graph_changes",
            },
            "plan evidence",
            code="PLAN_EVIDENCE_INVALID",
        )
        artifact_data = data.get("artifact")
        artifact = None
        if artifact_data is not None:
            artifact = PlanArtifact.from_dict(_mapping(artifact_data, "plan evidence artifact"))
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
            issues=tuple(PlanValidationIssue.from_dict(_mapping(item, "plan evidence issue")) for item in issues_data),
            graph_changes=tuple(
                PlanGraphChange.from_dict(_mapping(item, "plan graph change")) for item in changes_data
            ),
            trial=data.get("trial"),
        )
