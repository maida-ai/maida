"""Add passive capture observers without replacing a project's configuration."""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied", "SessionEnd")
COMMAND = "maida capture claude-hook"


def read_safe(path: Path) -> bytes | None:
    """Read an ordinary file, refusing symlinked output ancestors."""
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError(f"Refusing symlinked configuration path {path}. Use an ordinary file and rerun maida init.")
    if path.exists() and not path.is_file():
        raise ValueError(f"Configuration path {path} is not a file. Move it aside and rerun maida init.")
    return path.read_bytes() if path.exists() else None


def atomic_replace(path: Path, content: bytes, *, expected: bytes | None) -> None:
    """Replace a previewed file, preserving permissions and checking its input."""
    if read_safe(path) != expected:
        raise ValueError(f"Configuration {path} changed after preview. Rerun maida init to review the new changes.")
    if content == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".maida-settings-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        if expected is not None:
            os.chmod(temporary, path.stat().st_mode & 0o777)
        if read_safe(path) != expected:
            raise ValueError(f"Configuration {path} changed after preview. Rerun maida init.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def settings_object(raw: bytes | None, path: Path) -> dict:
    try:
        settings = json.loads(raw) if raw is not None else {}
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Malformed settings in {path}. Repair its JSON and rerun maida init.") from exc
    if not isinstance(settings, dict):
        raise ValueError(f"{path} settings must contain an object. Repair its JSON and rerun maida init.")
    return settings


def merged_settings(settings: dict) -> tuple[dict, list[str]]:
    """Preserve settings; add an unrestricted observer for each missing event."""
    result = copy.deepcopy(settings)
    hooks = result.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("settings.hooks must be an object. Repair it and rerun maida init.")
    added = []
    for event in EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise ValueError(f"settings.hooks.{event} must be a list. Repair it and rerun maida init.")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("hooks"), list):
                raise ValueError(f"settings.hooks.{event} contains an invalid group. Repair it and rerun maida init.")
            if any(not isinstance(hook, dict) for hook in group["hooks"]):
                raise ValueError(f"settings.hooks.{event} contains an invalid hook. Repair it and rerun maida init.")
        if any(
            group.get("matcher", "") in ("", "*")
            and any(
                hook.get("type") == "command"
                and hook.get("command") == COMMAND
                and not any(hook.get(key) for key in ("if", "async", "once"))
                for hook in group["hooks"]
            )
            for group in groups
        ):
            continue
        observer = {"type": "command", "command": COMMAND}
        if event == "SessionEnd":
            observer["timeout"] = 30
        groups.append({"hooks": [observer]})
        added.append(event)
    return result, added


def prepare_settings(project: Path) -> tuple[Path, bytes | None, bytes, list[str]]:
    path = project / ".claude/settings.json"
    before = read_safe(path)
    merged, added = merged_settings(settings_object(before, path))
    content = (json.dumps(merged, indent=2) + "\n").encode() if added else before
    return path, before, content, added


def install(project: Path, *, apply: bool) -> list[str]:
    """Preview or install observers; compatible with the tutorial installer."""
    path, before, content, added = prepare_settings(project.resolve(strict=True))
    if apply and added:
        atomic_replace(path, content, expected=before)
    return added
