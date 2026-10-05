"""Provider-neutral local capture installation state."""

from __future__ import annotations

import copy
from dataclasses import dataclass


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    runtime: str
    executable: str | None
    markers: tuple[str, ...]


PROVIDERS = {
    "claude-code": Provider("claude-code", "Claude Code", "claude-code", "claude", (".claude", "CLAUDE.md")),
    "codex": Provider("codex", "Codex", "codex", "codex", (".codex",)),
}


def attached_providers(pointer: dict, *, enabled_only: bool = True) -> set[str]:
    return {name for name, state in pointer["providers"].items() if not enabled_only or state["enabled"]}


def provider_enabled(pointer: dict, provider: str) -> bool:
    return provider in attached_providers(pointer)


def runtime_enabled(pointer: dict, runtime: str) -> bool:
    return any(PROVIDERS[name].runtime == runtime for name in attached_providers(pointer))


def updated_pointer(pointer: dict, provider: str, *, enabled: bool = True) -> dict:
    """Update one attachment without changing repository identity."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported capture provider {provider!r}")
    result = copy.deepcopy(pointer)
    result["providers"][provider] = {"enabled": enabled}
    return result
