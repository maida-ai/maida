"""Stable repository and fixture paths for tests at any package depth."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_ROOT = REPO_ROOT / "tests"
FIXTURES_ROOT = TESTS_ROOT / "fixtures"
