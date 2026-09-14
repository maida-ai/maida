"""The shell smoke check must reject server errors and preserve attack paths."""

import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("status", [200, 301, 500, 503])
def test_smoke_fails_on_unexpected_http_status(tmp_path, status):
    completed, _ = run_smoke(tmp_path, status)
    assert completed.returncode == 1, completed.stdout + completed.stderr


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_smoke_accepts_rejection_and_preserves_path(tmp_path, status):
    completed, calls = run_smoke(tmp_path, status)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert len(calls) == 12
    assert all("--path-as-is" in call for call in calls)


def run_smoke(tmp_path, status):
    curl = tmp_path / "curl"
    calls = tmp_path / "calls"
    curl.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$SMOKE_CALLS"\n'
        'printf "%s" "$SMOKE_STATUS"\n'
    )
    curl.chmod(0o755)
    completed = subprocess.run(
        ["bash", "scripts/smoke_path_traversal.sh", "http://127.0.0.1:8712"],
        cwd=Path(__file__).parents[1],
        env={
            **os.environ,
            "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
            "SMOKE_CALLS": str(calls),
            "SMOKE_STATUS": str(status),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed, calls.read_text().splitlines()
