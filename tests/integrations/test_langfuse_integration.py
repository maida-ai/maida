"""Offline conformance tests for the Langfuse import adapter."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from typer.testing import CliRunner

from maida.assertions import AssertionPolicy, RegressionReasonCode, run_assertions
from maida.baseline import create_baseline
from maida.cli import app
from maida.config import load_config
from maida.events import EventType, spans_to_events
from maida.integrations.langfuse import (
    IncompleteLangfuseTrace,
    LangfuseClient,
    LangfuseImportError,
    LangfuseInputError,
    import_langfuse_traces,
    normalize_langfuse_trace,
)
from maida.storage import install_validated_run, list_runs, load_validated_run


runner = CliRunner()
FIXTURE_PATH = (
    Path(__file__).parents[1] / "fixtures" / "langfuse" / "api-v2" / "observations.json"
)


def _observation(
    observation_id: str,
    *,
    trace_id: str = "source-trace-good",
    observation_type: str = "TOOL",
    name: str = "lookup",
    parent_id: str | None = None,
    start_second: int = 0,
    end_second: int | None = 1,
    level: str = "DEFAULT",
    input_value: object = None,
    output_value: object = None,
    metadata: object = None,
    usage: dict[str, int] | None = None,
) -> dict:
    return {
        "id": observation_id,
        "traceId": trace_id,
        "projectId": "project-public",
        "parentObservationId": parent_id,
        "type": observation_type,
        "name": name,
        "startTime": f"2026-01-01T00:00:{start_second:02d}.000Z",
        "endTime": (
            f"2026-01-01T00:00:{end_second:02d}.000Z"
            if end_second is not None
            else None
        ),
        "level": level,
        "statusMessage": "source failure" if level == "ERROR" else None,
        "input": input_value,
        "output": output_value,
        "metadata": metadata or {},
        "providedModelName": "model-a" if observation_type == "GENERATION" else None,
        "modelParameters": {"temperature": 0},
        "usageDetails": usage or {},
        "costDetails": {"total": 0.001},
        "totalCost": 0.001,
        "traceName": "nightly-support-job",
        "sessionId": "session-public",
        "environment": "test",
        "release": "release-public",
        "tags": ["nightly"],
    }


def test_normalize_preserves_tree_maps_calls_and_redacts(temp_data_dir):
    config = load_config()
    rows = [
        _observation(
            "agent-1",
            observation_type="AGENT",
            name="worker",
            start_second=0,
            end_second=5,
            metadata={
                "password": "must-not-reach-disk",
                "oversized": "x" * (config.max_field_bytes + 1),
            },
        ),
        _observation(
            "generation-1",
            observation_type="GENERATION",
            name="answer",
            parent_id="agent-1",
            start_second=1,
            end_second=2,
            input_value=json.dumps({"api_key": "also-secret", "prompt": "hello"}),
            output_value="hi",
            usage={
                "input": 10,
                "output": 5,
                "total": 15,
                "cache_read_input_tokens": 4,
            },
        ),
        _observation(
            "tool-1",
            parent_id="agent-1",
            start_second=3,
            end_second=4,
            input_value={"query": "docs"},
            output_value={"count": 1},
        ),
    ]

    normalized = normalize_langfuse_trace(rows, config)
    serialized = json.dumps({"meta": normalized.meta, "spans": normalized.spans})

    assert "must-not-reach-disk" not in serialized
    assert "also-secret" not in serialized
    assert "__REDACTED__" in serialized
    assert "__TRUNCATED__" in serialized
    assert len(normalized.trace_id) == 32
    assert normalized.meta["run_name"] == "nightly-support-job"
    assert normalized.meta["counts"] == {
        "llm_calls": 1,
        "tool_calls": 1,
        "errors": 0,
        "loop_warnings": 0,
    }

    events = spans_to_events(normalized.spans)
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        "UNKNOWN",
        EventType.LLM_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.RUN_END.value,
    ]
    llm = next(event for event in events if event["event_type"] == "LLM_CALL")
    assert llm["payload"]["usage"] == {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
    }
    tool = next(event for event in events if event["event_type"] == "TOOL_CALL")
    assert tool["payload"]["args"] == {"query": "docs"}
    assert tool["payload"]["result"] == {"count": 1}

    spans_by_source_id = {}
    for span in normalized.spans:
        raw_meta = span["attributes"].get("maida.meta")
        if raw_meta:
            source_id = json.loads(raw_meta).get("langfuse", {}).get("observation_id")
            if source_id:
                spans_by_source_id[source_id] = span
    assert (
        spans_by_source_id["generation-1"]["parent_span_id"]
        == spans_by_source_id["agent-1"]["span_id"]
    )


def test_normalize_maps_legacy_camel_case_usage_fields(temp_data_dir):
    row = _observation("generation-1", observation_type="GENERATION", usage={})
    row.update({"promptTokens": 8, "completionTokens": 3, "totalTokens": 11})

    normalized = normalize_langfuse_trace([row], load_config())
    llm = next(
        event
        for event in spans_to_events(normalized.spans)
        if event["event_type"] == "LLM_CALL"
    )

    assert llm["payload"]["usage"] == {
        "prompt_tokens": 8,
        "completion_tokens": 3,
        "total_tokens": 11,
    }


def test_normalize_attaches_missing_parent_to_synthetic_root(temp_data_dir):
    normalized = normalize_langfuse_trace(
        [_observation("tool-1", parent_id="not-in-export")], load_config()
    )
    root = next(span for span in normalized.spans if span["parent_span_id"] is None)
    tool = next(span for span in normalized.spans if span["name"] == "lookup")
    assert tool["parent_span_id"] == root["span_id"]


def test_normalize_preserves_physical_parent_for_logical_subagent_root(
    temp_data_dir,
):
    parent = _observation(
        "agent-1", observation_type="AGENT", name="supervisor", end_second=4
    )
    child = _observation(
        "agent-2",
        observation_type="AGENT",
        name="runtime-worker",
        parent_id="agent-1",
        start_second=1,
        end_second=3,
    )
    child["isRootObservation"] = True

    normalized = normalize_langfuse_trace([parent, child], load_config())
    by_name = {span["name"]: span for span in normalized.spans}

    assert (
        by_name["runtime-worker"]["parent_span_id"] == by_name["supervisor"]["span_id"]
    )


def test_normalize_rejects_parent_cycles(temp_data_dir):
    rows = [
        _observation("tool-1", parent_id="tool-2"),
        _observation("tool-2", parent_id="tool-1", start_second=2, end_second=3),
    ]

    with pytest.raises(LangfuseImportError, match="parent cycle"):
        normalize_langfuse_trace(rows, load_config())


def test_normalize_detects_historical_tool_loop(temp_data_dir):
    rows = [
        _observation(
            f"tool-{index}",
            name="repeat",
            start_second=index,
            end_second=index + 1,
            input_value={"query": "same"},
        )
        for index in range(3)
    ]

    normalized = normalize_langfuse_trace(rows, load_config())
    events = spans_to_events(normalized.spans)

    assert normalized.meta["counts"]["loop_warnings"] == 1
    assert [event["event_type"] for event in events].count("LOOP_WARNING") == 1


def test_normalize_preserves_completed_source_error(temp_data_dir):
    normalized = normalize_langfuse_trace(
        [_observation("tool-1", level="ERROR")], load_config()
    )
    tool = next(
        event
        for event in spans_to_events(normalized.spans)
        if event["event_type"] == "TOOL_CALL"
    )

    assert normalized.meta["status"] == "error"
    assert normalized.meta["counts"]["errors"] == 1
    assert tool["payload"]["status"] == "error"
    assert tool["payload"]["error"]["message"] == "source failure"


def test_normalize_rejects_incomplete_non_event_trace(temp_data_dir):
    with pytest.raises(IncompleteLangfuseTrace, match="incomplete observation"):
        normalize_langfuse_trace(
            [_observation("tool-1", end_second=None)], load_config()
        )


def test_normalize_allows_instantaneous_event_without_end_time(temp_data_dir):
    normalized = normalize_langfuse_trace(
        [
            _observation(
                "event-1",
                observation_type="EVENT",
                name="checkpoint",
                end_second=None,
            )
        ],
        load_config(),
    )
    event_span = next(span for span in normalized.spans if span["name"] == "checkpoint")
    assert event_span["end_time"] == event_span["start_time"]
    assert event_span["duration_ms"] == 0


def test_normalize_preserves_and_reports_future_observation_type(temp_data_dir):
    normalized = normalize_langfuse_trace(
        [
            _observation(
                "future-1",
                observation_type="FUTURE_CONTAINER",
                name="future-container",
            )
        ],
        load_config(),
    )

    assert normalized.unmapped_observation_types == ("FUTURE_CONTAINER",)
    assert any(span["name"] == "future-container" for span in normalized.spans)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._payload


def test_client_follows_cursor_pagination(monkeypatch):
    requests = []
    responses = [
        {"data": [{"id": "first"}], "meta": {"cursor": "next-page"}},
        {"data": [{"id": "second"}], "meta": {}},
    ]

    def fake_urlopen(request, timeout):
        requests.append((request, timeout))
        return _FakeResponse(responses.pop(0))

    monkeypatch.setattr("maida.integrations.langfuse.urlopen", fake_urlopen)
    client = LangfuseClient(
        base_url="https://example.langfuse.test",
        public_key="pk-public",
        secret_key="sk-private",
        timeout=7,
    )

    rows = client.fetch_observations({"traceId": "trace-one"})

    assert [row["id"] for row in rows] == ["first", "second"]
    assert len(requests) == 2
    second_query = parse_qs(urlsplit(requests[1][0].full_url).query)
    assert second_query["cursor"] == ["next-page"]
    assert requests[0][1] == 7
    assert requests[0][0].get_header("Authorization").startswith("Basic ")


def test_client_rejects_repeated_cursor(monkeypatch):
    def fake_urlopen(_request, timeout):
        assert timeout == 5
        return _FakeResponse({"data": [], "meta": {"cursor": "same"}})

    monkeypatch.setattr("maida.integrations.langfuse.urlopen", fake_urlopen)
    client = LangfuseClient("https://example.test", "public", "secret")

    with pytest.raises(LangfuseImportError, match="repeated pagination cursor"):
        client.fetch_observations({})


def test_client_rejects_base_url_paths_and_redacts_auth_errors(monkeypatch):
    with pytest.raises(LangfuseInputError, match=r"http\(s\) origin"):
        LangfuseClient("https://example.test/langfuse", "public", "secret")

    def unauthorized(request, timeout):
        assert timeout == 5
        raise HTTPError(request.full_url, 401, "unauthorized", None, None)

    monkeypatch.setattr("maida.integrations.langfuse.urlopen", unauthorized)
    client = LangfuseClient("https://example.test", "public-value", "secret-value")

    with pytest.raises(LangfuseImportError) as raised:
        client.fetch_observations({})

    assert "authentication failed" in str(raised.value)
    assert "public-value" not in str(raised.value)
    assert "secret-value" not in str(raised.value)


def test_range_discovery_hydrates_complete_traces(temp_data_dir):
    rows = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["data"]

    class FakeClient:
        def __init__(self):
            self.params = []

        def fetch_observations(self, params):
            self.params.append(params)
            if "filter" in params:
                return [rows[0], rows[2]]
            return [row for row in rows if row["traceId"] == params["traceId"]]

    client = FakeClient()
    summary = import_langfuse_traces(
        client,
        load_config(),
        from_time="2026-01-01T00:00:00Z",
        to_time="2026-01-03T00:00:00Z",
        trace_name="fixture-recurring-job",
        environments=("test",),
    )

    assert len(summary.imported) == 2
    assert len(client.params) == 3
    filters = json.loads(client.params[0]["filter"])
    assert {item["column"] for item in filters} == {
        "startTime",
        "traceName",
        "environment",
    }
    assert {item["traceId"] for item in client.params[1:]} == {
        "fixture-good-trace",
        "fixture-regression-trace",
    }


def test_invalid_range_is_input_error(temp_data_dir):
    class NoRequestClient:
        def fetch_observations(self, _params):
            raise AssertionError("invalid input must fail before an API request")

    with pytest.raises(LangfuseInputError, match="--from must be earlier"):
        import_langfuse_traces(
            NoRequestClient(),
            load_config(),
            from_time="2026-01-03T00:00:00Z",
            to_time="2026-01-01T00:00:00Z",
        )


def test_malformed_range_timestamp_is_input_error(temp_data_dir):
    class NoRequestClient:
        def fetch_observations(self, _params):
            raise AssertionError("invalid input must fail before an API request")

    with pytest.raises(LangfuseInputError, match="ISO-8601 timestamp"):
        import_langfuse_traces(
            NoRequestClient(),
            load_config(),
            from_time="not-a-timestamp",
            to_time="2026-01-01T00:00:00Z",
        )


def test_sanitized_fixture_imports_baselines_and_fails_regression_gate(
    temp_data_dir,
):
    rows = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["data"]
    config = load_config()
    good = normalize_langfuse_trace(
        [row for row in rows if row["traceId"] == "fixture-good-trace"], config
    )
    regression = normalize_langfuse_trace(
        [row for row in rows if row["traceId"] == "fixture-regression-trace"],
        config,
    )
    install_validated_run(good.meta, good.spans, config)
    install_validated_run(regression.meta, regression.spans, config)
    baseline = create_baseline(good.trace_id, config)
    report = run_assertions(
        regression.trace_id,
        AssertionPolicy(
            no_new_tools=True,
            no_loops=True,
            cost_tolerance=0,
        ),
        baseline,
        config,
    )

    assert not report.passed
    assert set(report.reason_codes) >= {
        RegressionReasonCode.NEW_TOOL_PATH,
        RegressionReasonCode.LOOP_DETECTED,
        RegressionReasonCode.COST_ENVELOPE_EXCEEDED,
    }


def test_cli_imports_and_idempotently_skips_trace(monkeypatch, temp_data_dir):
    rows = [_observation("tool-1")]

    def fake_fetch(_self, params):
        return rows if params.get("traceId") else [rows[0]]

    monkeypatch.setattr(LangfuseClient, "fetch_observations", fake_fetch)
    env = {
        "LANGFUSE_PUBLIC_KEY": "public-key",
        "LANGFUSE_SECRET_KEY": "secret-key",
    }
    args = ["import", "langfuse", "--trace-id", "source-trace-good", "--json"]

    first = runner.invoke(app, args, env=env)
    second = runner.invoke(app, args, env=env)

    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.stdout)
    assert len(first_payload["imported"]) == 1
    assert first_payload["skipped"] == []
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["skipped"][0]["reason"] == "already imported"

    config = load_config()
    runs = list_runs(10, config)
    assert len(runs) == 1
    trace_id = runs[0]["trace_id"]
    load_validated_run(trace_id, config)
    baseline = create_baseline(trace_id, config)
    assert baseline["source_run_name"] == "nightly-support-job"


def test_changed_source_trace_refuses_to_overwrite_import(temp_data_dir):
    rows = [_observation("tool-1", output_value={"version": 1})]

    class MutableClient:
        def fetch_observations(self, _params):
            return rows

    client = MutableClient()
    config = load_config()
    first = import_langfuse_traces(client, config, source_trace_id="source-trace-good")
    rows[0]["output"] = {"version": 2}

    with pytest.raises(LangfuseImportError, match="source trace changed"):
        import_langfuse_traces(client, config, source_trace_id="source-trace-good")

    _meta, spans = load_validated_run(first.imported[0]["trace_id"], config)
    tool_event = next(
        event for event in spans_to_events(spans) if event["event_type"] == "TOOL_CALL"
    )
    assert tool_event["payload"]["result"] == {"version": 1}


def test_cli_missing_credentials_is_input_error(temp_data_dir):
    result = runner.invoke(
        app,
        ["import", "langfuse", "--trace-id", "source-trace-good"],
        env={"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""},
    )

    assert result.exit_code == 2
    assert "LANGFUSE_PUBLIC_KEY" in result.stderr
    assert "secret-key" not in result.output


def test_cli_json_errors_remain_machine_readable(temp_data_dir):
    result = runner.invoke(
        app,
        ["import", "langfuse", "--trace-id", "source-trace-good", "--json"],
        env={"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""},
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error"]["kind"] == "invalid_input"
    assert "LANGFUSE_PUBLIC_KEY" in payload["error"]["message"]
