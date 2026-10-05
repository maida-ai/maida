"""Local installation pointers and first-run evidence, never policy configuration."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from maida.capture_setup import read_safe
from maida.capture.providers import PROVIDERS, attached_providers

if TYPE_CHECKING:
    from maida.config import MaidaConfig

LOCAL_POINTER = Path(".maida/local.json")
_PROJECT_ID = re.compile(r"^[0-9a-f]{32}$")


def repository_root(start: Path) -> Path | None:
    """Find the nearest checkout boundary without consulting remote identities."""
    start = start.resolve()
    for directory in (start, *start.parents):
        if (directory / ".git").exists():
            return directory
    return None


def installation(start: Path, *, command: str = "maida init") -> tuple[Path, dict] | None:
    root = repository_root(start)
    if root is None:
        return None
    path = root / LOCAL_POINTER
    raw = read_safe(path, command=command)
    if raw is None:
        return None
    try:
        pointer = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Invalid {path}. Move it aside and rerun {command} to create a new local identity.") from exc
    if (
        not isinstance(pointer, dict)
        or type(pointer.get("version")) is not int
        or pointer["version"] not in (1, 2)
        or not isinstance(pointer.get("project_id"), str)
        or not _PROJECT_ID.fullmatch(pointer["project_id"])
        or not _valid_providers(pointer)
    ):
        raise ValueError(f"Invalid {path}. Move it aside and rerun {command} to create a new local identity.")
    return root, pointer


def _valid_providers(pointer: dict) -> bool:
    if pointer["version"] == 1:
        return pointer.get("capture") == "claude-code" and (
            "enabled" not in pointer or isinstance(pointer["enabled"], bool)
        )
    providers = pointer.get("providers")
    return (
        isinstance(providers, dict)
        and bool(providers)
        and all(
            name in PROVIDERS and isinstance(state, dict) and type(state.get("enabled")) is bool
            for name, state in providers.items()
        )
    )


def _receipts(config: MaidaConfig, runtimes: set[str], *, command: str) -> list[dict]:
    receipts = []
    directory = config.data_dir / "onboarding"
    for path in directory.glob("*.json"):
        runtime = "codex" if path.name.startswith("codex-") else "claude-code"
        if runtime not in runtimes:
            continue
        try:
            receipt = json.loads(path.read_bytes())
            if not isinstance(receipt, dict) or not isinstance(receipt.get("started_at"), str):
                raise ValueError("invalid receipt")
            started = datetime.fromisoformat(receipt["started_at"].replace("Z", "+00:00"))
            if started.tzinfo is None:
                raise ValueError("receipt timestamp requires timezone")
            receipt = {**receipt, "_runtime": runtime, "_started": started}
            receipts.append(receipt)
        except (OSError, ValueError, UnicodeError) as exc:
            recovery = (
                "capture a new Codex runtime turn and let it finish"
                if runtime == "codex"
                else "capture a new Claude Code task, exit the session"
            )
            raise ValueError(
                f"Local capture state is unreadable at {path}. Preserve and move that receipt aside, then {recovery} "
                f"and rerun {command}."
            ) from exc
    return receipts


def onboarding_run(config: MaidaConfig, *, command: str = "maida check") -> str:
    """Legacy Claude session selection remains independent of other providers."""
    receipts = _receipts(config, {"claude-code"}, command=command)
    if not receipts:
        raise FileNotFoundError(
            "No Claude Code task captured in this repository. Start a new Claude Code session here, "
            f"run one bounded task, exit the session, then rerun {command}."
        )
    latest = max(receipts, key=lambda item: item["_started"])
    return _claude_run(latest, command=command)


def _claude_run(latest: dict, *, command: str) -> str:
    if latest.get("state") != "closed":
        raise FileNotFoundError(
            f"The newest Claude Code session is still open. Finish and exit that session, then rerun {command}."
        )
    if not latest.get("has_start") or not latest.get("complete_tools"):
        raise FileNotFoundError(
            "Capture is missing a complete task lifecycle or tool activity. Start a new Claude Code session, "
            f"run a bounded task that reads a repository file, exit, then rerun {command}."
        )
    trace_id = latest.get("trace_id")
    if not isinstance(trace_id, str) or not _PROJECT_ID.fullmatch(trace_id):
        raise FileNotFoundError(
            "The completed session could not be imported. Start a new Claude Code session, run the bounded task again, "
            f"exit, then rerun {command}."
        )
    return trace_id


def captured_task(
    config: MaidaConfig, pointer: dict, *, agent: str | None = None, command: str = "maida check"
) -> tuple[str, str]:
    """Choose the newest task before import; a newer unusable task never falls back."""
    attached = attached_providers(pointer)
    if agent is not None and agent not in PROVIDERS:
        raise ValueError(
            "--agent must be claude-code, codex or chatgpt-work. Rerun maida check with a supported agent."
        )
    if agent is not None and agent not in attached:
        raise ValueError(f"Capture is not attached for {agent}. Run maida init --agent {agent} in this repository.")
    runtimes = {PROVIDERS[name].runtime for name in ({agent} if agent else attached)}
    if not runtimes:
        raise FileNotFoundError("Capture is not attached here. Run maida init in this repository to attach it.")
    receipts = _receipts(config, runtimes, command=command)
    if not receipts:
        if runtimes == {"claude-code"}:
            return onboarding_run(config, command=command), "claude-code"
        raise FileNotFoundError(
            "No task captured in this repository. Review and trust the configured native hooks, start a new local "
            "agent session, run one bounded task, let the turn finish, then rerun " + command + ". "
            "For local-only Work, restart the desktop app and install Maida capture from the repository marketplace."
        )
    latest = max(receipts, key=lambda item: item["_started"])
    if latest["_runtime"] == "claude-code":
        return _claude_run(latest, command=command), "claude-code"
    if latest.get("state") != "closed":
        raise FileNotFoundError(
            f"The newest Codex runtime turn is {latest.get('state', 'unreadable')}. Finish or rerun that task "
            f"and let its root turn stop, then rerun {command}. Session closure does not complete a turn."
        )
    if latest.get("has_start") is not True or latest.get("complete_tools") is not True:
        raise FileNotFoundError(
            "The newest Codex runtime turn has incomplete lifecycle or tool pairs. Review and trust the capture "
            f"hooks, run a new bounded task that reads a repository file, let it finish, then rerun {command}."
        )
    from maida.capture.codex_hook import materialize_turn

    try:
        trace_id = materialize_turn(config, latest)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        raise FileNotFoundError(
            "The newest Codex runtime turn could not be imported. Preserve its evidence, review and trust the "
            f"capture hooks, run a new bounded task and rerun {command}."
        ) from exc
    return trace_id, "codex"
