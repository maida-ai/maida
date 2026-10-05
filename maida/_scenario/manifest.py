"""Load and validate scenario manifest v1 inputs."""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

import yaml

from maida._scenario.constants import _MODEL_ALIASES, _MODEL_RE, _SEMVER_RE
from maida._scenario.types import (
    ClaudeScenarioConfig,
    ScenarioDefinition,
    ScenarioInputError,
    ScenarioManifest,
    WorkspaceFixture,
)
from maida.assertions import AssertionPolicy
from maida.baseline import load_baseline
from maida.policy import load_policy


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioInputError(f"{field} must be an object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ScenarioInputError(f"{field} contains unknown field(s): {', '.join(unknown)}")


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioInputError(f"{field} must be a nonempty string")
    return value.strip()


def _positive_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ScenarioInputError(f"{field} must be a positive number")
    return float(value)


def _relative_path(value: object, field: str, *, allow_dot: bool = False) -> Path:
    text = _required_string(value, field)
    path = Path(text)
    if path.is_absolute() or ".." in path.parts or "\\" in text:
        raise ScenarioInputError(f"{field} must be a traversal-safe relative path")
    if not allow_dot and path == Path("."):
        raise ScenarioInputError(f"{field} must name a file or directory")
    return path


def _inside(root: Path, relative: Path, field: str) -> Path:
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ScenarioInputError(f"{field} escapes the project root") from exc
    return resolved


def _require_tracked(project_root: Path, path: Path, field: str) -> None:
    try:
        relative = path.relative_to(project_root)
    except ValueError as exc:
        raise ScenarioInputError(f"{field} escapes the project root") from exc
    try:
        completed = subprocess.run(
            ["git", "ls-files", "--error-unmatch", "--", relative.as_posix()],
            cwd=project_root,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScenarioInputError("scenario project must be a readable Git worktree") from exc
    if completed.returncode != 0 or not path.is_file() or path.is_symlink():
        raise ScenarioInputError(f"{field} must be a tracked file")


def _read_json_object(path: Path, field: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScenarioInputError(f"{field} must be a readable JSON object") from exc
    if not isinstance(value, dict):
        raise ScenarioInputError(f"{field} must be a JSON object")
    return value


def _validate_settings(path: Path) -> None:
    settings = _read_json_object(path, "claude.settings")
    permissions = settings.get("permissions")
    if isinstance(permissions, dict) and permissions.get("defaultMode") == ("bypassPermissions"):
        raise ScenarioInputError("claude.settings must not bypass permissions")
    if settings.get("dangerouslySkipPermissions"):
        raise ScenarioInputError("claude.settings must not bypass permissions")
    if "hooks" in settings:
        raise ScenarioInputError("claude.settings must not install undeclared hooks")
    if "env" in settings:
        raise ScenarioInputError("claude.settings must not override the runner environment")


def _validate_mcp(path: Path) -> None:
    config = _read_json_object(path, "claude.mcp_config")
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        raise ScenarioInputError("claude.mcp_config must contain an mcpServers object")


def _load_manifest_yaml(path: Path) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ScenarioInputError(f"scenario manifest not found: {path}") from exc
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ScenarioInputError(f"scenario manifest is not valid YAML: {path}") from exc
    return _mapping(value, "manifest")


def _load_baseline(path: Path) -> dict[str, Any]:
    try:
        return load_baseline(path)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise ScenarioInputError(f"scenario baseline is invalid: {path}") from exc


def _load_policy(path: Path | None) -> AssertionPolicy:
    if path is None:
        return AssertionPolicy()
    try:
        return load_policy(path)
    except (OSError, UnicodeError, ValueError, yaml.YAMLError) as exc:
        raise ScenarioInputError(f"scenario policy is invalid: {path}") from exc


def load_scenario_manifest(
    path: Path,
    *,
    project_root: Path | None = None,
) -> ScenarioManifest:
    """Load manifest v1 and validate all reproducibility/safety inputs."""
    root = (project_root or Path.cwd()).resolve()
    manifest_path = path if path.is_absolute() else root / path
    manifest_path = manifest_path.resolve()
    data = _load_manifest_yaml(manifest_path)
    _reject_unknown(data, {"version", "claude", "scenarios"}, "manifest")
    if data.get("version") != 1:
        raise ScenarioInputError("manifest.version must be 1")

    claude_data = _mapping(data.get("claude"), "claude")
    _reject_unknown(
        claude_data,
        {
            "executable",
            "version",
            "model",
            "settings",
            "mcp_config",
            "timeout_seconds",
            "max_budget_usd",
            "max_turns",
            "allowed_tools",
        },
        "claude",
    )
    executable = _required_string(claude_data.get("executable", "claude"), "claude.executable")
    if Path(executable).name != executable:
        raise ScenarioInputError("claude.executable must be a command name")
    version = _required_string(claude_data.get("version"), "claude.version")
    if _SEMVER_RE.fullmatch(version) is None:
        raise ScenarioInputError("claude.version must be an exact semantic version")
    model = _required_string(claude_data.get("model"), "claude.model")
    if model in _MODEL_ALIASES or _MODEL_RE.fullmatch(model) is None:
        raise ScenarioInputError("claude.model must be a full Claude model ID")
    settings = _relative_path(claude_data.get("settings"), "claude.settings")
    mcp_config = _relative_path(claude_data.get("mcp_config"), "claude.mcp_config")
    timeout_seconds = _positive_number(claude_data.get("timeout_seconds"), "claude.timeout_seconds")
    max_budget_usd = _positive_number(claude_data.get("max_budget_usd"), "claude.max_budget_usd")
    max_turns = claude_data.get("max_turns")
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
        raise ScenarioInputError("claude.max_turns must be a positive integer")
    tools = claude_data.get("allowed_tools")
    if not isinstance(tools, list) or not tools or not all(isinstance(item, str) and item.strip() for item in tools):
        raise ScenarioInputError("claude.allowed_tools must be a nonempty string list")
    allowed_tools = tuple(item.strip() for item in tools)
    if len(set(allowed_tools)) != len(allowed_tools):
        raise ScenarioInputError("claude.allowed_tools must not contain duplicates")
    if any("*" in item for item in allowed_tools):
        raise ScenarioInputError("claude.allowed_tools must not contain wildcards")

    scenarios_data = data.get("scenarios")
    if not isinstance(scenarios_data, list) or not scenarios_data:
        raise ScenarioInputError("manifest.scenarios must be a nonempty list")
    scenarios: list[ScenarioDefinition] = []
    ids: list[str] = []
    for index, raw in enumerate(scenarios_data):
        field = f"scenarios[{index}]"
        scenario = _mapping(raw, field)
        _reject_unknown(
            scenario,
            {"id", "fixture", "prompt", "baseline", "policy"},
            field,
        )
        scenario_id = _required_string(scenario.get("id"), f"{field}.id")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", scenario_id):
            raise ScenarioInputError(f"{field}.id has an invalid value")
        ids.append(scenario_id)
        fixture_data = _mapping(scenario.get("fixture"), f"{field}.fixture")
        _reject_unknown(fixture_data, {"root", "files"}, f"{field}.fixture")
        fixture_root = _relative_path(fixture_data.get("root"), f"{field}.fixture.root", allow_dot=True)
        files_data = fixture_data.get("files")
        if not isinstance(files_data, list) or not files_data:
            raise ScenarioInputError(f"{field}.fixture.files must be a nonempty list")
        files = tuple(_relative_path(value, f"{field}.fixture.files") for value in files_data)
        if len(set(files)) != len(files):
            raise ScenarioInputError(f"{field}.fixture.files contains duplicates")
        if settings not in files or mcp_config not in files:
            raise ScenarioInputError(f"{field}.fixture.files must declare Claude settings and MCP config")
        source_root = _inside(root, fixture_root, f"{field}.fixture.root")
        for relative in files:
            _require_tracked(
                root,
                _inside(source_root, relative, f"{field}.fixture.files"),
                f"{field}.fixture tracked file",
            )
        settings_source = _inside(source_root, settings, "claude.settings")
        mcp_source = _inside(source_root, mcp_config, "claude.mcp_config")
        _validate_settings(settings_source)
        _validate_mcp(mcp_source)

        baseline_relative = _relative_path(scenario.get("baseline"), f"{field}.baseline")
        baseline_path = _inside(root, baseline_relative, f"{field}.baseline")
        _require_tracked(root, baseline_path, f"{field}.baseline")
        policy_path: Path | None = None
        if scenario.get("policy") is not None:
            policy_relative = _relative_path(scenario.get("policy"), f"{field}.policy")
            policy_path = _inside(root, policy_relative, f"{field}.policy")
            _require_tracked(root, policy_path, f"{field}.policy")
        scenarios.append(
            ScenarioDefinition(
                scenario_id=scenario_id,
                fixture=WorkspaceFixture(root=source_root, files=files),
                prompt=_required_string(scenario.get("prompt"), f"{field}.prompt"),
                baseline_path=baseline_path,
                baseline=_load_baseline(baseline_path),
                policy_path=policy_path,
                policy=_load_policy(policy_path),
            )
        )
    if len(set(ids)) != len(ids):
        raise ScenarioInputError("scenario IDs must be unique")

    return ScenarioManifest(
        path=manifest_path,
        project_root=root,
        claude=ClaudeScenarioConfig(
            executable=executable,
            version=version,
            model=model,
            settings=settings,
            mcp_config=mcp_config,
            timeout_seconds=timeout_seconds,
            max_budget_usd=max_budget_usd,
            max_turns=max_turns,
            allowed_tools=allowed_tools,
        ),
        scenarios=tuple(scenarios),
    )
