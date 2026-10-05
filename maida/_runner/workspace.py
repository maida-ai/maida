"""Workspace isolation helpers for per-trial execution."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any

from maida import __version__
from maida.config import MaidaConfig
from maida._runner.types import RunExecutionError


def _workspace_files(project_root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return [Path(item.decode()) for item in result.stdout.split(b"\0") if item]


def _copy_workspace(project_root: Path, destination: Path) -> None:
    for relative_path in _workspace_files(project_root):
        source = project_root / relative_path
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        elif source.is_file():
            shutil.copy2(source, target)


def _environment_fingerprint(project_root: Path) -> dict[str, Any]:
    digest = hashlib.sha256()
    for relative in sorted(_workspace_files(project_root), key=lambda item: item.as_posix()):
        path = project_root / relative
        if not path.is_file():
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "algorithm": "sha256",
        "workspace": digest.hexdigest(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "maida": __version__,
    }


def _preserve_trace(trace_id: str, trial_data_dir: Path, config: MaidaConfig) -> None:
    source = trial_data_dir / "runs" / trace_id
    destination = config.data_dir.expanduser() / "runs" / trace_id
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise RunExecutionError(f"Trace destination already exists: {trace_id}")
    shutil.copytree(source, destination)
