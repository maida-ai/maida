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
from maida.storage import load_run_meta, load_spans

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
    "spec_version",
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
