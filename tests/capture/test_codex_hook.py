from __future__ import annotations
import json
from dataclasses import replace
import pytest
from maida.capture.codex_hook import (
    CodexHookConflictError,
    CodexHookInputError,
    capture_codex_hook,
    materialize_turn,
    parse_codex_hook_json,
)
from maida.config import load_config
from maida.events import spans_to_events
from maida.storage import load_validated_run


def payload(event, **extra):
    return {
        "session_id": "private-session",
        "turn_id": "turn-1",
        "hook_event_name": event,
        "cwd": "/repo",
        **extra,
    }


@pytest.fixture
def config(temp_data_dir, tmp_path):
    return replace(load_config(), project_id="project-id", project_root=tmp_path)


def tool(config, event="PreToolUse", **extra):
    data = dict(tool_name="Bash", tool_use_id="call-1", tool_input={"command": "echo ok"})
    data.update(extra)
    return capture_codex_hook(payload(event, **data), config)


def finish(config):
    capture_codex_hook(payload("UserPromptSubmit", prompt="do work"), config)
    tool(config)
    tool(config, "PostToolUse", tool_response={"exit_code": 0})
    return capture_codex_hook(payload("Stop"), config)


def test_passive_capture_redacts_deduplicates_and_materializes_only_at_check(config):
    capture_codex_hook(payload("UserPromptSubmit", prompt="do work"), config)
    record = payload("PreToolUse", tool_name="Bash", tool_use_id="call-1", tool_input={"token": "secret"})
    first = capture_codex_hook(record, config)
    assert first.accepted
    assert not capture_codex_hook(record, config).accepted
    assert not list((config.data_dir / "runs").glob("*"))
    persisted = "\n".join(p.read_text() for p in (config.data_dir / "captures").rglob("*") if p.is_file())
    assert "secret" not in persisted
    assert "private-session" not in persisted
    tool(config, "PostToolUse", tool_response={"exit_code": 0}, tool_input={"token": "secret"})
    completed = capture_codex_hook(payload("Stop"), config)
    receipt = json.loads(completed.receipt_path.read_text())
    assert receipt["state"] == "closed"
    trace_id = materialize_turn(config, receipt)
    meta, spans = load_validated_run(trace_id, config)
    assert meta["counts"]["tool_calls"] == 1
    assert meta["counts"]["llm_calls"] == 0
    assert [e["event_type"] for e in spans_to_events(spans)].count("TOOL_CALL") == 1
    assert materialize_turn(config, receipt) == trace_id
    assert all("gen_ai.usage.input_tokens" not in span["attributes"] for span in spans)


def test_incomplete_tools_and_child_stop_do_not_complete_task(config):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    tool(config)
    child = capture_codex_hook(payload("Stop", agent_id="child"), config)
    assert json.loads(child.receipt_path.read_text())["state"] == "active"
    root = capture_codex_hook(payload("Stop"), config)
    receipt = json.loads(root.receipt_path.read_text())
    assert receipt["state"] == "active"
    with pytest.raises(CodexHookInputError, match="complete"):
        materialize_turn(config, receipt)
    tool(config, "PostToolUse", tool_response="done")
    assert json.loads(root.receipt_path.read_text())["state"] == "active"
    capture_codex_hook(payload("Stop"), config)
    assert json.loads(root.receipt_path.read_text())["state"] == "closed"


def test_continuation_creates_immutable_snapshot_and_duplicate_stop_is_inert(config):
    completed = finish(config)
    receipt = json.loads(completed.receipt_path.read_text())
    old_id = materialize_turn(config, receipt)
    old_run = load_validated_run(old_id, config)
    assert not capture_codex_hook(payload("Stop"), config).accepted
    tool(config, tool_use_id="call-2")
    assert json.loads(completed.receipt_path.read_text())["state"] == "active"
    tool(config, "PostToolUse", tool_use_id="call-2", tool_response="done")
    capture_codex_hook(payload("Stop", stop_hook_active=True), config)
    new_id = materialize_turn(config, json.loads(completed.receipt_path.read_text()))
    assert old_id != new_id
    assert load_validated_run(old_id, config) == old_run


