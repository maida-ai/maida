"""Dependency-light local installation discovery and pointer validation."""

import json
import re
from pathlib import Path

from maida.capture.providers import PROVIDERS
from maida.capture_setup import read_safe

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
        raise ValueError(
            f"Unsupported or invalid local pointer. Move {path} aside and rerun maida init to create a new local identity."
        ) from exc
    if (
        not isinstance(pointer, dict)
        or type(pointer.get("version")) is not int
        or pointer["version"] != 2
        or "capture" in pointer
        or "enabled" in pointer
        or not isinstance(pointer.get("project_id"), str)
        or not _PROJECT_ID.fullmatch(pointer["project_id"])
        or not _valid_providers(pointer)
    ):
        raise ValueError(
            f"Unsupported or invalid local pointer. Move {path} aside and rerun maida init to create a new local identity."
        )
    return root, pointer


def _valid_providers(pointer: dict) -> bool:
    providers = pointer.get("providers")
    return (
        isinstance(providers, dict)
        and bool(providers)
        and all(
            name in PROVIDERS and isinstance(state, dict) and type(state.get("enabled")) is bool
            for name, state in providers.items()
        )
    )
