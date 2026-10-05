"""Isolated, capture-backed scenario execution for headless Claude Code."""

from maida._scenario.constants import DEFAULT_SCENARIO_MANIFEST
from maida._scenario.executor import run_scenario_file, run_scenario_manifest
from maida._scenario.manifest import load_scenario_manifest
from maida._scenario.types import (
    ClaudeProcessOutcome,
    ClaudeScenarioConfig,
    ScenarioDefinition,
    ScenarioInputError,
    ScenarioManifest,
    ScenarioResult,
    ScenarioRunReport,
    ScenarioStatus,
    WorkspaceFixture,
)

__all__ = [
    "DEFAULT_SCENARIO_MANIFEST",
    "ClaudeProcessOutcome",
    "ClaudeScenarioConfig",
    "ScenarioDefinition",
    "ScenarioInputError",
    "ScenarioManifest",
    "ScenarioResult",
    "ScenarioRunReport",
    "ScenarioStatus",
    "WorkspaceFixture",
    "load_scenario_manifest",
    "run_scenario_file",
    "run_scenario_manifest",
]