def test_recovered_tool_failure_does_not_make_root_failure(config):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    tool(config)
    tool(config, "PostToolUse", tool_response={"exit_code": 1}, error="exit 1")
    completed = capture_codex_hook(payload("Stop"), config)
    meta, spans = load_validated_run(materialize_turn(config, json.loads(completed.receipt_path.read_text())), config)
    assert meta["status"] == "ok"
    assert meta["counts"]["errors"] == 1
    assert any(span["status_code"] == "ERROR" for span in spans)


@pytest.mark.parametrize("event", ["Interrupt", "SessionEnd"])
def test_interruption_and_session_end_never_manufacture_completion(config, event):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    result = capture_codex_hook(payload(event), config)
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["state"] == "interrupted"


def test_conflicting_delivery_marks_receipt_corrupt(config):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    initial = tool(config)
    with pytest.raises(CodexHookConflictError):
        tool(config, tool_input={"command": "changed"})
    assert json.loads(initial.receipt_path.read_text())["state"] == "corrupt"
    capture_codex_hook(payload("Stop"), config)
    assert json.loads(initial.receipt_path.read_text())["state"] == "corrupt"


@pytest.mark.parametrize("raw", ["", "{}", "[]", "{} {}", "{", json.dumps(payload("Stop", turn_id=None))])
def test_malformed_payload_is_rejected_without_writing(config, raw):
    with pytest.raises(CodexHookInputError):
        parse_codex_hook_json(raw, config)
    assert not (config.data_dir / "captures").exists()


def test_normal_session_end_preserves_completed_turn(config):
    completed = finish(config)
    capture_codex_hook({"session_id": "private-session", "hook_event_name": "SessionEnd"}, config)
    assert json.loads(completed.receipt_path.read_text())["state"] == "closed"


def test_no_observed_tools_is_incomplete_coverage(config):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    result = capture_codex_hook(payload("Stop"), config)
    receipt = json.loads(result.receipt_path.read_text())
    assert receipt["complete_tools"] is False
    assert receipt["state"] == "closed"


def test_repository_id_isolates_same_source_session_and_turn(config):
    first = finish(config)
    other_config = replace(config, project_id="other-project")
    other = capture_codex_hook(payload("UserPromptSubmit"), other_config)
    assert first.receipt_path != other.receipt_path
    assert json.loads(first.receipt_path.read_text())["state"] == "closed"


def test_corrupt_record_structure_is_recovery_error(config):
    completed = finish(config)
    receipt = json.loads(completed.receipt_path.read_text())
    from maida.capture.codex_hook import turn_dir

    (turn_dir(config, receipt["session_hash"], receipt["turn_hash"]) / "events.jsonl").write_text("{}\n")
    with pytest.raises((CodexHookInputError, CodexHookConflictError)):
        materialize_turn(config, receipt)
    with pytest.raises((CodexHookInputError, CodexHookConflictError)):
        capture_codex_hook(payload("Stop"), config)


def test_normalized_run_and_tool_error_match_public_event_contract(config):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    tool(config)
    tool(config, "PostToolUse", tool_response={"exit_code": 2, "message": "bad command"}, error="exit 2")
    completed = capture_codex_hook(payload("Stop"), config)
    meta, spans = load_validated_run(materialize_turn(config, json.loads(completed.receipt_path.read_text())), config)
    events = spans_to_events(spans)
    tool_event = next(event for event in events if event["event_type"] == "TOOL_CALL")
    assert tool_event["payload"]["error"]["message"] == "exit 2"
    assert tool_event["payload"]["error"]["error_type"] == "CodexToolError"
    assert tool_event["payload"]["result"] == {"exit_code": 2, "message": "bad command"}
    assert spans[0]["attributes"]["maida.run_name"] == meta["run_name"]
    assert spans[0]["attributes"]["maida.tool_calls"] == 1


