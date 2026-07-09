"""Golden trace fixture coverage for deterministic OTel storage examples."""

import json
import shutil
from pathlib import Path

import pytest

from maida.assertions import AssertionPolicy, run_assertions
from maida.baseline import create_baseline
from maida.config import load_config
from maida.constants import SPEC_VERSION
from maida.diff import compute_diff
from maida.events import EventType, spans_to_events
from maida.loopdetect import compute_signature, detect_loop
from maida.storage import (
    RunValidationError,
    load_run_for_analysis,
    load_run_meta,
    load_spans,
    load_validated_run,
)

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "traces"

CURRENT_FIXTURES = {
    "normal": "10000000000000000000000000000001",
    "tool-call-spike": "10000000000000000000000000000002",
    "loop": "10000000000000000000000000000003",
    "missing-terminal-state": "10000000000000000000000000000004",
    "guardrail": "10000000000000000000000000000005",
    "latency-cost-envelope": "10000000000000000000000000000006",
}

MALFORMED_FIXTURES = {
    "missing-spans": "20000000000000000000000000000001",
    "bad-metadata": "20000000000000000000000000000002",
    "invalid-spans": "20000000000000000000000000000003",
    "unsupported-version": "20000000000000000000000000000004",
}

EXTERNAL_CURRENT_FIXTURES = {
    "maida-ts": {
        "normal": "30000000000000000000000000000001",
        "tool-loop": "30000000000000000000000000000002",
        "missing-terminal-state": "30000000000000000000000000000003",
    },
    "opencode-plugin": {
        "normal": "70000000000000000000000000000001",
        "tool-loop": "70000000000000000000000000000002",
        "missing-terminal-state": "70000000000000000000000000000003",
    },
}

EXTERNAL_MALFORMED_FIXTURES = {
    "maida-ts": {
        "invalid-spans": "40000000000000000000000000000001",
    },
    "opencode-plugin": {
        "invalid-span-id": "70000000000000000000000000000004",
    },
}

EXTERNAL_NORMAL_EXPECTATIONS = {
    "maida-ts": {
        "trace_id": "30000000000000000000000000000001",
        "run_name": "ts-normal",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "total_tokens": 25,
        "tool_name": "search",
        "tool_args": {"query": "maida"},
        "tool_result": {"results": 1},
    },
    "opencode-plugin": {
        "trace_id": "70000000000000000000000000000001",
        "run_name": "opencode:fixture-normal",
        "model": "unknown",
        "provider": "unknown",
        "total_tokens": None,
        "tool_name": "bash",
        "tool_args": {"command": "npm test"},
        "tool_result": "18 passed",
    },
}

EXTERNAL_LOOP_EXPECTATIONS = {
    "maida-ts": {
        "trace_id": "30000000000000000000000000000002",
        "run_name": "ts-tool-loop",
        "tool_name": "lookup",
        "tool_args": {"id": "A"},
        "signature": "TOOL_CALL:lookup args:{id:str}",
        "stored_pattern": "TOOL_CALL:lookup",
    },
    "opencode-plugin": {
        "trace_id": "70000000000000000000000000000002",
        "run_name": "opencode:fixture-tool-loop",
        "tool_name": "search",
        "tool_args": {"query": "same query"},
        "signature": "TOOL_CALL:search args:{query:str}",
        "stored_pattern": "TOOL_CALL:search",
    },
}

EXTERNAL_RUNNING_EXPECTATIONS = {
    "maida-ts": {
        "trace_id": "30000000000000000000000000000003",
        "run_name": "ts-running",
        "event_type": EventType.TOOL_CALL.value,
        "event_name": "streaming_tool",
        "payload": {
            "tool_name": "streaming_tool",
            "args": {"stream": True},
            "result": None,
        },
    },
    "opencode-plugin": {
        "trace_id": "70000000000000000000000000000003",
        "run_name": "opencode:fixture-running",
        "event_type": EventType.LLM_CALL.value,
        "event_name": "unknown",
        "payload": {
            "model": "unknown",
            "response": "I am still working on the task.",
            "total_tokens": None,
        },
    },
}

