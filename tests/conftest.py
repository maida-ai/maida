"""
Shared pytest fixtures and helpers for Maida tests.
"""

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def reset_otel():
    """Reset OTel singleton state before each test so MaidaLocalSpanExporter
    picks up the correct MAIDA_DATA_DIR for this test's temp dir."""
    from maida._tracing._otel import _shutdown_otel

    _shutdown_otel()
    yield


@pytest.fixture
def temp_data_dir():
    """Create a temporary directory and set MAIDA_DATA_DIR to it for the test."""
    with tempfile.TemporaryDirectory() as tmp:
        old = os.environ.get("MAIDA_DATA_DIR")
        try:
            os.environ["MAIDA_DATA_DIR"] = tmp
            yield Path(tmp)
        finally:
            if old is not None:
                os.environ["MAIDA_DATA_DIR"] = old
            elif "MAIDA_DATA_DIR" in os.environ:
                os.environ.pop("MAIDA_DATA_DIR")


def get_latest_run_id(config):
    """
    Return run_id of the most recent run for the given config.

    Use when the test has just created a single run in a temp dir (so the
    latest run is the one we care about). If the code under test starts
    writing multiple runs, prefer selecting by run_name or another stable
    attribute instead.
    """
    from maida.storage import list_runs

    runs = list_runs(limit=1, config=config)
    assert runs, "expected at least one run"
    return runs[0].get("run_id") or runs[0].get("trace_id")
