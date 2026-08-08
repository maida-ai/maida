from __future__ import annotations

import json
import re
from pathlib import Path

from typer.testing import CliRunner

from maida.cli import app


def _payload(event: str, session_id: str = "cli-hook-session") -> dict[str, object]:
    value: dict[str, object] = {
        "session_id": session_id,
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/workspace",
        "hook_event_name": event,
    }
    if event == "SessionStart":
        value["source"] = "startup"
    elif event == "PreToolUse":
        value.update(
            tool_name="Read",
            tool_use_id="toolu_cli",
            tool_input={"file_path": "/workspace/README.md"},
        )
    elif event == "PostToolUse":
        value.update(
            tool_name="Read",
            tool_use_id="toolu_cli",
            tool_input={"file_path": "/workspace/README.md"},
            tool_response={"success": True},
        )
    elif event == "SessionEnd":
        value["reason"] = "other"
    return value


def test_capture_claude_hook_is_silent_and_session_end_imports(temp_data_dir):
    runner = CliRunner()
    for event in ("SessionStart", "PreToolUse", "PostToolUse", "SessionEnd"):
        result = runner.invoke(
            app,
            ["capture", "claude-hook"],
            input=json.dumps(_payload(event)),
        )
        assert result.exit_code == 0, result.stderr
        assert result.stdout == ""
        assert result.stderr == ""


def test_capture_claude_hook_reads_exactly_one_json_object(temp_data_dir):
    runner = CliRunner()
    malformed = runner.invoke(app, ["capture", "claude-hook"], input="not json")
    assert malformed.exit_code == 10
    assert malformed.stdout == ""
    assert "Invalid Claude hook payload" in malformed.stderr

    multiple = runner.invoke(
        app,
        ["capture", "claude-hook"],
        input=json.dumps(_payload("SessionStart")) + "\n{}",
    )
    assert multiple.exit_code == 10
    assert multiple.stdout == ""


def test_capture_claude_hook_never_emits_a_policy_decision(temp_data_dir):
    denied = {
        "session_id": "cli-hook-session",
        "transcript_path": "/tmp/transcript.jsonl",
        "cwd": "/workspace",
        "permission_mode": "auto",
        "hook_event_name": "PermissionDenied",
        "tool_name": "Bash",
        "tool_input": {"command": "rm -rf /tmp/example"},
        "tool_use_id": "toolu_denied",
        "reason": "Blocked by classifier",
    }
    result = CliRunner().invoke(
        app,
        ["capture", "claude-hook"],
        input=json.dumps(denied),
    )
    assert result.exit_code == 0
    assert result.stdout == ""


def test_documented_project_hook_configuration_is_valid_and_compact():
    docs = (Path(__file__).parents[1] / "docs" / "claude-code.md").read_text()
    match = re.search(
        r"## Command-hook fallback.*?```json\n(?P<config>.*?)\n```",
        docs,
        re.DOTALL,
    )
    assert match is not None
    config_text = match.group("config")
    assert len([line for line in config_text.splitlines() if line.strip()]) <= 10
    config = json.loads(config_text)
    assert set(config["hooks"]) == {
        "SessionStart",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PermissionDenied",
        "SessionEnd",
    }
