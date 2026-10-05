"""Add passive capture observers without replacing a project's configuration."""

from __future__ import annotations

import copy
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

EVENTS = ("SessionStart", "PreToolUse", "PostToolUse", "PostToolUseFailure", "PermissionDenied", "SessionEnd")
COMMAND = "maida capture claude-hook"
_WINDOWS = os.name == "nt"
_HOOK_ARGUMENTS = ["-E", "-P", "-m", "maida.cli", "capture", "claude-hook"]


def bound_hook_command() -> str:
    """Keep the active environment's interpreter path, including venv symlinks."""
    # Exclude cwd and Python import overrides, retaining user-site installations.
    if _WINDOWS:
        executable = str(PureWindowsPath(sys.executable)).replace("'", "''")
        return f"& '{executable}' {' '.join(_HOOK_ARGUMENTS)}"
    return shlex.join([str(Path(sys.executable).absolute()), *_HOOK_ARGUMENTS])


def hook_arguments(command: str) -> list[str]:
    """Parse our exact bound PowerShell invocation or a legacy POSIX command."""
    match = re.fullmatch(r"& '((?:[^']|'')+)' " + re.escape(" ".join(_HOOK_ARGUMENTS)), command)
    if match:
        executable = match[1].replace("''", "'")
        if PureWindowsPath(executable).is_absolute():
            return [executable, *_HOOK_ARGUMENTS]
        raise ValueError("Hook interpreter must be an absolute path")
    return shlex.split(command)


def is_maida_hook_command(command: object) -> bool:
    """Recognize exact legacy and bound invocations, never shell wrappers."""
    if command == COMMAND:
        return True
    if not isinstance(command, str):
        return False
    try:
        arguments = hook_arguments(command)
    except ValueError:
        return False
    return (
        len(arguments) in (5, 6, 7)
        and (Path(arguments[0]).is_absolute() or PureWindowsPath(arguments[0]).is_absolute())
        and arguments[1:]
        in (
            ["-E", "-P", "-m", "maida.cli", "capture", "claude-hook"],
            ["-I", "-m", "maida.cli", "capture", "claude-hook"],
            ["-m", "maida.cli", "capture", "claude-hook"],
        )
        and (shlex.join(arguments) == command or command.startswith("& '"))
    )


def validate_hook_command(command: str) -> None:
    """Probe installed capture without a project import path or uv's PATH."""
    environment = os.environ.copy()
    environment["PATH"] = os.defpath
    for key in ("PYTHONPATH", "PYTHONHOME"):
        environment.pop(key, None)
    try:
        with tempfile.TemporaryDirectory(prefix="maida-hook-check-") as directory:
            result = subprocess.run(
                [*hook_arguments(command), "--help"],
                cwd=directory,
                env=environment,
                capture_output=True,
                timeout=15,
                check=False,
            )
        if result.returncode == 0:
            return
    except (OSError, subprocess.TimeoutExpired):
        pass
    raise ValueError(
        "The Maida environment cannot run its Claude capture hook independently. "
        "Reinstall Maida in this environment, then rerun maida init."
    )


def read_safe(path: Path, *, command: str = "maida init") -> bytes | None:
    """Read an ordinary file, refusing symlinked output ancestors."""
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError(f"Refusing symlinked configuration path {path}. Use an ordinary file and rerun {command}.")
    if path.exists() and not path.is_file():
        raise ValueError(f"Configuration path {path} is not a file. Move it aside and rerun {command}.")
    return path.read_bytes() if path.exists() else None


def atomic_replace(path: Path, content: bytes, *, expected: bytes | None, command: str = "maida init") -> None:
    """Replace a previewed file, preserving permissions and checking its input."""
    if not isinstance(content, bytes):
        raise TypeError("Configuration replacement content must be bytes")
    if read_safe(path, command=command) != expected:
        raise ValueError(f"Configuration {path} changed after preview. Rerun {command} to review the new changes.")
    if content == expected:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".maida-settings-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
        if expected is not None:
            os.chmod(temporary, path.stat().st_mode & 0o777)
        if read_safe(path, command=command) != expected:
            raise ValueError(f"Configuration {path} changed after preview. Rerun {command}.")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def settings_object(raw: bytes | None, path: Path, *, command: str = "maida init") -> dict:
    try:
        settings = json.loads(raw) if raw is not None else {}
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"Malformed settings in {path}. Repair its JSON and rerun {command}.") from exc
    if not isinstance(settings, dict):
        raise ValueError(f"{path} settings must contain an object. Repair its JSON and rerun {command}.")
    return settings


