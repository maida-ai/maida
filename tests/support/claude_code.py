"""Offline claude_code observation and capture helpers."""

from __future__ import annotations
from tests.support.paths import FIXTURES_ROOT
import hashlib
import json
import shutil
from pathlib import Path


FIXTURES = FIXTURES_ROOT / "traces" / "claude-code" / "2.1.220"

SESSIONS = {
    "normal": "fixture-normal",
    "regression": "fixture-regression",
    "log-only": "fixture-log-only",
    "malformed": "fixture-malformed",
}


def _install_fixture(name: str, data_dir: Path) -> Path:
    session_id = SESSIONS[name]
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    destination = data_dir / "captures" / "claude-code" / session_hash / "0001"
    shutil.copytree(FIXTURES / name, destination)
    return destination


def _source_meta(span: dict) -> dict:
    raw = span.get("attributes", {}).get("maida.meta")
    return json.loads(raw)["claude_code"] if raw else {}
