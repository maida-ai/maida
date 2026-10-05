"""Normalize local Codex hook turns through the existing trace storage boundary."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from maida._tracing._otel import (
    MAIDA_ERROR_COUNT,
    MAIDA_ERROR_MESSAGE,
    MAIDA_ERROR_TYPE,
    MAIDA_EVENT_TYPE,
    MAIDA_LLM_COUNT,
    MAIDA_LOOP_WARNING_COUNT,
    MAIDA_RUN_NAME,
    MAIDA_TOOL_COUNT,
)
from maida.capture.common import _atomic_json, _canonical, _capture_lock, _session_hash
from maida.capture.codex_hook import (
    CodexHookInputError,
    _completion,
    _read_state,
    read_turn_records,
    receipt_path,
    turn_dir,
)
from maida.config import MaidaConfig
from maida.constants import SPEC_VERSION
from maida.events import span_to_event_dict
from maida.loopdetect import detect_loop, pattern_key
from maida.storage import install_validated_run, load_validated_run

_MAPPING_VERSION = 1
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _span(
    trace_id: str,
    identity: str,
    parent: str | None,
    name: str,
    start: str,
    end: str,
    attrs: dict,
    *,
    status: str = "OK",
    events: list | None = None,
) -> dict:
    elapsed = (
        datetime.fromisoformat(end.replace("Z", "+00:00")) - datetime.fromisoformat(start.replace("Z", "+00:00"))
    ).total_seconds()
    return {
        "trace_id": trace_id,
        "span_id": _session_hash(identity)[:16],
        "parent_span_id": parent,
        "name": name,
        "kind": "INTERNAL",
        "start_time": start,
        "end_time": end,
        "duration_ms": max(0, int(elapsed * 1000)),
        "attributes": attrs,
        "events": events or [],
        "status_code": status,
        "status_description": "",
    }


def normalize_codex_turn(records: list[dict], config: MaidaConfig, receipt: dict) -> tuple[dict, list[dict]]:
    """Map paired observed tools; model usage is unavailable from hooks."""
    if not records:
        raise CodexHookInputError("Codex turn has no evidence")
    state, has_start, paired = _completion(records)
    if state != "closed" or not has_start or not paired:
        raise CodexHookInputError("Codex turn is not complete; finish the root turn and retry maida check")
    fingerprint = _session_hash(
        _canonical(
            {
                "mapping_version": _MAPPING_VERSION,
                "records": records,
                "session_context": receipt.get("session_context", {}),
            }
        )
    )
    trace_id = _session_hash(
        _canonical([config.project_id, receipt["session_hash"], receipt["turn_hash"], fingerprint])
    )[:32]
    root_id = _session_hash("root")[:16]
    stop_index = max(
        index
        for index, item in enumerate(records)
        if item["event"] == "Stop" and not item["source_keys"].get("agent_id")
    )
    turn_records = records[: stop_index + 1]
    trailing_lifecycle = records[stop_index + 1 :]
    start = min(item["observed_at"] for item in turn_records)
    end = records[stop_index]["observed_at"]
    coverage = {
        "observed": ["turn start", "paired tool hook events", "root stop"],
        "outside_scope": [
            "private transcripts",
            "prompt and assistant text",
            "subagent lifecycle and topology",
            "model calls",
            "tokens",
            "cost",
            "tools without hook deliveries",
            "correctness",
        ],
        "completion": "root Stop and paired observed tools",
        "client_identity": "native Codex hooks; no transcript-based inference",
    }
    source = {
        "mapping_version": _MAPPING_VERSION,
        "source": "codex-hook",
        "session_hash": receipt["session_hash"],
        "turn_hash": receipt["turn_hash"],
        "source_fingerprint": fingerprint,
        "session_context": receipt.get("session_context", {}),
        "coverage": coverage,
        "trailing_lifecycle": trailing_lifecycle,
    }
    spans = []
    starts: dict[tuple, dict] = {}
    actions = []
    opaque_tool_names = 0
    for item in turn_records:
        event, payload = item["event"], item["payload"]
        agent_id = item["source_keys"].get("agent_id")
        parent = root_id
        if event == "PreToolUse":
            starts[(agent_id, item["source_keys"]["tool_use_id"])] = item
            continue
        if event == "PostToolUse":
            key = (agent_id, item["source_keys"]["tool_use_id"])
            initial = starts.get(key)
            if initial is None or initial["source_keys"]["tool_name"] != item["source_keys"]["tool_name"]:
                raise CodexHookInputError("Codex tool completion does not match its start")
            response = payload.get("tool_response")
            failed = isinstance(response, dict) and (
                response.get("exit_code", 0) != 0 or response.get("success") is False or response.get("isError") is True
            )
            source_tool_name_key = item["source_keys"]["tool_name"]
            exact_name = _session_hash(payload["tool_name"]) == source_tool_name_key
            tool_name = payload["tool_name"] if exact_name else f"redacted-tool-{source_tool_name_key}"
            opaque_tool_names += not exact_name
            attrs: dict[str, Any] = {
                "maida.tool_name": tool_name,
                "maida.meta": _canonical(
                    {
                        "codex": {
                            "mapping_version": _MAPPING_VERSION,
                            "source_kind": "tool_hook_pair",
                            "source_tool_use_id": payload["tool_use_id"],
                            "source_tool_name_key": source_tool_name_key,
                            "normalized_tool_name": tool_name,
                            "tool_name_mapping": "exact" if exact_name else "opaque",
                            "start_attributes": initial["payload"],
                            "terminal_attributes": payload,
                        }
                    }
                ),
            }
            if failed:
                attrs.update(
                    {
                        MAIDA_ERROR_TYPE: "CodexToolError",
                        MAIDA_ERROR_MESSAGE: str(
                            payload.get("error") or payload.get("reason") or "tool returned an error"
                        ),
                    }
                )
            projected = _span(
                trace_id,
                f"tool:{agent_id}:{item['source_keys']['tool_use_id']}",
                parent,
                tool_name,
                initial["observed_at"],
                item["observed_at"],
                attrs,
                status="ERROR" if failed else "OK",
                events=[
                    {
                        "name": "maida.tool.args",
                        "timestamp": initial["observed_at"],
                        "attributes": {"args": _canonical(initial["payload"]["tool_input"])},
                    },
                    {
                        "name": "maida.tool.result",
                        "timestamp": item["observed_at"],
                        "attributes": {"result": _canonical(response)},
                    },
                ],
            )
            spans.append(projected)
            actions.append(projected)
            continue
        spans.append(
            _span(
                trace_id,
                f"event:{item['delivery_id']}",
                parent,
                f"codex.{event}",
                item["observed_at"],
                item["observed_at"],
                {
                    "maida.meta": _canonical(
                        {
                            "codex": {
                                "mapping_version": _MAPPING_VERSION,
                                "source_kind": "hook",
                                "source_attributes": payload,
                            }
                        }
                    )
                },
            )
        )
    loop_keys = set()
    window = []
    for action in actions:
        window.append(span_to_event_dict(action))
        window = window[-config.loop_window :]
        warning = detect_loop(window, config.loop_window, config.loop_repetitions)
        if warning is None:
            continue
        key = pattern_key(warning)
        if key in loop_keys:
            continue
        loop_keys.add(key)
        spans.append(
            _span(
                trace_id,
                f"loop:{key}",
                root_id,
                "loop_warning",
                action["end_time"],
                action["end_time"],
                {MAIDA_EVENT_TYPE: "LOOP_WARNING"},
                events=[{"name": "maida.loop.warning", "timestamp": action["end_time"], "attributes": warning}],
            )
        )
    coverage["tool_name_mapping"] = {
        "exact": len(actions) - opaque_tool_names,
        "opaque": opaque_tool_names,
        "description": "Changed source names use opaque identity labels to preserve distinct tools after redaction or truncation",
    }
    counts = {
        "llm_calls": 0,
        "tool_calls": len(actions),
        "errors": sum(span["status_code"] == "ERROR" for span in spans),
        "loop_warnings": len(loop_keys),
    }
    run_name = "Captured Codex turn"
    root = _span(
        trace_id,
        "root",
        None,
        run_name,
        start,
        end,
        {
            MAIDA_RUN_NAME: run_name,
            MAIDA_LLM_COUNT: 0,
            MAIDA_TOOL_COUNT: counts["tool_calls"],
            MAIDA_ERROR_COUNT: counts["errors"],
            MAIDA_LOOP_WARNING_COUNT: counts["loop_warnings"],
            "maida.meta": _canonical({"codex": source}),
        },
    )
    meta = {
        "spec_version": SPEC_VERSION,
        "trace_id": trace_id,
        "run_name": run_name,
        "started_at": start,
        "ended_at": end,
        "duration_ms": root["duration_ms"],
        "status": "ok",
        "counts": counts,
    }
    return meta, [root, *spans]


def import_codex_turn(config: MaidaConfig, receipt: dict) -> str:
    """Revalidate current evidence under capture lock, then install once."""
    for key in ("session_hash", "turn_hash"):
        if not isinstance(receipt.get(key), str) or not _HASH.fullmatch(receipt[key]):
            raise CodexHookInputError(f"Codex receipt has invalid {key}")
    path = receipt_path(config, receipt["session_hash"], receipt["turn_hash"])
    with _capture_lock(config.data_dir.expanduser() / "captures", "codex"):
        current = _read_state(path)
        if current.get("project_id") != config.project_id:
            raise CodexHookInputError("Codex receipt belongs to another repository installation")
        if (
            current.get("state") != "closed"
            or current.get("has_start") is not True
            or current.get("complete_tools") is not True
        ):
            raise CodexHookInputError("Codex turn is not complete; finish the root turn and retry maida check")
        records = read_turn_records(turn_dir(config, receipt["session_hash"], receipt["turn_hash"]) / "events.jsonl")
        try:
            meta, spans = normalize_codex_turn(records, config, current)
        except (KeyError, TypeError, ValueError) as exc:
            raise CodexHookInputError(f"Codex capture cannot be normalized: {exc}") from exc
        trace_id = meta["trace_id"]
        try:
            existing_meta, existing_spans = load_validated_run(trace_id, config)
        except FileNotFoundError:
            install_validated_run(meta, spans, config)
        else:
            if existing_meta != meta or existing_spans != spans:
                raise CodexHookInputError("Codex snapshot destination has conflicting evidence")
        current["trace_id"] = trace_id
        _atomic_json(path, current)
    return trace_id
