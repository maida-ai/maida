"""Reproduce Windows CI failures without requiring a Windows host."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from maida import record_tool_call, traced_run
from maida import baseline, starter
from maida._tracing._otel import span_to_dict
from maida.assertions import AssertionPolicy, run_assertions
from maida.config import load_config
from maida.events import spans_to_events
from maida.storage import load_run_meta, load_spans
from tests.conftest import get_latest_run_id


@pytest.mark.parametrize("fail_replace", [False, True])
def test_starter_closes_temporary_files_before_replace_and_cleanup(tmp_path, monkeypatch, fail_replace):
    monkeypatch.chdir(tmp_path)
    streams = []
    create = starter.tempfile.NamedTemporaryFile
    replace = starter.os.replace
    first = Path("first.txt")
    second = Path("second.txt")
    first.write_bytes(b"original")

    def track(**kwargs):
        stream = create(**kwargs)
        streams.append(stream)
        return stream

    def replace_closed(source, destination):
        assert streams[-1].closed, "Windows cannot replace an open temporary file"
        if fail_replace and destination == second:
            raise PermissionError("replacement denied")
        replace(source, destination)

    monkeypatch.setattr(starter.tempfile, "NamedTemporaryFile", track)
    monkeypatch.setattr(starter.os, "replace", replace_closed)
    if fail_replace:
        with pytest.raises(PermissionError, match="replacement denied"):
            starter.write_files({first: "new", second: "second"}, force=True)
        assert first.read_bytes() == b"original"
        assert not second.exists()
        assert set(tmp_path.iterdir()) == {tmp_path / first}
    else:
        starter.write_files({first: "new", second: "second"}, force=True)
        assert first.read_bytes() == b"new"
        assert second.read_bytes() == b"second"
        assert set(tmp_path.iterdir()) == {tmp_path / first, tmp_path / second}
    assert all(stream.closed for stream in streams)


@pytest.mark.parametrize("end_time, expected", [(100, 0), (101, 0), (None, None), (99, None)])
def test_zero_elapsed_span_is_measured_but_missing_or_backward_time_is_not(end_time, expected, temp_data_dir):
    span = SimpleNamespace(
        context=SimpleNamespace(trace_id=1, span_id=1),
        parent=None,
        start_time=100,
        end_time=end_time,
        attributes={},
        events=[],
        status=None,
        kind=None,
        name="coarse-clock",
    )
    assert span_to_dict(span, load_config())["duration_ms"] == expected


def test_coarse_clock_run_keeps_lifecycle_order_and_can_be_baselined(temp_data_dir, monkeypatch):
    monkeypatch.setattr("opentelemetry.sdk.trace.time_ns", lambda: 1_767_225_600_000_000_000)
    config = load_config()
    with traced_run(name="coarse-clock"):
        record_tool_call("Read")
        record_tool_call("Write")
    trace_id = get_latest_run_id(config)
    meta = load_run_meta(trace_id, config)
    events = spans_to_events(load_spans(trace_id, config))
    assert meta["duration_ms"] == 0
    assert [event["event_type"] for event in events] == ["RUN_START", "TOOL_CALL", "TOOL_CALL", "RUN_END"]
    assert [event["payload"]["tool_name"] for event in events if event["event_type"] == "TOOL_CALL"] == [
        "Read",
        "Write",
    ]
    captured = baseline.create_baseline(trace_id, config)
    assert captured["summary"]["duration_ms"] == 0
    report = run_assertions(trace_id, AssertionPolicy(max_duration_ms=0), config=config)
    assert report.passed


def test_baseline_rejects_dangling_symlink_before_windows_open_can_follow_it(tmp_path, monkeypatch, symlink_supported):
    path = tmp_path / "baseline.json"
    target = tmp_path / "missing.json"
    path.symlink_to(target)

    def unsafe_open(*args, **kwargs):
        pytest.fail("Windows exclusive-create can follow a dangling symlink; reject it before opening")

    monkeypatch.setattr(baseline, "open", unsafe_open, raising=False)
    with pytest.raises(FileExistsError, match="already exists"):
        baseline.save_baseline({}, path)
    assert path.is_symlink()
    assert not target.exists()
