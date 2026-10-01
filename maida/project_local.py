"""Local installation pointers and first-run evidence, never policy configuration."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from maida.capture_setup import read_safe

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
        or pointer["version"] != 1
        or not isinstance(pointer.get("project_id"), str)
        or not _PROJECT_ID.fullmatch(pointer["project_id"])
        or pointer.get("capture") != "claude-code"
        or ("enabled" in pointer and not isinstance(pointer["enabled"], bool))
    ):
        raise ValueError(f"Invalid {path}. Move it aside and rerun {command} to create a new local identity.")
    return root, pointer


def onboarding_run(config: MaidaConfig, *, command: str = "maida check") -> str:
    """Select the newest started session, refusing stale or incomplete evidence."""
    receipts = []
    directory = config.data_dir / "onboarding"
    for path in directory.glob("*.json"):
        try:
            receipt = json.loads(path.read_bytes())
            if not isinstance(receipt, dict) or not isinstance(receipt.get("started_at"), str):
                raise ValueError("invalid receipt")
            receipts.append(receipt)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"Local capture state is unreadable at {path}. Move that file aside, then capture a new Claude Code "
                f"task, exit the session and rerun {command}."
            ) from exc
    if not receipts:
        raise FileNotFoundError(
            "No Claude Code task captured in this repository. Start a new Claude Code session here, "
            f"run one bounded task, exit the session, then rerun {command}."
        )
    latest = max(receipts, key=lambda item: item["started_at"])
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