REQUIRED_META_FIELDS = {
    "spec_version",
    "trace_id",
    "run_name",
    "started_at",
    "ended_at",
    "duration_ms",
    "status",
    "counts",
}
REQUIRED_SPAN_FIELDS = {
    "trace_id",
    "span_id",
    "parent_span_id",
    "name",
    "kind",
    "start_time",
    "end_time",
    "duration_ms",
    "attributes",
    "events",
    "status_code",
    "status_description",
}


def _install_fixture(config, group: str, name: str) -> Path:
    source = FIXTURE_ROOT / group / name
    trace_id = _trace_id_for(group, name)
    dest = config.data_dir / "runs" / trace_id
    shutil.copytree(source, dest)
    return dest


def _install_external_fixture(
    config,
    source_name: str,
    group: str,
    name: str,
) -> tuple[str, Path]:
    source = FIXTURE_ROOT / "external" / source_name / group / name
    trace_id = _read_json(source / "meta.json")["trace_id"]
    dest = config.data_dir / "runs" / trace_id
    shutil.copytree(source, dest)
    return trace_id, dest


def _external_cases(fixtures: dict[str, dict[str, str]]) -> list[tuple[str, str, str]]:
    return [
        (source, name, trace_id)
        for source, source_fixtures in sorted(fixtures.items())
        for name, trace_id in sorted(source_fixtures.items())
    ]


def _trace_id_for(group: str, name: str) -> str:
    if group == "current":
        return CURRENT_FIXTURES[name]
    if group == "malformed":
        return MALFORMED_FIXTURES[name]
    raise KeyError(group)


def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_jsonl(path: Path) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _validate_fixture_shape(run_dir: Path, expected_trace_id: str) -> list[dict]:
    meta_path = run_dir / "meta.json"
    spans_path = run_dir / "spans.jsonl"
    assert meta_path.is_file()
    assert spans_path.is_file()

    meta = _read_json(meta_path)
    assert REQUIRED_META_FIELDS <= set(meta)
    assert meta["trace_id"] == expected_trace_id
    assert meta["status"] in {"running", "ok", "error"}
    assert set(meta["counts"]) == {
        "llm_calls",
        "tool_calls",
        "errors",
        "loop_warnings",
    }
    assert meta["spec_version"] == SPEC_VERSION

    spans = _read_jsonl(spans_path)
    assert spans
    root_spans = []
    for span in spans:
        assert REQUIRED_SPAN_FIELDS <= set(span)
        assert span["trace_id"] == expected_trace_id
        assert len(span["span_id"]) == 16
        assert span["parent_span_id"] is None or len(span["parent_span_id"]) == 16
        assert isinstance(span["attributes"], dict)
        assert isinstance(span["events"], list)
        if span["parent_span_id"] is None:
            root_spans.append(span)
    if meta["status"] != "running":
        assert len(root_spans) == 1
    return spans


@pytest.mark.parametrize("name,trace_id", sorted(CURRENT_FIXTURES.items()))
def test_current_golden_trace_fixtures_are_documented_and_valid(name, trace_id):
    run_dir = FIXTURE_ROOT / "current" / name

    assert (run_dir / "README.md").is_file()
    _validate_fixture_shape(run_dir, trace_id)


@pytest.mark.parametrize("name", sorted(MALFORMED_FIXTURES))
def test_malformed_golden_trace_fixtures_are_documented(name):
    run_dir = FIXTURE_ROOT / "malformed" / name

    assert (run_dir / "README.md").is_file()


@pytest.mark.parametrize(
    "source,name,trace_id", _external_cases(EXTERNAL_CURRENT_FIXTURES)
)
def test_external_current_trace_fixtures_are_documented_and_valid(
    source, name, trace_id
):
    run_dir = FIXTURE_ROOT / "external" / source / "current" / name

    assert (run_dir / "README.md").is_file()
    _validate_fixture_shape(run_dir, trace_id)


