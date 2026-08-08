"""Isolated, capture-backed scenario execution for headless Claude Code."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import uvicorn
import yaml

from maida.assertions import AssertionPolicy
from maida.baseline import load_baseline
from maida.capture.claude_code import create_claude_code_app
from maida.config import MaidaConfig, load_config
from maida.evaluation import (
    StoredRunEvaluation,
    evaluate_stored_run_against_baseline,
)
from maida.integrations.claude_code import import_claude_capture
from maida.policy import load_policy


DEFAULT_SCENARIO_MANIFEST = Path(".maida/scenarios.yaml")
_SEMVER_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")
_MODEL_RE = re.compile(
    r"^claude-(?:(?:haiku|opus|sonnet)-\d+(?:-\d+)+"
    r"|\d+(?:-\d+)*-(?:haiku|opus|sonnet)-\d{8})$"
)
_MODEL_ALIASES = frozenset(
    {
        "claude",
        "claude-haiku",
        "claude-opus",
        "claude-sonnet",
        "claude-test",
        "default",
        "haiku",
        "opus",
        "sonnet",
    }
)
_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "COMSPEC",
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NO_PROXY",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TERM",
        "TMPDIR",
        "USER",
    }
)


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
                    lines.append(
                        item.evaluation.render("text", baseline_path=item.baseline_path)
                    )
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
                        item.evaluation.render(
                            "markdown", baseline_path=item.baseline_path
                        ),
                    ]
                )
            return "\n".join(lines)
        raise ValueError("output format must be text, json, or markdown")


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScenarioInputError(f"{field} must be an object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ScenarioInputError(
            f"{field} contains unknown field(s): {', '.join(unknown)}"
        )


def _required_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScenarioInputError(f"{field} must be a nonempty string")
    return value.strip()


def _positive_number(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
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
        raise ScenarioInputError(
            "scenario project must be a readable Git worktree"
        ) from exc
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
    if isinstance(permissions, dict) and permissions.get("defaultMode") == (
        "bypassPermissions"
    ):
        raise ScenarioInputError("claude.settings must not bypass permissions")
    if settings.get("dangerouslySkipPermissions"):
        raise ScenarioInputError("claude.settings must not bypass permissions")
    if "hooks" in settings:
        raise ScenarioInputError("claude.settings must not install undeclared hooks")
    if "env" in settings:
        raise ScenarioInputError(
            "claude.settings must not override the runner environment"
        )


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
        raise ScenarioInputError(
            f"scenario manifest is not valid YAML: {path}"
        ) from exc
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
    executable = _required_string(
        claude_data.get("executable", "claude"), "claude.executable"
    )
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
    timeout_seconds = _positive_number(
        claude_data.get("timeout_seconds"), "claude.timeout_seconds"
    )
    max_budget_usd = _positive_number(
        claude_data.get("max_budget_usd"), "claude.max_budget_usd"
    )
    max_turns = claude_data.get("max_turns")
    if isinstance(max_turns, bool) or not isinstance(max_turns, int) or max_turns < 1:
        raise ScenarioInputError("claude.max_turns must be a positive integer")
    tools = claude_data.get("allowed_tools")
    if (
        not isinstance(tools, list)
        or not tools
        or not all(isinstance(item, str) and item.strip() for item in tools)
    ):
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
        fixture_root = _relative_path(
            fixture_data.get("root"), f"{field}.fixture.root", allow_dot=True
        )
        files_data = fixture_data.get("files")
        if not isinstance(files_data, list) or not files_data:
            raise ScenarioInputError(f"{field}.fixture.files must be a nonempty list")
        files = tuple(
            _relative_path(value, f"{field}.fixture.files") for value in files_data
        )
        if len(set(files)) != len(files):
            raise ScenarioInputError(f"{field}.fixture.files contains duplicates")
        if settings not in files or mcp_config not in files:
            raise ScenarioInputError(
                f"{field}.fixture.files must declare Claude settings and MCP config"
            )
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

        baseline_relative = _relative_path(
            scenario.get("baseline"), f"{field}.baseline"
        )
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


def _filtered_environment(
    endpoint: str,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = source if source is not None else os.environ
    filtered = {
        key: value
        for key, value in environment.items()
        if key.upper() in _SAFE_ENVIRONMENT_KEYS
    }
    filtered.update(
        {
            "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
            "CLAUDE_CODE_ENHANCED_TELEMETRY_BETA": "1",
            "CLAUDE_CODE_PROPAGATE_TRACEPARENT": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "OTEL_METRICS_EXPORTER": "none",
            "OTEL_LOGS_EXPORTER": "otlp",
            "OTEL_TRACES_EXPORTER": "otlp",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_LOGS_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL": "http/protobuf",
            "OTEL_EXPORTER_OTLP_ENDPOINT": endpoint,
            "OTEL_EXPORTER_OTLP_LOGS_ENDPOINT": f"{endpoint}/v1/logs",
            "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{endpoint}/v1/traces",
            "OTEL_LOGS_EXPORT_INTERVAL": "100",
            "OTEL_TRACES_EXPORT_INTERVAL": "100",
            "OTEL_LOG_USER_PROMPTS": "0",
            "OTEL_LOG_ASSISTANT_RESPONSES": "0",
            "OTEL_LOG_TOOL_CONTENT": "0",
            "OTEL_LOG_TOOL_DETAILS": "0",
        }
    )
    return filtered


def _parse_cost(stdout: str) -> float | None:
    try:
        value = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    cost = value.get("total_cost_usd", value.get("cost_usd"))
    if (
        isinstance(cost, bool)
        or not isinstance(cost, (int, float))
        or not math.isfinite(cost)
        or cost < 0
    ):
        return None
    return float(cost)


def _stop_process_group(process: subprocess.Popen[str], *, force: bool) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        if force:
            process.kill()
        else:
            process.terminate()
        return
    group = os.getpgid(process.pid)
    os.killpg(group, signal.SIGKILL if force else signal.SIGTERM)


def _run_claude_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: float,
) -> ClaudeProcessOutcome:
    """Run one process group and discard its streams after parsing cost."""
    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    )
    process = subprocess.Popen(
        list(argv),
        cwd=cwd,
        env=dict(env),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=os.name != "nt",
        creationflags=creationflags,
    )
    timed_out = False
    stdout = ""
    try:
        stdout, _stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_process_group(process, force=False)
        try:
            stdout, _stderr = process.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            _stop_process_group(process, force=True)
            stdout, _stderr = process.communicate()
    return ClaudeProcessOutcome(
        returncode=process.returncode if process.returncode is not None else -1,
        timed_out=timed_out,
        cost_usd=_parse_cost(stdout),
    )


@contextmanager
def _claude_receiver(config: MaidaConfig) -> Iterator[str]:
    """Run an ephemeral loopback OTLP receiver for one scenario."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(
        uvicorn.Config(
            create_claude_code_app(config),
            log_level="error",
            lifespan="off",
            access_log=False,
        )
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"sockets": [listener]},
        name=f"maida-claude-receiver-{port}",
        daemon=True,
    )
    thread.start()
    deadline = time.monotonic() + 5.0
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    if not server.started:
        server.should_exit = True
        thread.join(timeout=1.0)
        listener.close()
        raise RuntimeError("Claude Code capture receiver did not start")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=1.0)
        listener.close()


