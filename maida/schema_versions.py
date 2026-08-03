"""Independent compatibility rules for Maida's four schema streams."""

from __future__ import annotations

import re


POLICY_SCHEMA_VERSION = "2"
TRACE_SCHEMA_VERSION = "0.2.0"
BASELINE_SCHEMA_VERSION = "0.3.0"
REPORT_SCHEMA_VERSION = "2.0.0"

_SEMVER_RE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def parse_machine_semver(version: object, *, stream: str) -> tuple[int, int, int]:
    """Parse the required full semver form used by generated artifacts."""
    if not isinstance(version, str):
        raise ValueError(f"{stream} schema version must be a semantic version string")
    match = _SEMVER_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"{stream} schema version must use major.minor.patch form")
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def machine_minor_compatible(
    declared: object,
    current: str,
    *,
    stream: str,
    legacy: frozenset[str] = frozenset(),
) -> bool:
    """Generated artifacts tolerate patch drift within the current minor."""
    if declared in legacy:
        return True
    try:
        candidate = parse_machine_semver(declared, stream=stream)
        supported = parse_machine_semver(current, stream=stream)
    except ValueError:
        return False
    return candidate[:2] == supported[:2]
