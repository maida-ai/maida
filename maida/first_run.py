"""Detect and approve local first-run setup before changing project files."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import typer

from maida.capture_setup import (
    atomic_replace,
    bound_hook_command,
    prepare_settings,
    read_safe,
    removed_settings,
    settings_object,
    validate_hook_command,
)
from maida.project_local import LOCAL_POINTER, installation, repository_root

NEXT_ACTION = (
    "Next: Start a new Claude Code session here, run one bounded task\n"
    "(for example, find this repository's test command without editing files),\n"
    "then exit the session and run:\n"
    "  {command} check"
)
_AGENTS = {
    "Claude Code": ("claude", (".claude", "CLAUDE.md")),
    "Codex": ("codex", (".codex",)),
    "Cursor": ("cursor-agent", (".cursor",)),
    "OpenCode": ("opencode", ("opencode.json", "opencode.jsonc")),
}


def is_interactive() -> bool:
    return sys.stdin.isatty() and not os.environ.get("CI")


def maida_command() -> str:
    """Keep printed commands usable when the CLI was launched through uv."""
    return "uv run maida" if os.environ.get("UV_RUN_RECURSION_DEPTH") else "maida"


def detect_agent(root: Path, explicit: str | None, *, command: str = "maida init") -> str:
    if explicit is not None:
        if explicit != "claude-code":
            raise ValueError(f"Unsupported agent {explicit!r}. Use {command} --agent claude-code for Claude Code.")
        return "Claude Code (explicit selection)"
    if installation(root, command=command) is not None:
        return "Claude Code (existing local setup)"
    projects = [name for name, (_, paths) in _AGENTS.items() if any((root / path).exists() for path in paths)]
    installed = [name for name, (command, _) in _AGENTS.items() if shutil.which(command)]
    selected = projects or installed
    if selected == ["Claude Code"]:
        return "Claude Code (repository configuration)" if projects else "Claude Code (installed command)"
    if len(selected) > 1:
        raise ValueError(
            f"Ambiguous agent environment: detected {', '.join(selected)} "
            f"in {'repository configuration' if projects else 'installed commands'}. "
            f"If this task uses Claude Code, run {command} --agent claude-code."
        )
    if selected:
        raise ValueError(
            f"Detected {selected[0]}; automatic capture setup currently supports Claude Code. "
            f"For a local Claude Code task, run {command} --agent claude-code."
        )
    raise ValueError(
        "No supported coding agent detected (checked repository configuration and installed agent commands). "
        f"Start Claude Code in this repository, then rerun {command}."
    )


def _check_hooks_enabled(root: Path) -> None:
    disabled_at = None
    for path in (
        Path.home() / ".claude/settings.json",
        root / ".claude/settings.json",
        root / ".claude/settings.local.json",
    ):
        settings = settings_object(read_safe(path), path)
        if "disableAllHooks" in settings:
            if not isinstance(settings["disableAllHooks"], bool):
                raise ValueError(f"Invalid disableAllHooks in {path}. Set it to a boolean and rerun maida init.")
            disabled_at = path if settings["disableAllHooks"] else None
    if disabled_at:
        raise ValueError(f"Claude hooks are disabled in {disabled_at}. Enable hooks there and rerun maida init.")


def _git(root: Path, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root), *arguments], text=True, capture_output=True, check=False)


def initialize_capture(agent: str | None = None) -> None:
    root = repository_root(Path.cwd())
    if root is None or _git(root, "rev-parse", "--show-toplevel").returncode:
        raise ValueError("No Git repository detected. Run maida init from your Git checkout.")
    detected = detect_agent(root, agent)
    typer.echo(f"Detected {detected}.")
    _check_hooks_enabled(root)
    observer_command = bound_hook_command()
    settings_path, settings_before, settings_after, events = prepare_settings(
        root, local=True, observer_command=observer_command
    )
    validate_hook_command(observer_command)
    if _git(root, "ls-files", "--error-unmatch", "--", ".claude/settings.local.json").returncode == 0:
        raise ValueError(
            ".claude/settings.local.json is tracked by Git. Run git rm --cached -- .claude/settings.local.json, then rerun maida init."
        )
    local = installation(root)
    project_id = local[1]["project_id"] if local else uuid.uuid4().hex
    pointer_path = root / LOCAL_POINTER
    pointer_before = read_safe(pointer_path)
    pointer = local[1].copy() if local else {"version": 1, "project_id": project_id, "capture": "claude-code"}
    if pointer.get("enabled") is False:
        pointer["enabled"] = True
    pointer_after = pointer_before if local and pointer == local[1] else (json.dumps(pointer, indent=2) + "\n").encode()
    if _git(root, "ls-files", "--error-unmatch", "--", str(LOCAL_POINTER)).returncode == 0:
        raise ValueError(
            ".maida/local.json is tracked by Git. Run git rm --cached -- .maida/local.json, then rerun maida init."
        )
    exclude_result = _git(root, "rev-parse", "--path-format=absolute", "--git-path", "info/exclude")
    if exclude_result.returncode:
        raise ValueError(
            "Cannot locate Git's local exclude file. Repair this checkout's Git metadata and rerun maida init."
        )
    exclude_path = Path(exclude_result.stdout.strip())
    exclude_before = read_safe(exclude_path)
    exclude_after = exclude_before or b""
    rules = [b"/.maida/local.json"]
    if settings_after is not None:
        rules.append(b"/.claude/settings.local.json")
    added_rules = []
    for rule in rules:
        if rule not in exclude_after.splitlines():
            if exclude_after and not exclude_after.endswith(b"\n"):
                exclude_after += b"\n"
            exclude_after += rule + b"\n"
            added_rules.append(rule.decode())
    files = [
        (settings_path, settings_before, settings_after),
        (pointer_path, pointer_before, pointer_after),
        (exclude_path, exclude_before, exclude_after),
    ]
    changes = [item for item in files if item[1] != item[2]]
    if not changes:
        typer.echo("Claude Code capture is already ready for this repository.")
        typer.echo("\n" + NEXT_ACTION.format(command=maida_command()))
        return
    typer.echo(f"Repository: {root}")
    if events:
        typer.echo(f"Would add or update observer command in {settings_path}: {observer_command}")
        typer.echo("Events: " + ", ".join(events))
        if "SessionEnd" in events:
            typer.echo("SessionEnd observer timeout: 30 seconds.")
    if pointer_before is None:
        typer.echo(f"Would create local setup file: {pointer_path}")
    elif pointer_before != pointer_after:
        typer.echo(f"Would re-enable capture in {pointer_path}")
    if added_rules:
        typer.echo(f"Would exclude {', '.join(added_rules)} in {exclude_path}")
    typer.echo("Capture is passive; redaction follows your Maida settings. Task evidence stays on this machine.")
    typer.echo("Run plain claude after setup; Maida's hooks use the validated executable above.")
    if not is_interactive():
        raise ValueError(
            "Setup needs one explicit approval. Rerun maida init in an interactive terminal to approve this preview."
        )
    try:
        approved = typer.confirm("Configure Claude Code capture for this repository?", default=False)
    except (typer.Abort, EOFError):
        approved = False
    if not approved:
        typer.echo("Setup cancelled. Rerun maida init when ready.")
        return
    _write_previewed(files, "init")
    typer.echo("Claude Code capture is ready for this repository.")
    typer.echo("\n" + NEXT_ACTION.format(command=maida_command()))


def _write_previewed(files: list[tuple[Path, bytes | None, bytes | None]], action: str) -> None:
    """Recheck the full preview and restore earlier writes if a later one fails."""
    for path, before, _ in files:
        if read_safe(path, command=f"maida {action}") != before:
            raise ValueError(
                f"Configuration {path} changed after preview. Rerun maida {action} to review the new changes."
            )
    written = []
    try:
        for path, before, after in files:
            if before == after:
                continue
            assert after is not None, "Missing replacement for changed settings"
            atomic_replace(path, after, expected=before, command=f"maida {action}")
            written.append((path, before, after))
    except (OSError, ValueError) as exc:
        unrestored = []
        for path, before, after in reversed(written):
            try:
                if before is None:
                    if read_safe(path, command=f"maida {action}") != after:
                        raise ValueError("concurrent change")
                    path.unlink()
                else:
                    atomic_replace(path, before, expected=after, command=f"maida {action}")
            except (OSError, ValueError):
                unrestored.append(str(path))
        if unrestored:
            raise ValueError(
                f"Setup could not restore {', '.join(unrestored)}. Review these files before rerunning maida {action}."
            ) from exc
        raise ValueError(
            f"Could not write capture setup; previous files were restored. Check write permissions for the previewed paths and rerun maida {action}."
        ) from exc


def detach_capture(agent: str | None = None) -> None:
    """Detach repository observers without deleting evidence or gate artifacts."""
    root = repository_root(Path.cwd())
    if root is None or _git(root, "rev-parse", "--show-toplevel").returncode:
        raise ValueError("No Git repository detected. Run maida detach from your Git checkout.")
    if agent is not None and agent != "claude-code":
        raise ValueError(f"Unsupported agent {agent!r}. Use maida detach --agent claude-code for Claude Code.")
    files = []
    removed = 0
    command = "maida detach --agent claude-code"
    for path in (root / ".claude/settings.json", root / ".claude/settings.local.json"):
        before = read_safe(path, command=command)
        settings, count = removed_settings(settings_object(before, path, command=command))
        after = (json.dumps(settings, indent=2) + "\n").encode() if count else before
        files.append((path, before, after))
        removed += count
    # A damaged identity must not trap a user in capture. Remove hooks anyway;
    # preserve the unreadable pointer and explicitly report cached-hook limits.
    damaged = False
    try:
        local = installation(root, command=command)
    except ValueError:
        local = None
        damaged = True
    if not removed and local is None:
        if agent is None:
            detect_agent(root, None, command="maida detach")
    if local:
        path = root / LOCAL_POINTER
        before = read_safe(path, command=command)
        pointer = {**local[1], "enabled": False}
        after = before if local[1].get("enabled") is False else (json.dumps(pointer, indent=2) + "\n").encode()
        files.append((path, before, after))
    if not any(before != after for _, before, after in files):
        typer.echo("No active repository Maida hooks to detach.")
        return
    typer.echo(f"Repository: {root}")
    typer.echo(
        "Warning: Maida will be detached from Claude Code in this repository; future tasks will not be captured by these hooks."
    )
    for path, before, after in files:
        if before != after:
            typer.echo(
                f"Would {'remove Maida observers from' if path.name != 'local.json' else 'disable capture in'} {path}"
            )
            if _git(root, "ls-files", "--error-unmatch", "--", str(path.relative_to(root))).returncode == 0:
                typer.echo(
                    "  TRACKED: this file is shared with your team; detaching changes version-controlled configuration. Review its Git diff before committing."
                )
    typer.echo("Existing task evidence, policies, baselines and other hooks are preserved.")
    typer.echo("User-wide and managed hooks are outside this repository detach operation.")
    if damaged:
        typer.echo(
            "Local capture identity is unreadable and will be preserved. Exit existing Claude Code sessions before continuing."
        )
    if not is_interactive():
        raise ValueError(
            "Detach needs one explicit approval. Rerun maida detach --agent claude-code in an interactive terminal."
        )
    try:
        approved = typer.confirm("Detach Maida from Claude Code in this repository?", default=False)
    except (typer.Abort, EOFError):
        approved = False
    if not approved:
        typer.echo("Detach cancelled. Capture configuration is unchanged.")
        return
    _write_previewed(files, "detach --agent claude-code")
    typer.echo(
        "Repository Maida hooks detached. Exit and restart any existing Claude Code session to reload its settings."
    )
    typer.echo("To reconnect: maida init --agent claude-code")
