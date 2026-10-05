"""Capture installation identity; Codex and local Work share a runtime."""

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
    "chatgpt-work": Provider("chatgpt-work", "local-only ChatGPT Work", "codex", None, ()),
}


def attached_providers(pointer: dict, *, enabled_only: bool = True) -> set[str]:
    if pointer["version"] == 1:
        return {"claude-code"} if not enabled_only or pointer.get("enabled", True) else set()
    return {name for name, state in pointer["providers"].items() if not enabled_only or state["enabled"]}


def provider_enabled(pointer: dict, provider: str) -> bool:
    return provider in attached_providers(pointer)


def runtime_enabled(pointer: dict, runtime: str) -> bool:
    return any(PROVIDERS[name].runtime == runtime for name in attached_providers(pointer))


def updated_pointer(pointer: dict, provider: str, *, enabled: bool = True) -> dict:
    """Upgrade explicitly without changing project identity or evidence paths."""
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported capture provider {provider!r}")
    if pointer["version"] == 1 and provider == "claude-code":
        result = copy.deepcopy(pointer)
        if not enabled or pointer.get("enabled") is False:
            result["enabled"] = enabled
        return result
    if pointer["version"] == 1:
        result = copy.deepcopy(pointer)
        result.pop("capture", None)
        result.pop("enabled", None)
        result.update(version=2, providers={"claude-code": {"enabled": pointer.get("enabled", True)}})
    else:
        result = copy.deepcopy(pointer)
    result["providers"][provider] = {"enabled": enabled}
    return result
