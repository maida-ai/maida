"""Fixtures shared by CLI command tests."""

import pytest


@pytest.fixture
def empty_data_dir(temp_data_dir):
    """Empty data dir with MAIDA_DATA_DIR set (env restored after test)."""
    return temp_data_dir
