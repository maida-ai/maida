"""Reusable test helpers."""

import subprocess
import sys
from pathlib import Path
import pytest


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("MAIDA_DATA_DIR", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setattr("maida.first_run.is_interactive", lambda: True)
    monkeypatch.setattr(
        "maida.first_run.shutil.which",
        lambda name: str(Path(sys.executable).parent / name) if name in {"claude", "maida"} else None,
    )
    return root