@pytest.mark.parametrize(
    "source,name,trace_id", _external_cases(EXTERNAL_MALFORMED_FIXTURES)
)
def test_external_malformed_trace_fixtures_are_documented(source, name, trace_id):
    run_dir = FIXTURE_ROOT / "external" / source / "malformed" / name

    assert (run_dir / "README.md").is_file()
    assert (run_dir / "meta.json").is_file()
    assert (run_dir / "spans.jsonl").is_file()
    assert _read_json(run_dir / "meta.json")["trace_id"] == trace_id


def test_golden_normal_run_parses_and_projects_to_events(temp_data_dir):
    config = load_config()
    trace_id = CURRENT_FIXTURES["normal"]
    _install_fixture(config, "current", "normal")

    meta = load_run_meta(trace_id, config)
    spans = load_spans(trace_id, config)
    events = spans_to_events(spans)

    assert meta["run_name"] == "golden-normal"
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        EventType.LLM_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.RUN_END.value,
    ]
    llm_event = next(e for e in events if e["event_type"] == EventType.LLM_CALL.value)
    assert llm_event["payload"]["usage"]["total_tokens"] == 25
    tool_event = next(e for e in events if e["event_type"] == EventType.TOOL_CALL.value)
    assert tool_event["payload"]["tool_name"] == "search"
    assert tool_event["payload"]["args"] == {"query": "maida"}


def test_golden_loop_fixture_drives_signature_derivation(temp_data_dir):
    config = load_config()
    trace_id = CURRENT_FIXTURES["loop"]
    _install_fixture(config, "current", "loop")
    events = spans_to_events(load_spans(trace_id, config))
    action_events = [
        event
        for event in events
        if event["event_type"] in {EventType.LLM_CALL.value, EventType.TOOL_CALL.value}
    ]

    assert [compute_signature(event) for event in action_events] == [
        "LLM_CALL:gpt-loop",
        "TOOL_CALL:lookup",
        "LLM_CALL:gpt-loop",
        "TOOL_CALL:lookup",
        "LLM_CALL:gpt-loop",
        "TOOL_CALL:lookup",
    ]
    loop = detect_loop(action_events, window=6, repetitions=3)
    assert loop is not None
    assert loop["pattern"] == "LLM_CALL:gpt-loop -> TOOL_CALL:lookup"


def test_golden_fixtures_drive_baseline_diff_and_assertions(temp_data_dir):
    config = load_config()
    normal_id = CURRENT_FIXTURES["normal"]
    spike_id = CURRENT_FIXTURES["tool-call-spike"]
    _install_fixture(config, "current", "normal")
    _install_fixture(config, "current", "tool-call-spike")

    baseline = create_baseline(normal_id, config)
    diff = compute_diff(spike_id, baseline=baseline, config=config)
    report = run_assertions(
        spike_id,
        AssertionPolicy(
            no_new_tools=True,
            max_tool_calls=2,
            step_tolerance=10.0,
            duration_tolerance=10.0,
        ),
        baseline=baseline,
        config=config,
    )

    assert baseline["summary"]["tool_calls"] == 1
    assert baseline["summary"]["total_tokens"] == 25
    assert diff.new_tools == ["calculator", "fetch_profile", "summarize"]
    assert not report.passed
    assert {result.check_name for result in report.results if not result.passed} == {
        "tool_calls",
        "new_tools",
    }


def test_golden_latency_cost_fixture_changes_envelope(temp_data_dir):
    config = load_config()
    normal_id = CURRENT_FIXTURES["normal"]
    envelope_id = CURRENT_FIXTURES["latency-cost-envelope"]
    _install_fixture(config, "current", "normal")
    _install_fixture(config, "current", "latency-cost-envelope")

    baseline = create_baseline(normal_id, config)
    diff = compute_diff(envelope_id, baseline=baseline, config=config)
    report = run_assertions(
        envelope_id,
        AssertionPolicy(max_cost_tokens=100, max_duration_ms=500),
        baseline=baseline,
        config=config,
    )

    assert diff.summary_diff["total_tokens"] == (300, 25)
    assert diff.summary_diff["duration_ms"] == (2000, 150)
    assert not report.passed
    assert {result.check_name for result in report.results if not result.passed} == {
        "cost_tokens",
        "duration",
    }


