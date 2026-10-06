"""Passive stdin receiver binds source cwd to an enabled local installation."""

import json

import pytest
from typer.testing import CliRunner

from maida.cli import app
from maida.config import load_config
from tests.support.capture import initialized

runner = CliRunner()


@pytest.fixture
def project(tmp_path, temp_data_dir, monkeypatch):
    project_id = initialized(tmp_path)
    (tmp_path / ".maida/local.json").write_text(
        json.dumps({"version": 2, "project_id": project_id, "providers": {"codex": {"enabled": True}}})
    )
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.parametrize("raw", ["{", "[]", "{}", '{"cwd":"relative"}'])
def test_bad_hook_input_is_nonblocking_and_never_persisted(project, raw):
    result = runner.invoke(app, ["capture", "codex-hook"], input=raw)
    assert result.exit_code == 10
    assert not result.stdout
    assert "Invalid Codex hook payload" in result.stderr
    assert not (load_config(capture=True).data_dir / "captures").exists()


def test_codex_receipt_does_not_import_inside_hooks(project):
    result = runner.invoke(
        app,
        ["capture", "codex-hook"],
        input=json.dumps(
            {
                "cwd": str(project),
                "session_id": "session",
                "turn_id": "turn",
                "hook_event_name": "UserPromptSubmit",
                "prompt": "bounded task",
            }
        ),
    )
    assert result.exit_code == 0
    assert result.stdout == result.stderr == ""
    assert len(list((load_config(capture=True).data_dir / "onboarding").glob("codex-*.json"))) == 1
    assert not (load_config(capture=True).data_dir / "runs").exists()


def test_claude_receiver_is_inert_for_codex_only_pointer(project):
    result = runner.invoke(app, ["capture", "claude-hook"], input="invalid cached input")
    assert result.exit_code == 0
    assert result.stdout == result.stderr == ""
    assert not (load_config(capture=True).data_dir / "captures").exists()
