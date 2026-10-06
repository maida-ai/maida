"""Offline claude_code loading tests."""

from __future__ import annotations
from tests.support.paths import FIXTURES_ROOT
import pytest
from maida.config import load_config
from maida.integrations.claude_code import (
    ClaudeCaptureInputError,
    load_capture_segment,
    load_claude_capture,
)
from tests.support.claude_code import _install_fixture


FIXTURES = FIXTURES_ROOT / "traces" / "claude-code" / "2.1.220"

SESSIONS = {
    "normal": "fixture-normal",
    "regression": "fixture-regression",
    "log-only": "fixture-log-only",
    "malformed": "fixture-malformed",
}


def test_malformed_fixture_and_traversal_segment_are_rejected(temp_data_dir):
    with pytest.raises(ClaudeCaptureInputError, match="input_tokens"):
        load_capture_segment(FIXTURES / "malformed")
    _install_fixture("normal", temp_data_dir)
    with pytest.raises(ClaudeCaptureInputError, match="segment"):
        load_claude_capture("fixture-normal", load_config(), segment="../0001")