def test_golden_guardrail_fixture_covers_error_and_guardrail_events(temp_data_dir):
    config = load_config()
    trace_id = CURRENT_FIXTURES["guardrail"]
    _install_fixture(config, "current", "guardrail")

    baseline = create_baseline(trace_id, config)
    report = run_assertions(
        trace_id,
        AssertionPolicy(no_guardrails=True, expect_status="ok"),
        config=config,
    )

    assert baseline["summary"]["errors"] == 1
    assert baseline["guardrail_events"]
    assert not report.passed
    assert {result.check_name for result in report.results if not result.passed} == {
        "no_guardrails",
        "expect_status",
    }


def test_golden_running_fixture_documents_missing_terminal_state(temp_data_dir):
    config = load_config()
    trace_id = CURRENT_FIXTURES["missing-terminal-state"]
    _install_fixture(config, "current", "missing-terminal-state")
    events = spans_to_events(load_spans(trace_id, config))

    assert load_run_meta(trace_id, config)["status"] == "running"
    assert [event["event_type"] for event in events] == [EventType.TOOL_CALL.value]


def test_malformed_golden_fixtures_cover_expected_failure_modes():
    missing_spans = FIXTURE_ROOT / "malformed" / "missing-spans"
    bad_metadata = FIXTURE_ROOT / "malformed" / "bad-metadata"
    invalid_spans = FIXTURE_ROOT / "malformed" / "invalid-spans"
    unsupported_version = FIXTURE_ROOT / "malformed" / "unsupported-version"

    assert (missing_spans / "meta.json").is_file()
    assert not (missing_spans / "spans.jsonl").exists()

    with pytest.raises(json.JSONDecodeError):
        _read_json(bad_metadata / "meta.json")

    with pytest.raises(json.JSONDecodeError):
        _read_jsonl(invalid_spans / "spans.jsonl")

    meta = _read_json(unsupported_version / "meta.json")
    assert meta["spec_version"] == "0.1"
    assert meta["spec_version"] != SPEC_VERSION


@pytest.mark.parametrize(
    "source,expected", sorted(EXTERNAL_NORMAL_EXPECTATIONS.items())
)
def test_external_normal_run_reads_and_projects_for_analysis(
    temp_data_dir, source, expected
):
    config = load_config()
    trace_id, _ = _install_external_fixture(config, source, "current", "normal")

    meta, spans = load_validated_run(trace_id, config)
    loaded_spans = load_spans(trace_id, config)
    events = spans_to_events(spans)
    resolved_id, analysis_meta, analysis_events = load_run_for_analysis(
        trace_id[:12], config
    )

    assert trace_id == expected["trace_id"]
    assert resolved_id == trace_id
    assert load_run_meta(trace_id, config) == meta
    assert loaded_spans == spans
    assert analysis_meta == meta
    assert analysis_events == events
    assert meta["run_name"] == expected["run_name"]
    assert meta["status"] == "ok"
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        EventType.LLM_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.RUN_END.value,
    ]

    llm_event = next(e for e in events if e["event_type"] == EventType.LLM_CALL.value)
    assert llm_event["payload"]["model"] == expected["model"]
    assert llm_event["payload"]["provider"] == expected["provider"]
    assert llm_event["payload"]["usage"]["total_tokens"] == expected["total_tokens"]

    tool_event = next(e for e in events if e["event_type"] == EventType.TOOL_CALL.value)
    assert tool_event["payload"]["tool_name"] == expected["tool_name"]
    assert tool_event["payload"]["args"] == expected["tool_args"]
    assert tool_event["payload"]["result"] == expected["tool_result"]


