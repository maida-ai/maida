"""Offline langfuse importer tests."""

from __future__ import annotations
from tests.support.paths import FIXTURES_ROOT
import json
import pytest
from typer.testing import CliRunner
from maida.assertions import AssertionPolicy, RegressionReasonCode, run_assertions
from maida.baseline import create_baseline
from maida.config import load_config
from maida.events import spans_to_events
from maida.integrations.langfuse import (
    LangfuseImportError,
    LangfuseInputError,
    import_langfuse_traces,
    normalize_langfuse_trace,
)
from maida.storage import install_validated_run, load_validated_run
from tests.support.langfuse import _observation


runner = CliRunner()

FIXTURE_PATH = FIXTURES_ROOT / "langfuse" / "api-v2" / "observations.json"


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
    good = normalize_langfuse_trace([row for row in rows if row["traceId"] == "fixture-good-trace"], config)
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
    tool_event = next(event for event in spans_to_events(spans) if event["event_type"] == "TOOL_CALL")
    assert tool_event["payload"]["result"] == {"version": 1}
