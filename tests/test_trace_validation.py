import json
from pathlib import Path

import pytest

from maida.assertions import AssertionPolicy, run_assertions
from maida.baseline import create_baseline
from maida.config import load_config
from maida.events import spans_to_events
from maida.storage import install_validated_run, load_validated_run
from maida.trace_validation import (
    TraceInputError,
    TraceValidationError,
    validate_trace_path,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "traces"
    / "external"
    / "emitter"
    / "current"
    / "multithread"
)
TRACE_ID = "80000000000000000000000000000001"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _copy_fixture(tmp_path: Path) -> Path:
    run_dir = tmp_path / "emitted-run"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(
        (FIXTURE / "meta.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (run_dir / "spans.jsonl").write_text(
        (FIXTURE / "spans.jsonl").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return run_dir


def test_validate_trace_path_accepts_directory_and_meta_file() -> None:
    from_directory = validate_trace_path(FIXTURE)
    from_meta = validate_trace_path(FIXTURE / "meta.json")

    assert from_directory.meta == from_meta.meta
    assert from_directory.spans == from_meta.spans
    assert from_directory.trace_id == TRACE_ID
    assert from_directory.spec_version == "0.2.0"
    assert from_directory.status == "ok"
    assert len(from_directory.spans) == 5


def test_validate_trace_path_is_read_only(tmp_path: Path) -> None:
    run_dir = _copy_fixture(tmp_path)
    before = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (run_dir / "meta.json", run_dir / "spans.jsonl")
    }

    validate_trace_path(run_dir)

    after = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (run_dir / "meta.json", run_dir / "spans.jsonl")
    }
    assert after == before


def test_external_fixture_installs_projects_and_asserts_end_to_end(
    temp_data_dir,
) -> None:
    validated = validate_trace_path(FIXTURE)
    config = load_config()
    install_validated_run(validated.meta, validated.spans, config)

    stored_meta, stored_spans = load_validated_run(TRACE_ID, config)
    events = spans_to_events(stored_spans)
    baseline = create_baseline(TRACE_ID, config)
    report = run_assertions(
        TRACE_ID,
        AssertionPolicy(no_new_tools=True, expect_status="ok"),
        baseline=baseline,
        config=config,
    )

    assert stored_meta == validated.meta
    assert [span["parent_span_id"] for span in stored_spans[2:]] == [
        "8000000000000000",
        "8000000000000002",
        "8000000000000003",
    ]
    assert [event["event_type"] for event in events] == [
        "RUN_START",
        "LLM_CALL",
        "TOOL_CALL",
        "LLM_CALL",
        "TOOL_CALL",
        "RUN_END",
    ]
    assert baseline["tool_call_sequence"] == ["delegate", "read"]
    assert report.passed


@pytest.mark.parametrize(
    ("mutate", "code", "location"),
    [
        (
            lambda meta, spans: meta.update(spec_version="1.0.0"),
            "unsupported_version",
            "meta.json.spec_version",
        ),
        (
            lambda meta, spans: spans[1].update(trace_id="9" * 32),
            "trace_id_mismatch",
            "spans.jsonl:2.trace_id",
        ),
        (
            lambda meta, spans: meta.update(started_at="2026-08-08 10:00:00Z"),
            "invalid_datetime",
            "meta.json.started_at",
        ),
        (
            lambda meta, spans: spans[1].update(span_id=spans[0]["span_id"]),
            "duplicate_span_id",
            "spans.jsonl:2.span_id",
        ),
        (
            lambda meta, spans: spans[1].update(parent_span_id=None),
            "multiple_roots",
            "spans.jsonl:2.parent_span_id",
        ),
        (
            lambda meta, spans: spans[1].update(parent_span_id="f" * 16),
            "missing_parent",
            "spans.jsonl:2.parent_span_id",
        ),
        (
            lambda meta, spans: spans[0].update(parent_span_id=spans[1]["span_id"]),
            "parent_cycle",
            "spans.jsonl:1.parent_span_id",
        ),
    ],
)
def test_validate_trace_path_reports_semantic_failures(
    tmp_path: Path, mutate, code: str, location: str
) -> None:
    run_dir = _copy_fixture(tmp_path)
    meta = _read_json(run_dir / "meta.json")
    spans = _read_jsonl(run_dir / "spans.jsonl")
    mutate(meta, spans)
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (run_dir / "spans.jsonl").write_text(
        "\n".join(json.dumps(span) for span in spans) + "\n", encoding="utf-8"
    )

    with pytest.raises(TraceValidationError) as excinfo:
        validate_trace_path(run_dir)

    assert any(
        diagnostic.code == code and diagnostic.location == location
        for diagnostic in excinfo.value.diagnostics
    )


def test_validate_trace_path_allows_incomplete_running_topology(tmp_path: Path) -> None:
    run_dir = _copy_fixture(tmp_path)
    meta = _read_json(run_dir / "meta.json")
    child = _read_jsonl(run_dir / "spans.jsonl")[1]
    meta.update(status="running", ended_at=None, duration_ms=None)
    child.update(parent_span_id="f" * 16, end_time=None, duration_ms=None)
    (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (run_dir / "spans.jsonl").write_text(json.dumps(child) + "\n", encoding="utf-8")

    validated = validate_trace_path(run_dir)

    assert validated.status == "running"
    assert validated.spans == [child]


def test_validate_trace_path_rejects_bad_input_paths(tmp_path: Path) -> None:
    wrong_file = tmp_path / "trace.json"
    wrong_file.write_text("{}", encoding="utf-8")
    missing_sibling = tmp_path / "meta.json"
    missing_sibling.write_text("{}", encoding="utf-8")

    with pytest.raises(TraceInputError, match="run directory or meta.json"):
        validate_trace_path(wrong_file)
    with pytest.raises(TraceInputError, match="spans.jsonl"):
        validate_trace_path(missing_sibling)
    with pytest.raises(TraceInputError, match="does not exist"):
        validate_trace_path(tmp_path / "missing")


def test_validate_trace_path_sanitizes_malformed_content(tmp_path: Path) -> None:
    run_dir = _copy_fixture(tmp_path)
    (run_dir / "spans.jsonl").write_text(
        '{"secret":"sk-test-DO-NOT-LEAK",\n', encoding="utf-8"
    )

    with pytest.raises(TraceValidationError) as excinfo:
        validate_trace_path(run_dir)

    diagnostic = excinfo.value.diagnostics[0]
    assert diagnostic.code == "malformed_json"
    assert diagnostic.location == "spans.jsonl:1"
    assert "sk-test-DO-NOT-LEAK" not in str(excinfo.value)