@pytest.mark.parametrize("source,expected", sorted(EXTERNAL_LOOP_EXPECTATIONS.items()))
def test_external_tool_loop_run_projects_loop_structure(
    temp_data_dir, source, expected
):
    config = load_config()
    trace_id, _ = _install_external_fixture(config, source, "current", "tool-loop")

    meta, spans = load_validated_run(trace_id, config)
    events = spans_to_events(spans)
    resolved_id, analysis_meta, analysis_events = load_run_for_analysis(
        trace_id, config
    )
    tool_events = [
        event for event in events if event["event_type"] == EventType.TOOL_CALL.value
    ]

    assert trace_id == expected["trace_id"]
    assert resolved_id == trace_id
    assert analysis_meta == meta
    assert analysis_events == events
    assert meta["run_name"] == expected["run_name"]
    assert meta["counts"]["tool_calls"] == 3
    assert meta["counts"]["loop_warnings"] == 1
    assert [event["event_type"] for event in events] == [
        EventType.RUN_START.value,
        EventType.TOOL_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.TOOL_CALL.value,
        EventType.LOOP_WARNING.value,
        EventType.RUN_END.value,
    ]
    assert [event["payload"]["tool_name"] for event in tool_events] == [
        expected["tool_name"],
        expected["tool_name"],
        expected["tool_name"],
    ]
    assert [event["payload"]["args"] for event in tool_events] == [
        expected["tool_args"],
        expected["tool_args"],
        expected["tool_args"],
    ]

    signatures = [compute_signature(event) for event in tool_events]
    assert signatures == [
        expected["signature"],
        expected["signature"],
        expected["signature"],
    ]
    loop = detect_loop(tool_events, window=3, repetitions=3)
    assert loop is not None
    assert loop["pattern"] == expected["signature"]

    loop_warning = next(
        event for event in events if event["event_type"] == EventType.LOOP_WARNING.value
    )
    assert loop_warning["payload"]["pattern"] == expected["stored_pattern"]
    assert loop_warning["payload"]["repetitions"] == 3


@pytest.mark.parametrize(
    "source,expected", sorted(EXTERNAL_RUNNING_EXPECTATIONS.items())
)
def test_external_running_trace_allows_missing_terminal_state(
    temp_data_dir, source, expected
):
    config = load_config()
    trace_id, _ = _install_external_fixture(
        config, source, "current", "missing-terminal-state"
    )

    meta, spans = load_validated_run(trace_id, config)
    events = spans_to_events(load_spans(trace_id, config))
    resolved_id, analysis_meta, analysis_events = load_run_for_analysis(
        trace_id, config
    )

    assert trace_id == expected["trace_id"]
    assert resolved_id == trace_id
    assert analysis_meta == meta
    assert analysis_events == events
    assert spans == load_spans(trace_id, config)
    assert meta["run_name"] == expected["run_name"]
    assert meta["status"] == "running"
    assert meta["ended_at"] is None
    assert meta["duration_ms"] is None
    assert [event["event_type"] for event in events] == [expected["event_type"]]
    assert events[0]["name"] == expected["event_name"]

    for key, value in expected["payload"].items():
        if key == "total_tokens":
            assert events[0]["payload"]["usage"]["total_tokens"] == value
        else:
            assert events[0]["payload"][key] == value


@pytest.mark.parametrize(
    "source,name,expected_problem",
    [
        (
            "maida-ts",
            "invalid-spans",
            "spans.jsonl line 1 is malformed JSON",
        ),
        (
            "opencode-plugin",
            "invalid-span-id",
            "spans.jsonl line 1 has an invalid span_id",
        ),
    ],
)
def test_external_malformed_traces_are_rejected_by_python_validator(
    temp_data_dir, source, name, expected_problem
):
    config = load_config()
    trace_id, _ = _install_external_fixture(config, source, "malformed", name)

    with pytest.raises(RunValidationError) as validation_error:
        load_validated_run(trace_id, config)
    assert expected_problem in validation_error.value.problem

    with pytest.raises(RunValidationError) as analysis_error:
        load_run_for_analysis(trace_id, config)
    assert expected_problem in analysis_error.value.problem
