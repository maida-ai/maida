from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from maida.config import load_config
from maida.constants import REDACTED_MARKER, TRUNCATED_MARKER
from maida.events import spans_to_events
from maida.integrations.claude_code import (
    import_claude_capture,
    load_capture_segment,
    normalize_claude_capture,
)
from maida.storage import list_runs


SESSION_ID = "hook/session/../../private"


def _payload(event: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "session_id": SESSION_ID,
        "transcript_path": f"/tmp/{SESSION_ID}/transcript.jsonl",
        "cwd": "/workspace/project",
        "permission_mode": "default",
        "hook_event_name": event,
    }
    if event == "SessionStart":
        payload.update(source="startup", model="claude-test")
    elif event == "PreToolUse":
        payload.update(
            tool_name="Write",
            tool_use_id="toolu_write",
            tool_input={"file_path": "/workspace/project/out.txt", "content": "ok"},
        )
    elif event == "PostToolUse":
        payload.update(
            tool_name="Write",
            tool_use_id="toolu_write",
            tool_input={"file_path": "/workspace/project/out.txt", "content": "ok"},
            tool_response={"filePath": "/workspace/project/out.txt", "success": True},
            duration_ms=12,
        )
    elif event == "PostToolUseFailure":
        payload.update(
            tool_name="Bash",
            tool_use_id="toolu_bash",
            tool_input={"command": "pytest -q"},
            error="Exit code 1",
            is_interrupt=False,
            duration_ms=25,
        )
    elif event == "PermissionDenied":
        payload.update(
            tool_name="Read",
            tool_use_id="toolu_read",
            tool_input={"file_path": "/workspace/private.txt"},
            reason="Blocked by classifier",
        )
    elif event == "SessionEnd":
        payload.update(reason="other")
    payload.update(overrides)
    return payload


def _capture_dir(data_dir: Path, segment: str = "0001") -> Path:
    session_hash = hashlib.sha256(SESSION_ID.encode()).hexdigest()
    return data_dir / "captures" / "claude-code" / session_hash / segment


def _capture_worker(data_dir: str, payload: dict[str, object]) -> str:
    os.environ["MAIDA_DATA_DIR"] = data_dir
    from maida.capture.claude_hook import capture_claude_hook

    return capture_claude_hook(payload, load_config()).segment


def test_hook_capture_sanitizes_before_persistence(temp_data_dir, monkeypatch):
    from maida.capture.claude_hook import capture_claude_hook

    monkeypatch.setenv("MAIDA_MAX_FIELD_BYTES", "100")
    config = load_config()
    capture_claude_hook(_payload("SessionStart"), config)
    capture_claude_hook(
        _payload(
            "PreToolUse",
            tool_input={
                "file_path": "/workspace/project/out.txt",
                "content": "x" * 200,
                "nested": {"api_token": "secret-value", "safe": "yes"},
            },
        ),
        config,
    )

    capture_dir = _capture_dir(temp_data_dir)
    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in capture_dir.parent.rglob("*")
        if path.is_file()
    )
    assert SESSION_ID not in persisted
    records = [
        json.loads(line)
        for line in (capture_dir / "logs.jsonl").read_text().splitlines()
    ]
    tool_input = records[-1]["record"]["attributes"]["tool_input"]
    assert tool_input["nested"] == {"api_token": REDACTED_MARKER, "safe": "yes"}
    assert tool_input["content"].endswith(TRUNCATED_MARKER)
    assert "transcript_path" not in records[-1]["record"]["attributes"]


@pytest.mark.parametrize(
    "event",
    [
        "SessionStart",
        "PreToolUse",
        "PostToolUse",
        "PostToolUseFailure",
        "PermissionDenied",
        "SessionEnd",
    ],
)
def test_supported_hook_events_are_accepted(event, temp_data_dir):
    from maida.capture.claude_hook import capture_claude_hook

    result = capture_claude_hook(_payload(event), load_config())
    assert result.segment == "0001"


def test_segments_rotate_on_session_boundaries_but_not_compaction(temp_data_dir):
    from maida.capture.claude_hook import capture_claude_hook

    config = load_config()
    assert capture_claude_hook(_payload("SessionStart"), config).segment == "0001"
    assert (
        capture_claude_hook(_payload("SessionStart", source="compact"), config).segment
        == "0001"
    )
    assert (
        capture_claude_hook(_payload("SessionStart", source="resume"), config).segment
        == "0002"
    )
    # An exact delivery retry belongs to the already-active segment.
    assert (
        capture_claude_hook(_payload("SessionStart", source="resume"), config).segment
        == "0002"
    )
    assert (
        capture_claude_hook(_payload("SessionStart", source="fork"), config).segment
        == "0003"
    )
    assert (
        capture_claude_hook(_payload("SessionStart", source="clear"), config).segment
        == "0004"
    )
    assert (
        json.loads((_capture_dir(temp_data_dir, "0003") / "manifest.json").read_text())[
            "state"
        ]
        == "closed"
    )