def merged_settings(
    settings: dict, inherited: tuple[dict, ...] = (), *, observer_command: str = COMMAND
) -> tuple[dict, list[str]]:
    """Preserve settings; add an unrestricted observer for each missing event."""
    result = copy.deepcopy(settings)
    disabled = False
    for source in (*inherited, settings):
        if "disableAllHooks" in source:
            if not isinstance(source["disableAllHooks"], bool):
                raise ValueError("Invalid disableAllHooks. Set it to a boolean and rerun maida init.")
            disabled = source["disableAllHooks"]
    if disabled:
        raise ValueError("Claude hooks are disabled by effective settings. Enable hooks and rerun maida init.")
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
        updated = False
        for group in groups:
            for hook in group["hooks"]:
                if (
                    observer_command != COMMAND
                    and hook.get("type") == "command"
                    and is_maida_hook_command(hook.get("command"))
                    and hook["command"] != observer_command
                ):
                    hook["command"] = observer_command
                    if observer_command.startswith("& '"):
                        hook["shell"] = "powershell"
                    updated = True
        inherited_groups = []
        for source in inherited:
            source_hooks = source.get("hooks", {})
            if not isinstance(source_hooks, dict) or not isinstance(source_hooks.get(event, []), list):
                raise ValueError(f"Invalid inherited settings.hooks.{event}. Repair it and rerun maida init.")
            if any(
                not isinstance(group, dict)
                or not isinstance(group.get("hooks"), list)
                or any(not isinstance(hook, dict) for hook in group["hooks"])
                for group in source_hooks.get(event, [])
            ):
                raise ValueError(f"Invalid inherited settings.hooks.{event}. Repair it and rerun maida init.")
            inherited_groups.extend(source_hooks.get(event, []))
        if any(
            group.get("matcher", "") in ("", "*")
            and group.get("enabled", True) is True
            and not group.get("disabled", False)
            and any(
                hook.get("type") == "command"
                and (
                    hook.get("command") == observer_command
                    or (observer_command == COMMAND and is_maida_hook_command(hook.get("command")))
                )
                and hook.get("enabled", True) is True
                and not hook.get("disabled", False)
                and not any(hook.get(key) for key in ("if", "async", "once"))
                for hook in group["hooks"]
            )
            for group in groups + inherited_groups
            if isinstance(group, dict) and isinstance(group.get("hooks"), list)
        ):
            if updated:
                added.append(event)
            continue
        observer = {"type": "command", "command": observer_command}
        if observer_command.startswith("& '"):
            observer["shell"] = "powershell"
        if event == "SessionEnd":
            observer["timeout"] = 30
        groups.append({"hooks": [observer]})
        added.append(event)
    return result, added


def prepare_settings(
    project: Path, *, local: bool = False, observer_command: str = COMMAND
) -> tuple[Path, bytes | None, bytes | None, list[str]]:
    path = project / (".claude/settings.local.json" if local else ".claude/settings.json")
    before = read_safe(path)
    inherited = ()
    if local:
        sources = (Path.home() / ".claude/settings.json", project / ".claude/settings.json")
        inherited = tuple(settings_object(read_safe(source), source) for source in sources)
        # Inherited hooks are additive. Do not claim readiness while an older
        # observer may fail on PATH or record through a different environment.
        if observer_command != COMMAND:
            for source, settings in zip(sources, inherited):
                _, count = removed_settings(settings, command="maida init")
                if count and any(
                    is_maida_hook_command(hook.get("command")) and hook["command"] != observer_command
                    for groups in settings.get("hooks", {}).values()
                    for group in groups
                    for hook in group["hooks"]
                    if group.get("enabled", True) is True
                    and not group.get("disabled", False)
                    and hook.get("type") == "command"
                    and hook.get("enabled", True) is True
                    and not hook.get("disabled", False)
                ):
                    recovery = (
                        "Remove these user-wide Maida observers using Claude Code's /hooks menu, then rerun maida init."
                        if source == sources[0]
                        else "Run maida detach --agent claude-code, then rerun maida init to install local observers."
                    )
                    raise ValueError(f"Older Maida hooks in {source} use a different executable. {recovery}")
    merged, added = merged_settings(settings_object(before, path), inherited, observer_command=observer_command)
    content = (json.dumps(merged, indent=2) + "\n").encode() if added else before
    return path, before, content, added


def removed_settings(settings: dict, *, command: str = "maida detach --agent claude-code") -> tuple[dict, int]:
    """Remove exact Maida observers, retaining all other handlers and settings."""
    result = copy.deepcopy(settings)
    hooks = result.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"settings.hooks must be an object. Repair it and rerun {command}.")
    removed = 0
    for event, groups in list(hooks.items()):
        if not isinstance(groups, list):
            raise ValueError(f"Invalid hooks for {event}. Repair them and rerun {command}.")
        retained_groups = []
        for group in groups:
            if (
                not isinstance(group, dict)
                or not isinstance(group.get("hooks"), list)
                or any(not isinstance(hook, dict) for hook in group["hooks"])
            ):
                raise ValueError(f"Invalid hook group for {event}. Repair it and rerun {command}.")
            retained = [
                hook
                for hook in group["hooks"]
                if not (hook.get("type") == "command" and is_maida_hook_command(hook.get("command")))
            ]
            count = len(group["hooks"]) - len(retained)
            removed += count
            if not count or retained:
                group["hooks"] = retained
                retained_groups.append(group)
        if retained_groups or not groups:
            hooks[event] = retained_groups
        else:
            del hooks[event]
    if removed and not hooks:
        result.pop("hooks", None)
    return result, removed


def install(project: Path, *, apply: bool) -> list[str]:
    """Preview or install observers; compatible with the tutorial installer."""
    path, before, content, added = prepare_settings(project.resolve(strict=True))
    if apply and added:
        assert content is not None, "Missing settings replacement for added observers"
        atomic_replace(path, content, expected=before)
    return added
