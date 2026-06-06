"""
Unit tests for the integration run lifecycle registry.

Enter/exit callbacks are invoked only at the outermost run boundary;
nested @trace or traced_run do not invoke them again. Exit receives exception info when the run raises.
"""

import pytest

from maida import record_tool_call, trace, traced_run
from maida._integration_utils import (
    _clear_test_run_lifecycle_registry,
    register_run_enter,
    register_run_exit,
)
from maida.config import load_config
from maida.events import EventType, spans_to_events
from maida.storage import load_spans
from tests.conftest import get_latest_run_id


@pytest.fixture(autouse=True)
def clear_registry():
    """Clear run lifecycle callbacks before and after each test."""
    _clear_test_run_lifecycle_registry()
    yield
    _clear_test_run_lifecycle_registry()


def test_enter_and_exit_called_once_for_nested_runs(temp_data_dir):
    """With outer traced_run and nested @trace, run_enter and run_exit are each called exactly once."""
    enter_count = []
    exit_count = []

    def on_enter():
        enter_count.append(1)

    def on_exit(_run_id, _et, _ev, _tb):
        exit_count.append(1)

    register_run_enter(on_enter)
    register_run_exit(on_exit)

    @trace(name="inner")
    def inner():
        pass

    with traced_run(name="outer"):
        inner()

    assert len(enter_count) == 1, "run_enter should be called once for outermost run"
    assert len(exit_count) == 1, "run_exit should be called once for outermost run"


def test_exit_receives_exception_info_when_run_raises(temp_data_dir):
    """When the run raises, run_exit is called with run_id, exc_type, exc_value, and traceback set."""
    exit_info = []

    def on_exit(_run_id, exc_type, exc_value, tb):
        exit_info.append((exc_type, exc_value, tb))

    register_run_exit(on_exit)

    with pytest.raises(ValueError, match="run failed"):
        with traced_run(name="failing"):
            raise ValueError("run failed")

    assert len(exit_info) == 1
    exc_type, exc_value, tb = exit_info[0]
    assert exc_type is ValueError
    assert exc_value is not None and exc_value.args[0] == "run failed"
    assert tb is not None


def test_exit_receives_none_when_run_succeeds(temp_data_dir):
    """When the run completes normally, run_exit is called with (None, None, None)."""
    exit_info = []

    def on_exit(_run_id, exc_type, exc_value, tb):
        exit_info.append((exc_type, exc_value, tb))

    register_run_exit(on_exit)

    with traced_run(name="ok"):
        pass

    assert len(exit_info) == 1
    assert exit_info[0] == (None, None, None)


def test_run_exit_callback_event_before_run_end_and_exc_when_raises(temp_data_dir):
    """run_exit callback that records an event: event is written before RUN_END; exc_type is set when run raises."""
    exit_exc_type = []

    def on_exit(_run_id, exc_type, exc_value, tb):
        exit_exc_type.append(exc_type)
        record_tool_call(
            "run_exit_flush", args={}, result=None, meta={"from": "run_exit"}
        )

    register_run_exit(on_exit)

    with pytest.raises(ValueError, match="run failed"):
        with traced_run(name="failing"):
            raise ValueError("run failed")

    assert len(exit_exc_type) == 1 and exit_exc_type[0] is not None
    assert exit_exc_type[0] is ValueError

    config = load_config()
    run_id = get_latest_run_id(config)
    events = spans_to_events(load_spans(run_id, config))
    run_end_indices = [
        i
        for i, e in enumerate(events)
        if e.get("event_type") == EventType.RUN_END.value
    ]
    flush_indices = [
        i
        for i, e in enumerate(events)
        if e.get("event_type") == EventType.TOOL_CALL.value
        and e.get("payload", {}).get("tool_name") == "run_exit_flush"
    ]
    assert len(run_end_indices) == 1
    assert len(flush_indices) == 1
    assert flush_indices[0] < run_end_indices[0], (
        "run_exit-recorded event must appear before RUN_END"
    )