def test_concurrent_appends_and_duplicate_deliveries_are_safe(temp_data_dir):
    from maida.capture.claude_hook import capture_claude_hook

    capture_claude_hook(_payload("SessionStart"), load_config())
    unique = [
        _payload(
            "PreToolUse",
            tool_name="Read",
            tool_use_id=f"toolu_{index}",
            tool_input={"file_path": f"/workspace/{index}.txt"},
        )
        for index in range(12)
    ]
    deliveries = [*unique, unique[0], unique[0], unique[1]]
    with ProcessPoolExecutor(
        max_workers=4, mp_context=multiprocessing.get_context("spawn")
    ) as executor:
        segments = list(
            executor.map(
                _capture_worker,
                [str(temp_data_dir)] * len(deliveries),
                deliveries,
            )
        )

    assert set(segments) == {"0001"}
    records = [
        json.loads(line)
        for line in (_capture_dir(temp_data_dir) / "logs.jsonl")
        .read_text()
        .splitlines()
    ]
    pre_records = [
        record
        for record in records
        if record["record"]["event_name"] == "claude_code.hook.pre_tool_use"
    ]
    assert len(pre_records) == len(unique)
    assert len(
        {record["record"]["attributes"]["event.sequence"] for record in records}
    ) == len(records)
    manifest = json.loads((_capture_dir(temp_data_dir) / "manifest.json").read_text())
    assert manifest["signals"]["logs"] == len(records)


def test_duplicate_session_end_is_idempotent(temp_data_dir):
    from maida.capture.claude_hook import capture_claude_hook

    config = load_config()
    capture_claude_hook(_payload("SessionStart"), config)
    first = capture_claude_hook(_payload("SessionEnd"), config)
    second = capture_claude_hook(_payload("SessionEnd"), config)

    assert first.segment == second.segment == "0001"
    assert first.accepted is True
    assert second.accepted is False
    assert first.import_result.imported is True
    assert second.import_result.imported is False
    assert len(list_runs(limit=20, config=config)) == 1


def test_conflicting_duplicate_tool_delivery_is_rejected(temp_data_dir):
    from maida.capture.claude_hook import (
        ClaudeHookConflictError,
        capture_claude_hook,
    )

    config = load_config()
    capture_claude_hook(_payload("SessionStart"), config)
    capture_claude_hook(_payload("PreToolUse"), config)
    with pytest.raises(ClaudeHookConflictError, match="conflicting duplicate"):
        capture_claude_hook(
            _payload("PreToolUse", tool_input={"file_path": "/changed.txt"}),
            config,
        )


def test_hook_tools_pair_by_id_and_recover_incomplete_or_preless_calls(temp_data_dir):
    from maida.capture.claude_hook import capture_claude_hook

    config = load_config()
    for payload in (
        _payload("SessionStart"),
        _payload("PreToolUse"),
        _payload("PostToolUse"),
        _payload(
            "PreToolUse",
            tool_name="Bash",
            tool_use_id="toolu_incomplete",
            tool_input={"command": "pytest -q"},
        ),
        _payload(
            "PostToolUseFailure",
            tool_name="Edit",
            tool_use_id="toolu_preless_failure",
            tool_input={"file_path": "/workspace/a.py"},
            error="file changed",
        ),
        _payload("PermissionDenied"),
    ):
        capture_claude_hook(payload, config)

    segment = load_capture_segment(_capture_dir(temp_data_dir))
    normalized = normalize_claude_capture(segment, config)
    tools = [
        event
        for event in spans_to_events(normalized.spans)
        if event["event_type"] == "TOOL_CALL"
    ]
    by_name = {event["name"]: event for event in tools}
    assert set(by_name) == {"Write", "Bash", "Edit", "Read"}
    assert normalized.meta["counts"]["tool_calls"] == 4
    write = by_name["Write"]
    assert write["payload"]["args"]["file_path"] == "/workspace/project/out.txt"
    assert write["payload"]["result"]["success"] is True
    assert write["payload"]["status"] == "ok"
    incomplete = by_name["Bash"]
    assert incomplete["payload"]["status"] == "ok"
    assert incomplete["meta"]["claude_code"]["terminal_missing"] is True
    assert by_name["Edit"]["payload"]["status"] == "error"
    assert by_name["Read"]["payload"]["status"] == "error"


def test_abrupt_capture_remains_importable_and_session_end_imports_automatically(
    temp_data_dir,
):
    from maida.capture.claude_hook import ClaudeHookImportError, capture_claude_hook

    config = load_config()
    capture_claude_hook(_payload("SessionStart"), config)
    capture_claude_hook(_payload("PreToolUse"), config)
    manual = import_claude_capture(SESSION_ID, config)
    assert manual.imported is True

    # A terminal event changes the active capture, so import still refuses to overwrite it.
    capture_claude_hook(_payload("PostToolUse"), config)
    with pytest.raises(ClaudeHookImportError, match="changed"):
        capture_claude_hook(_payload("SessionEnd"), config)

    # A complete fresh segment imports during SessionEnd without an explicit command.
    second_session = "complete-hook-session"
    start = _payload("SessionStart", session_id=second_session)
    end = _payload("SessionEnd", session_id=second_session)
    capture_claude_hook(start, config)
    result = capture_claude_hook(end, config)
    assert result.import_result is not None
    assert result.import_result.imported is True
    assert len(list_runs(limit=20, config=config)) == 2


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({}, "session_id"),
        (_payload("Unknown"), "unsupported"),
        (_payload("SessionStart", source="unknown"), "source"),
        (_payload("PreToolUse", tool_use_id=""), "tool_use_id"),
        (_payload("PostToolUseFailure", duration_ms=-1), "duration_ms"),
    ],
)
def test_malformed_hook_payloads_are_rejected(payload, message, temp_data_dir):
    from maida.capture.claude_hook import ClaudeHookInputError, capture_claude_hook

    with pytest.raises(ClaudeHookInputError, match=message):
        capture_claude_hook(payload, load_config())
    assert not (temp_data_dir / "captures").exists()