def _copy_fixture(fixture: WorkspaceFixture, workspace: Path) -> None:
    for relative in fixture.files:
        source = fixture.root / relative
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _claude_argv(
    config: ClaudeScenarioConfig,
    scenario: ScenarioDefinition,
    workspace: Path,
    session_id: str,
) -> list[str]:
    return [
        config.executable,
        "-p",
        scenario.prompt,
        "--model",
        config.model,
        "--session-id",
        session_id,
        "--output-format",
        "json",
        "--settings",
        str(workspace / config.settings),
        "--setting-sources",
        "project",
        "--strict-mcp-config",
        "--mcp-config",
        str(workspace / config.mcp_config),
        "--tools",
        ",".join(config.allowed_tools),
        "--allowedTools",
        ",".join(config.allowed_tools),
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--max-budget-usd",
        f"{config.max_budget_usd:g}",
        "--max-turns",
        str(config.max_turns),
    ]


def _preflight_version(
    manifest: ScenarioManifest,
    *,
    version_runner: Callable[..., subprocess.CompletedProcess[str]],
    environment: Mapping[str, str] | None,
) -> None:
    env = _filtered_environment("http://127.0.0.1:1", environment)
    try:
        completed = version_runner(
            [manifest.claude.executable, "--version"],
            cwd=manifest.project_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=min(10.0, manifest.claude.timeout_seconds),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ScenarioInputError(
            f"Claude Code executable is unavailable: {manifest.claude.executable}"
        ) from exc
    match = _SEMVER_RE.search(completed.stdout or "")
    actual = match.group(1) if match else None
    if completed.returncode != 0 or actual != manifest.claude.version:
        raise ScenarioInputError(
            f"manifest requires Claude Code {manifest.claude.version}; "
            f"installed version is {actual or 'unknown'}"
        )


def run_scenario_manifest(
    manifest: ScenarioManifest,
    *,
    config: MaidaConfig,
    scenario_id: str | None = None,
    environment: Mapping[str, str] | None = None,
    version_runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    process_runner: Callable[..., ClaudeProcessOutcome] = _run_claude_process,
    receiver_factory: Callable[[MaidaConfig], Any] = _claude_receiver,
    capture_importer: Callable[..., Any] = import_claude_capture,
    evaluator: Callable[..., Any] = evaluate_stored_run_against_baseline,
    session_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
) -> ScenarioRunReport:
    """Preflight and execute selected scenarios without retaining agent streams."""
    selected = [
        scenario
        for scenario in manifest.scenarios
        if scenario_id is None or scenario.scenario_id == scenario_id
    ]
    if not selected:
        raise ScenarioInputError(f"scenario ID was not found: {scenario_id}")
    _preflight_version(manifest, version_runner=version_runner, environment=environment)

    results: list[ScenarioResult] = []
    for scenario in selected:
        session_id = session_id_factory()
        with tempfile.TemporaryDirectory(prefix="maida-claude-scenario-") as temp:
            workspace = Path(temp) / "workspace"
            workspace.mkdir()
            _copy_fixture(scenario.fixture, workspace)
            try:
                with receiver_factory(config) as endpoint:
                    outcome = process_runner(
                        _claude_argv(manifest.claude, scenario, workspace, session_id),
                        cwd=workspace,
                        env=_filtered_environment(endpoint, environment),
                        timeout_seconds=manifest.claude.timeout_seconds,
                    )
            except Exception:
                results.append(
                    ScenarioResult(
                        scenario_id=scenario.scenario_id,
                        status=ScenarioStatus.AGENT_FAILED,
                        failure_reason="runtime_failure",
                    )
                )
                continue
            if outcome.timed_out:
                results.append(
                    ScenarioResult(
                        scenario_id=scenario.scenario_id,
                        status=ScenarioStatus.AGENT_FAILED,
                        cost_usd=outcome.cost_usd,
                        failure_reason="timeout",
                        process_exit_code=outcome.returncode,
                    )
                )
                continue
            if (
                outcome.cost_usd is not None
                and outcome.cost_usd > manifest.claude.max_budget_usd
            ):
                results.append(
                    ScenarioResult(
                        scenario_id=scenario.scenario_id,
                        status=ScenarioStatus.AGENT_FAILED,
                        cost_usd=outcome.cost_usd,
                        failure_reason="budget_exceeded",
                        process_exit_code=outcome.returncode,
                    )
                )
                continue
            if outcome.returncode != 0:
                results.append(
                    ScenarioResult(
                        scenario_id=scenario.scenario_id,
                        status=ScenarioStatus.AGENT_FAILED,
                        cost_usd=outcome.cost_usd,
                        failure_reason="process_exit",
                        process_exit_code=outcome.returncode,
                    )
                )
                continue
            try:
                imported = capture_importer(session_id, config)
                evaluation = evaluator(
                    imported.trace_id,
                    scenario.baseline,
                    scenario.policy,
                    config,
                )
            except Exception:
                results.append(
                    ScenarioResult(
                        scenario_id=scenario.scenario_id,
                        status=ScenarioStatus.AGENT_FAILED,
                        cost_usd=outcome.cost_usd,
                        failure_reason="capture_import",
                        process_exit_code=outcome.returncode,
                    )
                )
                continue
            results.append(
                ScenarioResult(
                    scenario_id=scenario.scenario_id,
                    status=(
                        ScenarioStatus.PASS
                        if evaluation.passed
                        else ScenarioStatus.ASSERTION_FAILED
                    ),
                    trace_id=imported.trace_id,
                    cost_usd=outcome.cost_usd,
                    process_exit_code=outcome.returncode,
                    evaluation=evaluation,
                    baseline_path=scenario.baseline_path,
                )
            )
    return ScenarioRunReport(results=results)


def run_scenario_file(
    path: Path = DEFAULT_SCENARIO_MANIFEST,
    *,
    scenario_id: str | None = None,
    project_root: Path | None = None,
    config: MaidaConfig | None = None,
) -> ScenarioRunReport:
    """Load, preflight, and execute one scenario manifest."""
    root = (project_root or Path.cwd()).resolve()
    manifest = load_scenario_manifest(path, project_root=root)
    return run_scenario_manifest(
        manifest,
        config=config or load_config(),
        scenario_id=scenario_id,
    )
