"""
Shared pytest fixtures and helpers for Maida tests.
"""

import os
import tempfile
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolated_local_environment(tmp_path_factory):
    """Keep developer project pointers, home settings and capture env out of tests."""
    directory = tmp_path_factory.mktemp("local-environment")
    home = directory / "home"
    project = directory / "project"
    home.mkdir()
    project.mkdir()
    # Keep isolation separate from tests' monkeypatch fixture: some tests call
    # monkeypatch.undo() between assertions and must remain in the sandbox.
    with pytest.MonkeyPatch.context() as isolation:
        isolation.setenv("HOME", str(home))
        isolation.setenv("USERPROFILE", str(home))
        isolation.setenv("APPDATA", str(home / "AppData/Roaming"))
        isolation.setenv("LOCALAPPDATA", str(home / "AppData/Local"))
        isolation.setattr(Path, "home", staticmethod(lambda: home))
        isolation.chdir(project)
        for key in list(os.environ):
            if key.startswith("MAIDA_") or key == "CLAUDE_PROJECT_DIR":
                isolation.delenv(key)
        yield


@pytest.fixture(autouse=True)
def reset_otel(isolated_local_environment):
    """Reset OTel singleton state before each test so MaidaLocalSpanExporter
    picks up the correct MAIDA_DATA_DIR for this test's temp dir."""
    from maida._tracing.otel import _shutdown_otel

    _shutdown_otel()
    yield
    _shutdown_otel()


@pytest.fixture
def temp_data_dir():
    """Create a temporary directory and set MAIDA_DATA_DIR to it for the test."""
    with tempfile.TemporaryDirectory() as tmp:
        # Windows temp paths may contain a short-name alias (e.g. RUNNER~1).
        # Match the canonical storage paths returned by project configuration.
        directory = Path(tmp).resolve()
        old = os.environ.get("MAIDA_DATA_DIR")
        try:
            os.environ["MAIDA_DATA_DIR"] = str(directory)
            yield directory
        finally:
            if old is not None:
                os.environ["MAIDA_DATA_DIR"] = old
            elif "MAIDA_DATA_DIR" in os.environ:
                os.environ.pop("MAIDA_DATA_DIR")


@pytest.fixture
def symlink_supported(tmp_path):
    """Run symlink safety checks wherever the runner permits their creation."""
    target = tmp_path / "symlink-probe-target"
    link = tmp_path / "symlink-probe"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Runner cannot create symlinks: {exc}")
    else:
        link.unlink()
    finally:
        target.rmdir()
