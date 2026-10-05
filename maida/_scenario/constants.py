"""Shared constants for scenario manifests and Claude execution."""

from __future__ import annotations

import re
from pathlib import Path

DEFAULT_SCENARIO_MANIFEST = Path(".maida/scenarios.yaml")
_SEMVER_RE = re.compile(r"(?<!\d)(\d+\.\d+\.\d+)(?!\d)")
_MODEL_RE = re.compile(
    r"^claude-(?:(?:haiku|opus|sonnet)-\d+(?:-\d+)+"
    r"|\d+(?:-\d+)*-(?:haiku|opus|sonnet)-\d{8})$"
)
_MODEL_ALIASES = frozenset(
    {
        "claude",
        "claude-haiku",
        "claude-opus",
        "claude-sonnet",
        "claude-test",
        "default",
        "haiku",
        "opus",
        "sonnet",
    }
)
_SAFE_ENVIRONMENT_KEYS = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_BASE_URL",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "COMSPEC",
        "APPDATA",
        "LOCALAPPDATA",
        "HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NO_PROXY",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TERM",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERPROFILE",
    }
)
