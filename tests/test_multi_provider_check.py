"""Select the latest started task across attached runtimes, never stale success."""

import json
import subprocess

import pytest

from typer.testing import CliRunner

from maida.cli import app
from maida.capture.providers import updated_pointer
from maida.config import load_config
from tests.test_project_capture import initialized, task

runner = CliRunner()


def attach(root, provider):
    path = root / ".maida/local.json"
    path.write_text(json.dumps(updated_pointer(json.loads(path.read_text()), provider)))


def codex_event(root, event, *, session="codex-session", turn="turn-1", **extra):
    payload = {"session_id": session, "cwd": str(root), "hook_event_name": event, "turn_id": turn, **extra}
    return runner.invoke(app, ["capture", "codex-hook"], input=json.dumps(payload))


def codex_task(root, *, turn="turn-1"):
    for event, extra in (
        ("SessionStart", {"source": "startup"}),
        ("UserPromptSubmit", {"prompt": "Read a repository file"}),
        ("PreToolUse", {"tool_name": "Bash", "tool_use_id": "read", "tool_input": {"command": "cat README.md"}}),
        (
            "PostToolUse",
            {
                "tool_name": "Bash",
                "tool_use_id": "read",
                "tool_input": {"command": "cat README.md"},
                "tool_response": {"exit_code": 0},
            },
        ),
        ("Stop", {"stop_hook_active": False, "last_assistant_message": "Done"}),
    ):
        result = codex_event(root, event, turn=turn, **extra)
        assert result.exit_code == 0, result.output
        assert result.stdout == result.stderr == ""


def test_completed_open_session_and_newest_provider(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    attach(tmp_path, "codex")
    task(tmp_path)
    codex_task(tmp_path)
    result = runner.invoke(app, ["check", "--format", "json"])
    assert result.exit_code == 0, result.output
    run_id = json.loads(result.stdout)["run_id"]
    assert f"maida view {run_id}" in result.stderr
    from maida.storage import load_validated_run

    _, spans = load_validated_run(run_id, load_config(capture=True))
    assert "codex" in json.dumps(spans).lower()
    assert codex_event(tmp_path, "UserPromptSubmit", turn="turn-2", prompt="Newer task").exit_code == 0
    latest = runner.invoke(app, ["check"])
    assert latest.exit_code == 2, latest.output
    assert not latest.stdout
    assert "turn" in latest.stderr.lower()
    assert runner.invoke(app, ["check", "--agent", "claude-code"]).exit_code == 0


def test_work_route_shares_capture_without_claiming_client_identity(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    attach(tmp_path, "chatgpt-work")
    codex_task(tmp_path)
    result = runner.invoke(app, ["check", "--agent", "chatgpt-work"])
    assert result.exit_code == 0, result.output
    assert "Codex runtime" in result.stdout
    unsupported = runner.invoke(app, ["check", "--agent", "codex"])
    assert unsupported.exit_code == 2
    assert "maida init --agent codex" in unsupported.stderr


def test_disabled_cached_hook_is_quiet_and_sdk_defaults_survive(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    monkeypatch.chdir(tmp_path)
    attach(tmp_path, "codex")
    codex_task(tmp_path)
    path = tmp_path / ".maida/local.json"
    path.write_text(json.dumps(updated_pointer(json.loads(path.read_text()), "codex", enabled=False)))
    result = codex_event(tmp_path, "UserPromptSubmit", turn="disabled", prompt="Never record")
    assert result.exit_code == 0
    assert result.stdout == result.stderr == ""
    assert not any(
        "disabled" in item.read_text() for item in (load_config(capture=True).data_dir / "onboarding").glob("*.json")
    )
    assert load_config().data_dir == temp_data_dir


def test_wrong_repository_payload_and_bad_input_cannot_block_tools(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    attach(tmp_path, "codex")
    monkeypatch.chdir(tmp_path)
    missing = runner.invoke(app, ["capture", "codex-hook"], input="invalid")
    assert missing.exit_code == 10
    assert not missing.stdout
    assert "Invalid Codex hook payload" in missing.stderr
    assert runner.invoke(app, ["check", "--agent", "unknown"]).exit_code == 2


@pytest.mark.parametrize("output_format", ["json", "markdown"])
def test_codex_formats_and_exact_uv_viewer(tmp_path, temp_data_dir, monkeypatch, output_format):
    initialized(tmp_path)
    attach(tmp_path, "codex")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("UV_RUN_RECURSION_DEPTH", "1")
    codex_task(tmp_path)
    result = runner.invoke(app, ["check", "--agent", "codex", "--format", output_format])
    assert result.exit_code == 0, result.output
    assert "View:" not in result.stdout
    assert "View: uv run maida view " in result.stderr
    if output_format == "json":
        assert json.loads(result.stdout)["run_id"] in result.stderr
    else:
        assert "Maida verdict: pass" in result.stdout


def test_corrupt_newest_turn_never_falls_back_to_claude(tmp_path, temp_data_dir, monkeypatch):
    initialized(tmp_path)
    attach(tmp_path, "codex")
    monkeypatch.chdir(tmp_path)
    task(tmp_path)
    codex_task(tmp_path)
    tool = {"tool_name": "Bash", "tool_use_id": "read", "tool_input": {"command": "conflicting"}}
    conflict = codex_event(tmp_path, "PreToolUse", **tool)
    assert conflict.exit_code == 10
    result = runner.invoke(app, ["check"])
    assert result.exit_code == 2
    assert "corrupt" in result.stderr
    assert not result.stdout
    assert runner.invoke(app, ["check", "--agent", "claude-code"]).exit_code == 0


def test_payload_repository_and_subdirectory_isolation(tmp_path, temp_data_dir, monkeypatch):
    first, second = tmp_path / "first", tmp_path / "second"
    initialized(first)
    initialized(second)
    attach(first, "codex")
    attach(second, "codex")
    nested = first / "src"
    nested.mkdir()
    monkeypatch.chdir(second)
    codex_task(nested)
    assert runner.invoke(app, ["check", "--agent", "codex"]).exit_code == 2
    monkeypatch.chdir(nested)
    assert runner.invoke(app, ["check", "--agent", "codex"]).exit_code == 0
    assert load_config(capture=True).project_root == first
    monkeypatch.chdir(tmp_path)
    unattached = codex_event(tmp_path, "UserPromptSubmit", prompt="Outside a checkout")
    assert unattached.exit_code == 0 and unattached.stdout == unattached.stderr == ""
    assert not (temp_data_dir / "captures").exists()


def test_fresh_worktree_has_separate_capture_identity(tmp_path, temp_data_dir, monkeypatch):
    first, worktree = tmp_path / "first", tmp_path / "worktree"
    initialized(first)
    subprocess.run(
        [
            "git",
            "-C",
            str(first),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "--allow-empty",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", str(first), "worktree", "add", "--detach", "--quiet", str(worktree)], check=True)
    (worktree / ".maida").mkdir()
    (worktree / ".maida/local.json").write_text(
        json.dumps({"version": 2, "project_id": "b" * 32, "providers": {"codex": {"enabled": True}}})
    )
    attach(first, "codex")
    monkeypatch.chdir(first)
    codex_task(first)
    first_id = json.loads(runner.invoke(app, ["check", "--format", "json"]).stdout)["run_id"]
    monkeypatch.chdir(worktree)
    assert runner.invoke(app, ["check"]).exit_code == 2
    codex_task(worktree)
    second_id = json.loads(runner.invoke(app, ["check", "--format", "json"]).stdout)["run_id"]
    assert first_id != second_id