def test_long_and_redacted_ids_keep_distinct_tool_and_agent_identity(config):
    config = replace(config, max_field_bytes=100, redact_keys=[*config.redact_keys, "id"])
    capture_codex_hook(payload("UserPromptSubmit"), config)
    for index in range(2):
        child = "child" * 50 + str(index)
        tool(config, agent_id=child, tool_use_id="call" * 50 + str(index))
        tool(config, "PostToolUse", agent_id=child, tool_use_id="call" * 50 + str(index), tool_response="done")
    completed = capture_codex_hook(payload("Stop"), config)
    meta, spans = load_validated_run(materialize_turn(config, json.loads(completed.receipt_path.read_text())), config)
    assert meta["counts"]["tool_calls"] == 2
    tools = [span for span in spans if span["name"] == "Bash"]
    assert len({span["span_id"] for span in tools}) == 2
    assert len({span["parent_span_id"] for span in tools}) == 1


@pytest.mark.parametrize(
    "changed",
    [
        {"hook_event_name": "invented"},
        {"agent_id": 1},
        {"stop_hook_active": "true"},
        {"duration_ms": -1},
        {"duration_ms": True},
        {"tool_use_id": ""},
    ],
)
def test_invalid_source_fields_rejected_before_persistence(config, changed):
    data = payload("PreToolUse", tool_name="Bash", tool_use_id="tool", tool_input={})
    data.update(changed)
    with pytest.raises(CodexHookInputError):
        capture_codex_hook(data, config)
    assert not (config.data_dir / "captures").exists()


@pytest.mark.parametrize("event", ["PreToolUse", "PostToolUse"])
def test_missing_tool_payload_fields_are_rejected(config, event):
    data = payload(event, tool_name="Bash", tool_use_id="call")
    with pytest.raises(CodexHookInputError, match="tool_input"):
        capture_codex_hook(data, config)
    data["tool_input"] = {}
    if event == "PostToolUse":
        with pytest.raises(CodexHookInputError, match="tool_response"):
            capture_codex_hook(data, config)


def test_bounded_stdin_and_session_without_turn_are_safe(config):
    with pytest.raises(CodexHookInputError, match="large"):
        parse_codex_hook_json(json.dumps(payload("Stop")), config, max_hook_bytes=1)
    result = capture_codex_hook(
        {
            "session_id": "private-session",
            "hook_event_name": "SessionStart",
            "source": "resume",
            "model": "local-model",
        },
        config,
    )
    assert result.receipt_path is None
    empty_end = capture_codex_hook({"session_id": "private-session", "hook_event_name": "SessionEnd"}, config)
    assert empty_end.receipt_path is None
    completed = finish(config)
    _, spans = load_validated_run(materialize_turn(config, json.loads(completed.receipt_path.read_text())), config)
    source = json.loads(spans[0]["attributes"]["maida.meta"])["codex"]
    assert source["session_context"]["model"] == "local-model"
    assert source["coverage"]["outside_scope"]


def test_late_child_activity_invalidates_completed_root(config):
    completed = finish(config)
    tool(config, agent_id="child", tool_use_id="child-tool")
    assert json.loads(completed.receipt_path.read_text())["state"] == "active"
    tool(config, "PostToolUse", agent_id="child", tool_use_id="child-tool", tool_response="done")
    assert json.loads(completed.receipt_path.read_text())["state"] == "active"
    capture_codex_hook(payload("Stop"), config)
    assert json.loads(completed.receipt_path.read_text())["state"] == "closed"


def test_concurrent_duplicate_deliveries_are_not_double_counted(config):
    from concurrent.futures import ThreadPoolExecutor

    capture_codex_hook(payload("UserPromptSubmit"), config)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: tool(config), range(12)))
    assert sum(result.accepted for result in results) == 1
    tool(config, "PostToolUse", tool_response="done")
    completed = capture_codex_hook(payload("Stop"), config)
    meta, _ = load_validated_run(materialize_turn(config, json.loads(completed.receipt_path.read_text())), config)
    assert meta["counts"]["tool_calls"] == 1


@pytest.mark.parametrize("redact_tool_name", [False, True])
def test_loop_signals_are_retained_and_deduplicated(config, redact_tool_name):
    if redact_tool_name:
        config = replace(config, redact_keys=[*config.redact_keys, "tool_name"])
    capture_codex_hook(payload("UserPromptSubmit"), config)
    for index in range(8):
        tool(config, tool_use_id=f"call-{index}")
        tool(config, "PostToolUse", tool_use_id=f"call-{index}", tool_response="done")
    completed = capture_codex_hook(payload("Stop"), config)
    meta, spans = load_validated_run(materialize_turn(config, json.loads(completed.receipt_path.read_text())), config)
    assert meta["counts"]["loop_warnings"] >= 1
    events = spans_to_events(spans)
    assert sum(event["event_type"] == "LOOP_WARNING" for event in events) == meta["counts"]["loop_warnings"]


