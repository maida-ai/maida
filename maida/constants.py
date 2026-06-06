"""Shared constants: spec version, count schema, and redaction/truncation markers."""

from pathlib import Path

REDACTED_MARKER = "__REDACTED__"
TRUNCATED_MARKER = "__TRUNCATED__"

# SPEC version for the OTel-based trace format (major bump from 0.1).
SPEC_VERSION = "0.2"

# Recursion limit and depth of redaction/truncation
DEPTH_LIMIT = 10

# Default directory name for configs, local storage, etc.
LOCAL_DIR_NAME = Path(".maida")


def default_counts() -> dict[str, int]:
    """Default counts per run meta schema. Keys: llm_calls, tool_calls, errors, loop_warnings."""
    return {
        "llm_calls": 0,
        "tool_calls": 0,
        "errors": 0,
        "loop_warnings": 0,
    }
