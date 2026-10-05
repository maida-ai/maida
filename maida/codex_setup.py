"""Preserve native Codex hooks without changing native trust state."""

from __future__ import annotations

import copy
import json
import os
import shlex
import tomllib
from pathlib import Path, PureWindowsPath

from maida.capture_setup import hook_arguments, read_safe, settings_object

EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "Interrupt",
    "SessionEnd",
)
NATIVE_HOOKS = Path(".codex/hooks.json")


def encoded(value: dict) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode()


def is_codex_observer(command: object) -> bool:
    """Recognize only exact Maida invocations, never a shell wrapper."""
    if command == "maida capture codex-hook":
        return True
    if not isinstance(command, str):
        return False
    try:
        arguments = hook_arguments(command)
    except ValueError:
        return False
    return (
        len(arguments) >= 5
        and (Path(arguments[0]).is_absolute() or PureWindowsPath(arguments[0]).is_absolute())
        and arguments[1:]
        in (
            ["-E", "-P", "-m", "maida.cli", "capture", "codex-hook"],
            ["-I", "-m", "maida.cli", "capture", "codex-hook"],
            ["-m", "maida.cli", "capture", "codex-hook"],
        )
        and (shlex.join(arguments) == command or command.startswith("& '"))
    )


def _groups(settings: dict, command: str) -> dict:
    hooks = settings.get("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError(f"settings.hooks must be an object. Repair it and rerun {command}.")
    for event, groups in hooks.items():
        if not isinstance(groups, list) or any(
            not isinstance(group, dict)
            or not isinstance(group.get("hooks"), list)
            or any(not isinstance(hook, dict) for hook in group["hooks"])
            for group in groups
        ):
            raise ValueError(f"Invalid hook groups for {event}. Repair them and rerun {command}.")
    return hooks


def removed_hooks(settings: dict, *, command: str = "maida detach --agent codex") -> tuple[dict, int]:
    result = copy.deepcopy(settings)
    hooks = _groups(result, command)
    removed = 0
    for event, groups in list(hooks.items()):
        retained_groups = []
        for group in groups:
            retained = []
            for hook in group["hooks"]:
                platform_commands = [hook.get(field) for field in ("command", "commandWindows") if hook.get(field)]
                # A mixed handler may also own unrelated behavior on another
                # platform. Keep the whole handler; disabling the repository
                # runtime makes its remaining Maida command inert.
                owned = (
                    hook.get("type") == "command"
                    and bool(platform_commands)
                    and all(is_codex_observer(value) for value in platform_commands)
                )
                if not owned:
                    retained.append(hook)
            count = len(group["hooks"]) - len(retained)
            removed += count
            if not count or retained:
                group["hooks"] = retained
                retained_groups.append(group)
        if retained_groups or not groups:
            hooks[event] = retained_groups
        else:
            del hooks[event]
    if not hooks:
        result.pop("hooks", None)
    return result, removed


def merged_hooks(settings: dict, observer_command: str) -> tuple[dict, list[str]]:
    # Replace only our registrations, leaving restricted or asynchronous handlers
    # untouched when they are not Maida observers.
    result, _ = removed_hooks(settings, command="maida init")
    hooks = result.setdefault("hooks", {})
    for event in EVENTS:
        observer = {
            "type": "command",
            "command": observer_command,
            "timeout": 3 if event in {"Interrupt", "SessionEnd"} else 10,
        }
        if observer_command.startswith("& '"):
            observer["commandWindows"] = observer_command
        hooks.setdefault(event, []).append({"hooks": [observer]})
    added = [event for event in EVENTS if settings.get("hooks", {}).get(event) != hooks[event]]
    return result, added


def prepare_native_hooks(root: Path, observer_command: str) -> tuple[Path, bytes | None, bytes | None]:
    path = root / NATIVE_HOOKS
    before = read_safe(path)
    settings = settings_object(before, path)
    merged, events = merged_hooks(settings, observer_command)
    return path, before, encoded(merged) if events else before


def prepare_runtime_detach(root: Path) -> list[tuple[Path, bytes | None, bytes | None]]:
    command = "maida detach --agent codex"
    path = root / NATIVE_HOOKS
    before = read_safe(path, command=command)
    settings, count = removed_hooks(settings_object(before, path, command=command), command=command)
    return [(path, before, encoded(settings) if count else before)]


def check_hooks_enabled(root: Path) -> list[tuple[Path, bytes | None, bytes | None]]:
    """Detect local explicit disablement; native trust remains the user's review."""
    home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    enabled = True
    observed = []
    for path in (home / "config.toml", root / ".codex/config.toml", home / "requirements.toml"):
        raw = read_safe(path)
        observed.append((path, raw, raw))
        if raw is None:
            continue
        try:
            settings = tomllib.loads(raw.decode())
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Malformed settings in {path}. Repair its TOML and rerun maida init.") from exc
        if settings.get("allow_managed_hooks_only") is True:
            raise ValueError(
                f"Only managed hooks are allowed in {path}. Ask your administrator to allow local hooks, then rerun maida init."
            )
        features = settings.get("features", {})
        if not isinstance(features, dict):
            raise ValueError(f"Invalid features in {path}. Repair its TOML and rerun maida init.")
        flag = features.get("hooks", features.get("codex_hooks", enabled))
        if not isinstance(flag, bool):
            raise ValueError(f"Invalid hooks feature in {path}. Set it to a boolean and rerun maida init.")
        enabled = flag
    if not enabled:
        raise ValueError(
            "Codex hooks are disabled by local configuration. Enable [features].hooks and rerun maida init."
        )
    return observed