def test_bad_tool_pair_and_topology_produce_import_recovery(config):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    tool(config)
    tool(config, "PostToolUse", tool_name="Different", tool_response="done")
    completed = capture_codex_hook(payload("Stop"), config)
    with pytest.raises(CodexHookInputError, match="match"):
        materialize_turn(config, json.loads(completed.receipt_path.read_text()))


def test_invalid_receipt_identity_is_rejected(config):
    with pytest.raises(CodexHookInputError, match="session_hash"):
        materialize_turn(config, {"session_hash": "../../path", "turn_hash": "x"})


def test_source_event_id_conflict_is_detected(config):
    capture_codex_hook(payload("UserPromptSubmit", event_id="source-event", reason="first"), config)
    with pytest.raises(CodexHookConflictError):
        capture_codex_hook(payload("UserPromptSubmit", event_id="source-event", reason="conflicting"), config)


def test_json_encoded_response_secrets_are_redacted_before_persistence(config):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    tool(config)
    completed_tool = tool(
        config, "PostToolUse", tool_response=json.dumps({"nested": {"token": "secret-json-response"}})
    )
    persisted = "\n".join(p.read_text() for p in (config.data_dir / "captures").rglob("*") if p.is_file())
    assert "secret-json-response" not in persisted
    assert json.loads(completed_tool.receipt_path.read_text())["complete_tools"] is True


@pytest.mark.parametrize(
    ("response", "errors"),
    [
        ({"isError": True, "content": [{"type": "text", "text": "tool failed"}]}, 1),
        ({"isError": False, "content": [{"type": "text", "text": "tool failed"}]}, 0),
        ("error: tool failed in message text", 0),
    ],
)
def test_explicit_mcp_tool_error_is_recorded_without_parsing_text(config, response, errors):
    capture_codex_hook(payload("UserPromptSubmit"), config)
    tool(config)
    tool(config, "PostToolUse", tool_response=response)
    completed = capture_codex_hook(payload("Stop"), config)
    meta, spans = load_validated_run(materialize_turn(config, json.loads(completed.receipt_path.read_text())), config)
    assert meta["counts"]["errors"] == errors
    assert meta["status"] == "ok"
    assert next(span for span in spans if span["name"] == "Bash")["status_code"] == ("ERROR" if errors else "OK")


def test_automatic_continuation_stop_with_new_content_is_a_new_snapshot(config):
    completed = finish(config)
    original = materialize_turn(config, json.loads(completed.receipt_path.read_text()))
    continuation = capture_codex_hook(
        payload("Stop", stop_hook_active=True, last_assistant_message="follow-up completion"), config
    )
    assert continuation.accepted
    assert not capture_codex_hook(
        payload("Stop", stop_hook_active=True, last_assistant_message="follow-up completion"), config
    ).accepted
    updated = materialize_turn(config, json.loads(continuation.receipt_path.read_text()))
    assert updated != original
    assert load_validated_run(original, config)[0]["counts"]["llm_calls"] == 0
    assert load_validated_run(updated, config)[0]["counts"]["llm_calls"] == 0


def test_later_session_shutdown_preserves_turn_timing_and_prior_snapshot(config, monkeypatch):
    from datetime import datetime, timezone

    completed = finish(config)
    first_id = materialize_turn(config, json.loads(completed.receipt_path.read_text()))
    first_meta, first_spans = load_validated_run(first_id, config)

    class MuchLater(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2030, 1, 1, tzinfo=timezone.utc)

    monkeypatch.setattr("maida.capture.codex_hook.datetime", MuchLater)
    capture_codex_hook({"session_id": "private-session", "hook_event_name": "SessionEnd"}, config)
    second_id = materialize_turn(config, json.loads(completed.receipt_path.read_text()))
    second_meta, second_spans = load_validated_run(second_id, config)
    assert second_id != first_id
    assert second_meta["ended_at"] == first_meta["ended_at"]
    assert second_meta["duration_ms"] == first_meta["duration_ms"]
    assert all(span["end_time"] <= second_meta["ended_at"] for span in second_spans)
    assert load_validated_run(first_id, config) == (first_meta, first_spans)
    source = json.loads(second_spans[0]["attributes"]["maida.meta"])["codex"]
    assert [item["event"] for item in source["trailing_lifecycle"]] == ["SessionEnd"]


