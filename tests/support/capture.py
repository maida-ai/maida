"""Reusable test helpers."""

import json
import subprocess
import uuid
from typer.testing import CliRunner
from maida.cli import app


runner = CliRunner()


def initialized(root):
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    (root / ".maida").mkdir()
    project_id = uuid.uuid4().hex
    (root / ".maida/local.json").write_text(
        json.dumps({"version": 2, "project_id": project_id, "providers": {"claude-code": {"enabled": True}}})
    )
    return project_id


def deliver(root, event, *, session="normal-task", **extra):
    payload = {"session_id": session, "cwd": str(root), "hook_event_name": event, **extra}
    if event == "SessionStart":
        payload["source"] = "startup"
    if event == "SessionEnd":
        payload["reason"] = "other"
    return runner.invoke(app, ["capture", "claude-hook"], input=json.dumps(payload))


def task(root, session="normal-task", failure=False, looping=False):
    assert deliver(root, "SessionStart", session=session).exit_code == 0
    for index in range(3 if looping else 1):
        tool = {"tool_use_id": f"tool-{index}", "tool_name": "Read", "tool_input": {"file_path": "pyproject.toml"}}
        assert deliver(root, "PreToolUse", session=session, **tool).exit_code == 0
        terminal = "PostToolUseFailure" if failure else "PostToolUse"
        extra = {"error": "fixture failure"} if failure else {"tool_response": {"content": "pytest"}}
        assert deliver(root, terminal, session=session, **tool, **extra).exit_code == 0
    assert deliver(root, "SessionEnd", session=session).exit_code == 0
