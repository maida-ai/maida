"""Developer onboarding and configuration must not affect the test suite."""

from tests.support.paths import REPO_ROOT
import json
import os
import subprocess
import sys
import uuid


def test_suite_ignores_developer_project_home_and_capture_environment(tmp_path):
    root = REPO_ROOT
    project = tmp_path / "initialized-project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", str(project)], check=True)
    local = project / ".maida/local.json"
    local.parent.mkdir()
    local.write_text(
        json.dumps({"version": 2, "project_id": uuid.uuid4().hex, "providers": {"claude-code": {"enabled": True}}})
    )
    before = local.read_bytes()
    home = tmp_path / "developer-home"
    settings = home / ".maida/config.yaml"
    settings.parent.mkdir(parents=True)
    settings.write_text("redact: false\n")
    developer_data = tmp_path / "developer-data"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--config-file",
            str(root / "pytest.toml"),
            "-n",
            "0",
            "-q",
            str(root / "tests/_cli/test_gating.py") + "::test_assert_defaults_to_latest_run",
            str(root / "tests/_cli/test_claude_import.py") + "::test_cli_import_json_and_idempotent_notice",
            str(root / "tests/capture/test_claude_hook.py") + "::test_hook_capture_sanitizes_before_persistence",
            str(root / "tests/_cli/test_capture_diff.py")
            + "::test_capture_diff_ingestion_and_runtime_failures_are_exit_ten",
        ],
        cwd=project,
        env={
            **os.environ,
            "HOME": str(home),
            "MAIDA_DATA_DIR": str(developer_data),
            "MAIDA_REDACT": "0",
            "CLAUDE_PROJECT_DIR": str(project),
        },
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 passed" in result.stdout
    assert local.read_bytes() == before
    assert settings.read_text() == "redact: false\n"
    assert not developer_data.exists()