@pytest.mark.parametrize("name_mode", ["redacted", "truncated"])
def test_altered_tool_names_preserve_distinct_signature_without_false_loops(config, name_mode):
    from maida.loopdetect import compute_signature

    config = replace(config, max_field_bytes=100)
    if name_mode == "redacted":
        config = replace(config, redact_keys=[*config.redact_keys, "tool_name"])
        names = [f"PrivateTool{index}" for index in range(8)]
    else:
        names = ["same-prefix-" * 20 + str(index) for index in range(8)]
    capture_codex_hook(payload("UserPromptSubmit"), config)
    for index, name in enumerate(names):
        tool(config, tool_use_id=f"call-{index}", tool_name=name)
        tool(config, "PostToolUse", tool_use_id=f"call-{index}", tool_name=name, tool_response="done")
    completed = capture_codex_hook(payload("Stop"), config)
    meta, spans = load_validated_run(materialize_turn(config, json.loads(completed.receipt_path.read_text())), config)
    tools = [span for span in spans if "maida.tool_name" in span["attributes"]]
    assert len({span["attributes"]["maida.tool_name"] for span in tools}) == 8
    assert meta["counts"]["loop_warnings"] == 0
    tool_events = [event for event in spans_to_events(spans) if event["event_type"] == "TOOL_CALL"]
    assert len({compute_signature(event) for event in tool_events}) == 8
    for span in tools:
        source = json.loads(span["attributes"]["maida.meta"])["codex"]
        assert source["tool_name_mapping"] == "opaque"
        assert source["terminal_attributes"]["tool_name"] not in names
        assert source["normalized_tool_name"].startswith("redacted-tool-")
    assert all(name not in json.dumps(spans) for name in names)


@pytest.mark.parametrize("redact", [True, False])
def test_ordinary_conversation_is_not_persisted_anywhere(config, redact):
    config = replace(config, redact=redact)
    prompt = "ordinary-user-prompt-6f7bea2-never-store"
    assistant = "ordinary-assistant-answer-341e91-never-store"
    transcript = "private-transcript-58c019-never-store"
    capture_codex_hook(payload("SessionStart", transcript=transcript, messages=[prompt, assistant]), config)
    capture_codex_hook(payload("UserPromptSubmit", prompt=prompt, transcript_content=transcript), config)
    tool(config, tool_input={"command": "cat README.md", "token": "sensitive-tool-token"})
    tool(config, "PostToolUse", tool_response={"output": "behavioral-result", "token": "sensitive-result-token"})
    completed = capture_codex_hook(payload("Stop", last_assistant_message=assistant), config)
    capture_codex_hook(payload("SessionEnd", transcript=transcript), config)
    materialize_turn(config, json.loads(completed.receipt_path.read_text()))
    for area in ["captures", "onboarding", "runs"]:
        files = [p for p in (config.data_dir / area).rglob("*") if p.is_file()]
        assert files, area
        stored = "\n".join(p.read_text() for p in files)
        assert prompt not in stored
        assert assistant not in stored
        assert transcript not in stored
    stored = "\n".join(p.read_text() for p in (config.data_dir / "runs").rglob("*") if p.is_file())
    assert "cat README.md" in stored
    assert "behavioral-result" in stored
    if redact:
        assert "sensitive-tool-token" not in stored
        assert "sensitive-result-token" not in stored


@pytest.mark.parametrize("event", ["PermissionRequest", "SubagentStart", "SubagentStop", "PreCompact", "PostCompact"])
def test_extended_events_are_outside_first_capture_surface(config, event):
    with pytest.raises(CodexHookInputError, match="unsupported hook event"):
        capture_codex_hook(payload(event), config)
    assert not (config.data_dir / "captures").exists()
