"""Low-level plan artifact field parsing and private signature parts."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

from maida._plan_contract.errors import _fail

_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


def _mapping(value: object, field_name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an object")
    return value


def _exact_fields(
    value: Mapping[str, Any],
    expected: set[str],
    field_name: str,
    *,
    code: str = "PLAN_ARTIFACT_INVALID",
) -> None:
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        details = []
        if missing:
            details.append(f"missing {', '.join(missing)}")
        if unknown:
            details.append(f"unknown {', '.join(unknown)}")
        raise _fail(
            code,
            f"{field_name} fields do not match the contract ({'; '.join(details)})",
        )


def _string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be a non-empty string")
    return value


def _digest(value: object, field_name: str) -> str:
    result = _string(value, field_name)
    if _DIGEST_RE.fullmatch(result) is None:
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be a lowercase SHA-256 digest")
    return result


def _integer(value: object, field_name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            f"{field_name} must be an integer of at least {minimum}",
        )
    return value


def _number(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise _fail(
            "PLAN_ARTIFACT_INVALID",
            f"{field_name} must be a finite non-negative number",
        )
    return float(value)


def _string_set(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an array")
    items = tuple(_string(item, field_name) for item in value)
    if len(set(items)) != len(items):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must not contain duplicates")
    return tuple(sorted(items))


def _digest_list(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an array")
    return tuple(_digest(item, field_name) for item in value)


@dataclass(frozen=True)
class _PlanBudget:
    cost_usd: float
    model_tokens: int
    tool_calls: int
    wall_time_ms: int

    def to_dict(self) -> dict[str, int | float]:
        return {
            "cost_usd": self.cost_usd,
            "model_tokens": self.model_tokens,
            "tool_calls": self.tool_calls,
            "wall_time_ms": self.wall_time_ms,
        }


def _budget(value: object, field_name: str) -> _PlanBudget:
    data = _mapping(value, field_name)
    _exact_fields(
        data,
        {"cost_usd", "model_tokens", "tool_calls", "wall_time_ms"},
        field_name,
    )
    return _PlanBudget(
        cost_usd=_number(data["cost_usd"], f"{field_name}.cost_usd"),
        model_tokens=_integer(data["model_tokens"], f"{field_name}.model_tokens"),
        tool_calls=_integer(data["tool_calls"], f"{field_name}.tool_calls"),
        wall_time_ms=_integer(data["wall_time_ms"], f"{field_name}.wall_time_ms"),
    )


@dataclass(frozen=True, order=True)
class _PlanModule:
    module_id: str
    module_digest: str
    count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "module_digest": self.module_digest,
            "module_id": self.module_id,
        }


def _modules(value: object, field_name: str) -> tuple[_PlanModule, ...]:
    if not isinstance(value, list) or not value:
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be a non-empty array")
    modules = []
    for index, item in enumerate(value):
        data = _mapping(item, f"{field_name}[{index}]")
        _exact_fields(
            data,
            {"count", "module_digest", "module_id"},
            f"{field_name}[{index}]",
        )
        modules.append(
            _PlanModule(
                module_id=_string(data["module_id"], f"{field_name}[{index}].module_id"),
                module_digest=_digest(data["module_digest"], f"{field_name}[{index}].module_digest"),
                count=_integer(data["count"], f"{field_name}[{index}].count", minimum=1),
            )
        )
    identities = [(item.module_id, item.module_digest) for item in modules]
    if len(set(identities)) != len(identities):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must not contain duplicates")
    return tuple(sorted(modules))


@dataclass(frozen=True)
class _PlanGrant:
    capabilities: tuple[str, ...]
    effects: tuple[str, ...]

    def to_dict(self) -> dict[str, list[str]]:
        return {
            "capabilities": list(self.capabilities),
            "effects": list(self.effects),
        }

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted({*self.capabilities, *self.effects}))


def _grant(value: object, field_name: str) -> _PlanGrant:
    data = _mapping(value, field_name)
    _exact_fields(data, {"capabilities", "effects"}, field_name)
    return _PlanGrant(
        capabilities=_string_set(data["capabilities"], f"{field_name}.capabilities"),
        effects=_string_set(data["effects"], f"{field_name}.effects"),
    )


def _approval_requirements(value: object, field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an array")
    result = []
    for index, item in enumerate(value):
        data = _mapping(item, f"{field_name}[{index}]")
        _exact_fields(data, {"effect_name", "node_key"}, f"{field_name}[{index}]")
        result.append(
            (
                _string(data["node_key"], f"{field_name}[{index}].node_key"),
                _string(data["effect_name"], f"{field_name}[{index}].effect_name"),
            )
        )
    if len(set(result)) != len(result):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must not contain duplicates")
    return tuple(sorted(result))


def _alias_provenance(value: object, field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must be an array")
    result = []
    for index, item in enumerate(value):
        data = _mapping(item, f"{field_name}[{index}]")
        _exact_fields(data, {"alias", "node_key"}, f"{field_name}[{index}]")
        result.append(
            (
                _string(data["node_key"], f"{field_name}[{index}].node_key"),
                _string(data["alias"], f"{field_name}[{index}].alias"),
            )
        )
    if len(set(result)) != len(result):
        raise _fail("PLAN_ARTIFACT_INVALID", f"{field_name} must not contain duplicates")
    return tuple(sorted(result))


def _canonical_digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
