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

from maida.capture_setup import COMMAND, atomic_replace, prepare_settings, read_safe, settings_object
from maida.config import load_config
from maida.project_local import LOCAL_POINTER, installation, repository_root

NEXT_ACTION = (
    "Next: Start a new Claude Code session here, run one bounded task\n"
    "(for example, find this repository's test command without editing files),\n"
    "then exit the session and run:\n"
    "  maida assert --expect-status ok --no-loops --no-guardrails"
)
_AGENTS = {
    "Claude Code": ("claude", (".claude", "CLAUDE.md")),
    "Codex": ("codex", (".codex",)),
    "Cursor": ("cursor-agent", (".cursor",)),
    "OpenCode": ("opencode", ("opencode.json", "opencode.jsonc")),
}


def is_interactive() -> bool:
    return sys.stdin.isatty() and not os.environ.get("CI")


def detect_agent(root: Path, explicit: str | None) -> str:
    if explicit is not None:
        if explicit != "claude-code":
            raise ValueError(f"Unsupported agent {explicit!r}. Use maida init --agent claude-code for Claude Code.")
        return "Claude Code (explicit selection)"
    if installation(root) is not None:
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
            "If this task uses Claude Code, run maida init --agent claude-code."
        )
    if selected:
        raise ValueError(
            f"Detected {selected[0]}; automatic capture setup currently supports Claude Code. "
            "For a local Claude Code task, run maida init --agent claude-code."
        )
    raise ValueError(
        "No supported coding agent detected (checked repository configuration and installed agent commands). "
        "Start Claude Code in this repository, then rerun maida init."
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
    if not shutil.which("maida"):
        raise ValueError(
            "The maida command is not on PATH for Claude hooks. Add the Maida installation's bin directory to PATH and rerun maida init."
        )
    _check_hooks_enabled(root)
    settings_path, settings_before, settings_after, events = prepare_settings(root)
    local = installation(root)
    project_id = local[1]["project_id"] if local else uuid.uuid4().hex
    pointer_path = root / LOCAL_POINTER
    pointer_before = read_safe(pointer_path)
    pointer_after = (
        pointer_before
        if local
        else (json.dumps({"version": 1, "project_id": project_id, "capture": "claude-code"}, indent=2) + "\n").encode()
    )
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
    rule = b"/.maida/local.json"
    if rule not in exclude_after.splitlines():
        if exclude_after and not exclude_after.endswith(b"\n"):
            exclude_after += b"\n"
        exclude_after += rule + b"\n"
    files = [
        (settings_path, settings_before, settings_after),
        (pointer_path, pointer_before, pointer_after),
        (exclude_path, exclude_before, exclude_after),
    ]
    changes = [item for item in files if item[1] != item[2]]
    config = load_config(project_root=root)
    evidence = config.data_dir if local else config.data_dir.expanduser().resolve() / "projects" / project_id
    if not changes:
        typer.echo("Claude Code capture is already ready for this repository.")
        typer.echo("\n" + NEXT_ACTION)
        return
    typer.echo(f"Repository: {root}")
    if events:
        typer.echo(f"Would add observer command to {settings_path}: {COMMAND}")
        typer.echo("Events: " + ", ".join(events))
        if "SessionEnd" in events:
            typer.echo("SessionEnd observer timeout: 30 seconds.")
    if pointer_before is None:
        typer.echo(f"Would create local installation pointer: {pointer_path}")
    if exclude_before != exclude_after:
        typer.echo(f"Would add /.maida/local.json to {exclude_path}")
    typer.echo(f"Local task evidence: {evidence}")
    typer.echo("Capture is passive; redaction follows your Maida settings. Task evidence stays on this machine.")
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
    # Recheck every file before changing any of them.
    for path, before, _ in files:
        if read_safe(path) != before:
            raise ValueError(f"Configuration {path} changed after preview. Rerun maida init to review the new changes.")
    written = []
    try:
        for path, before, after in changes:
            atomic_replace(path, after, expected=before)
            written.append((path, before, after))
    except (OSError, ValueError) as exc:
        unrestored = []
        for path, before, after in reversed(written):
            try:
                if before is None:
                    if read_safe(path) != after:
                        raise ValueError("concurrent change")
                    path.unlink()
                else:
                    atomic_replace(path, before, expected=after)
            except (OSError, ValueError):
                unrestored.append(str(path))
        if unrestored:
            raise ValueError(
                f"Setup could not restore {', '.join(unrestored)}. Review these files before rerunning maida init."
            ) from exc
        raise ValueError(
            "Could not write capture setup; previous files were restored. Check write permissions for the previewed paths and rerun maida init."
        ) from exc
    typer.echo("Claude Code capture is ready for this repository.")
    typer.echo("\n" + NEXT_ACTION)
