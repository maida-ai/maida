"""Execute Claude Code scenarios against an ephemeral capture receiver."""

from __future__ import annotations

import json
import math
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

import uvicorn

from maida._scenario.constants import DEFAULT_SCENARIO_MANIFEST, _SAFE_ENVIRONMENT_KEYS, _SEMVER_RE
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
from maida.capture.claude_code import create_claude_code_app
from maida.config import MaidaConfig, load_config
from maida.evaluation import evaluate_stored_run_against_baseline
from maida.integrations.claude_code import import_claude_capture


def _filtered_environment(
    endpoint: str,
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    environment = source if source is not None else os.environ
    filtered = {key: value for key, value in environment.items() if key.upper() in _SAFE_ENVIRONMENT_KEYS}
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
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
        return None
    return float(cost)


def _stop_process_group(process: subprocess.Popen[str], *, force: bool) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        # terminate()/kill() only stop the parent on Windows. Tool children can
        # keep the capture pipes open, defeating communicate()'s timeout.
        try:
            result = subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
                check=False,
            )
        finally:
            if process.poll() is None:
                process.kill()
        if result.returncode != 0:
            raise OSError("Could not stop the Windows scenario process tree")
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
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
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
        raise ScenarioInputError(f"Claude Code executable is unavailable: {manifest.claude.executable}") from exc
    match = _SEMVER_RE.search(completed.stdout or "")
    actual = match.group(1) if match else None
    if completed.returncode != 0 or actual != manifest.claude.version:
        raise ScenarioInputError(
            f"manifest requires Claude Code {manifest.claude.version}; installed version is {actual or 'unknown'}"
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
        scenario for scenario in manifest.scenarios if scenario_id is None or scenario.scenario_id == scenario_id
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
            if outcome.cost_usd is not None and outcome.cost_usd > manifest.claude.max_budget_usd:
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
                    status=(ScenarioStatus.PASS if evaluation.passed else ScenarioStatus.ASSERTION_FAILED),
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
