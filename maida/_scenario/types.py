"""Scenario types, statuses, and aggregate run reports."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from maida._assertions.types import AssertionPolicy
from maida.evaluation import StoredRunEvaluation


class ScenarioInputError(ValueError):
    """A manifest or local preflight requirement is invalid."""


class ScenarioStatus(str, Enum):
    """Stable per-scenario outcomes."""

    PASS = "pass"
    ASSERTION_FAILED = "assertion_failed"
    AGENT_FAILED = "agent_failed"


@dataclass(frozen=True)
class ClaudeScenarioConfig:
    executable: str
    version: str
    model: str
    settings: Path
    mcp_config: Path
    timeout_seconds: float
    max_budget_usd: float
    max_turns: int
    allowed_tools: tuple[str, ...]


@dataclass(frozen=True)
class WorkspaceFixture:
    root: Path
    files: tuple[Path, ...]


@dataclass(frozen=True)
class ScenarioDefinition:
    scenario_id: str
    fixture: WorkspaceFixture
    prompt: str
    baseline_path: Path
    baseline: dict[str, Any]
    policy_path: Path | None
    policy: AssertionPolicy


@dataclass(frozen=True)
class ScenarioManifest:
    path: Path
    project_root: Path
    claude: ClaudeScenarioConfig
    scenarios: tuple[ScenarioDefinition, ...]


@dataclass(frozen=True)
class ClaudeProcessOutcome:
    """Only sanitized process facts; raw streams never leave the executor."""

    returncode: int
    timed_out: bool
    cost_usd: float | None


@dataclass(frozen=True)
class ScenarioResult:
    scenario_id: str
    status: ScenarioStatus
    trace_id: str | None = None
    cost_usd: float | None = None
    failure_reason: str | None = None
    process_exit_code: int | None = None
    evaluation: StoredRunEvaluation | Any | None = None
    baseline_path: Path | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "scenario_id": self.scenario_id,
            "status": self.status.value,
            "trace_id": self.trace_id,
            "cost_usd": self.cost_usd,
        }
        if self.failure_reason is not None:
            result["failure_reason"] = self.failure_reason
        if self.process_exit_code is not None:
            result["process_exit_code"] = self.process_exit_code
        if self.evaluation is not None:
            result["assertions"] = json.loads(self.evaluation.render("json"))
        return result


@dataclass(frozen=True)
class ScenarioRunReport:
    results: list[ScenarioResult]

    @property
    def exit_code(self) -> int:
        if any(item.status is ScenarioStatus.AGENT_FAILED for item in self.results):
            return 10
        if any(item.status is ScenarioStatus.ASSERTION_FAILED for item in self.results):
            return 1
        return 0

    @property
    def status(self) -> str:
        return {
            0: ScenarioStatus.PASS.value,
            1: ScenarioStatus.ASSERTION_FAILED.value,
            10: ScenarioStatus.AGENT_FAILED.value,
        }[self.exit_code]

    def render(self, output_format: str = "text") -> str:
        if output_format == "json":
            return json.dumps(
                {
                    "manifest_version": 1,
                    "status": self.status,
                    "scenarios": [item.as_dict() for item in self.results],
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        if output_format == "text":
            lines = [f"SCENARIOS: {self.status.upper()}"]
            for item in self.results:
                details = [item.status.value]
                if item.trace_id:
                    details.append(f"trace={item.trace_id}")
                if item.cost_usd is not None:
                    details.append(f"cost_usd={item.cost_usd:g}")
                if item.failure_reason:
                    details.append(f"reason={item.failure_reason}")
                lines.append(f"[{item.scenario_id}] " + " ".join(details))
                if item.evaluation is not None:
                    lines.append(item.evaluation.render("text", baseline_path=item.baseline_path))
            return "\n".join(lines)
        if output_format == "markdown":
            lines = [
                f"## Maida scenarios: {self.status}",
                "",
                "| Scenario | Status | Trace | Cost (USD) | Reason |",
                "|---|---|---|---:|---|",
            ]
            for item in self.results:
                lines.append(
                    f"| `{item.scenario_id}` | {item.status.value} | "
                    f"`{item.trace_id or '-'}` | "
                    f"{item.cost_usd if item.cost_usd is not None else '-'} | "
                    f"{item.failure_reason or '-'} |"
                )
            for item in self.results:
                if item.evaluation is None:
                    continue
                lines.extend(
                    [
                        "",
                        f"### `{item.scenario_id}`",
                        "",
                        item.evaluation.render("markdown", baseline_path=item.baseline_path),
                    ]
                )
            return "\n".join(lines)
        raise ValueError("output format must be text, json, or markdown")
