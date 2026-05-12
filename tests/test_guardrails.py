"""
Guardrails tests: stop_on_loop, max_llm_calls, max_tool_calls, max_events, max_duration_s.

Deterministic: no network, no randomness. Uses temp_data_dir; max_duration_s tests
use patched time.
"""

import pytest

from maida import record_llm_call, record_tool_call, record_state, trace, traced_run
from maida.config import load_config
from maida.events import EventType
from maida.exceptions import GuardrailExceeded, LoopAbort
from maida.storage import load_events, load_run_meta
from tests.conftest import get_latest_run_id


# ---------------------------------------------------------------------------
# stop_on_loop
# ---------------------------------------------------------------------------


@trace(stop_on_loop=True, stop_on_loop_min_repetitions=3)
def _run_loop_pattern():
    """Emit (TOOL_CALL:foo, LLM_CALL:gpt) x 3 so loop detection fires and guardrail aborts."""
    for _ in range(3):
        record_tool_call("foo", args={}, result=None)
        record_llm_call("gpt", prompt="p", response="r")


def test_stop_on_loop_enabled_and_threshold_crossed_aborts(temp_data_dir):
    """When stop_on_loop=True and loop detection fires with repetitions >= threshold, abort."""
    with pytest.raises(LoopAbort):
        _run_loop_pattern()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)
    run_meta = load_run_meta(run_id, config)

    errors = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    run_ends = [e for e in events if e.get("event_type") == EventType.RUN_END.value]
    assert len(errors) == 1
    assert len(run_ends) == 1
    assert run_meta.get("status") == "error"
    payload = errors[0].get("payload", {})
    assert payload.get("guardrail") == "stop_on_loop"
    assert payload.get("threshold") == 3
    assert payload.get("actual") == 3


@trace(stop_on_loop=False)
def _run_loop_pattern_no_stop():
    for _ in range(3):
        record_tool_call("x", args={}, result=None)
        record_llm_call("y", prompt="p", response="r")


def test_stop_on_loop_disabled_no_abort(temp_data_dir):
    """When stop_on_loop=False, loop warning is emitted but no abort."""
    _run_loop_pattern_no_stop()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)
    run_meta = load_run_meta(run_id, config)

    loop_warnings = [
        e for e in events if e.get("event_type") == EventType.LOOP_WARNING.value
    ]
    errors = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    assert len(loop_warnings) == 1
    assert len(errors) == 0
    assert run_meta.get("status") == "ok"


def test_stop_on_loop_below_threshold_no_abort(temp_data_dir):
    """When repetitions (2) < stop_on_loop_min_repetitions (3), no abort."""

    @trace(stop_on_loop=True, stop_on_loop_min_repetitions=3)
    def run_two():
        record_tool_call("a", args={}, result=None)
        record_llm_call("b", prompt="p", response="r")
        record_tool_call("a", args={}, result=None)
        record_llm_call("b", prompt="p", response="r")

    run_two()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)
    run_meta = load_run_meta(run_id, config)

    errors = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    assert len(errors) == 0
    assert run_meta.get("status") == "ok"


# ---------------------------------------------------------------------------
# max_llm_calls
# ---------------------------------------------------------------------------


def test_max_llm_calls_triggers_at_n_plus_one(temp_data_dir):
    """max_llm_calls=50 allows 50 calls; 51st triggers abort."""

    @trace(max_llm_calls=2)
    def run_three_llm():
        record_llm_call("m", prompt="p", response="r")
        record_llm_call("m", prompt="p", response="r")
        record_llm_call("m", prompt="p", response="r")

    with pytest.raises(GuardrailExceeded) as exc_info:
        run_three_llm()

    assert exc_info.value.guardrail == "max_llm_calls"
    assert exc_info.value.threshold == 2
    assert exc_info.value.actual == 3

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)
    run_meta = load_run_meta(run_id, config)
    errors = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    assert len(errors) == 1
    assert errors[0]["payload"]["guardrail"] == "max_llm_calls"
    assert run_meta.get("status") == "error"


def test_max_llm_calls_at_limit_does_not_trigger(temp_data_dir):
    """Exactly 2 LLM calls when max_llm_calls=2 completes ok."""

    @trace(max_llm_calls=2)
    def run_two_llm():
        record_llm_call("m", prompt="p", response="r")
        record_llm_call("m", prompt="p", response="r")

    run_two_llm()

    config = load_config()
    run_id = get_latest_run_id(config)
    run_meta = load_run_meta(run_id, config)
    assert run_meta.get("status") == "ok"
    assert run_meta.get("counts", {}).get("llm_calls") == 2


# ---------------------------------------------------------------------------
# max_tool_calls
# ---------------------------------------------------------------------------


def test_max_tool_calls_triggers_at_n_plus_one(temp_data_dir):
    """max_tool_calls=2 allows 2; 3rd triggers abort."""

    @trace(max_tool_calls=2)
    def run_three_tool():
        record_tool_call("t", args={}, result=None)
        record_tool_call("t", args={}, result=None)
        record_tool_call("t", args={}, result=None)

    with pytest.raises(GuardrailExceeded) as exc_info:
        run_three_tool()

    assert exc_info.value.guardrail == "max_tool_calls"
    assert exc_info.value.threshold == 2
    assert exc_info.value.actual == 3


# ---------------------------------------------------------------------------
# max_events
# ---------------------------------------------------------------------------


def test_max_events_triggers_at_threshold(temp_data_dir):
    """max_events=5 aborts when total events exceeds 5 (e.g. after 6th event)."""

    @trace(max_events=5)
    def run_many_events():
        record_llm_call("m", prompt="p", response="r")
        record_tool_call("t", args={}, result=None)
        record_state(state={})
        record_llm_call("m", prompt="p", response="r")
        record_tool_call("t", args={}, result=None)

    with pytest.raises(GuardrailExceeded) as exc_info:
        run_many_events()

    assert exc_info.value.guardrail == "max_events"
    assert exc_info.value.threshold == 5
    assert exc_info.value.actual > 5


# ---------------------------------------------------------------------------
# max_duration_s (deterministic via patched time)
# ---------------------------------------------------------------------------


def test_max_duration_s_triggers_after_timeout(temp_data_dir, monkeypatch):
    """max_duration_s triggers when elapsed time >= limit; use patched time for determinism."""
    from maida import guardrails as guardrails_mod
    from maida import storage as storage_mod

    start_ts = "2026-01-01T12:00:00.000Z"
    end_ts = "2026-01-01T12:01:40.000Z"  # 100s later

    monkeypatch.setattr(storage_mod, "utc_now_iso_ms_z", lambda: start_ts)
    monkeypatch.setattr(guardrails_mod, "utc_now_iso_ms_z", lambda: end_ts)

    with pytest.raises(GuardrailExceeded) as exc_info:
        with traced_run(max_duration_s=60):
            record_llm_call("m", prompt="p", response="r")

    assert exc_info.value.guardrail == "max_duration_s"
    assert exc_info.value.threshold == 60
    assert exc_info.value.actual >= 60


# ---------------------------------------------------------------------------
# Lifecycle: ERROR + RUN_END and re-raise
# ---------------------------------------------------------------------------


def test_guardrail_abort_records_error_and_run_end(temp_data_dir):
    """Guardrail abort produces exactly one ERROR and RUN_END(status=error)."""

    @trace(max_llm_calls=0)
    def run_one_llm():
        record_llm_call("m", prompt="p", response="r")

    with pytest.raises(GuardrailExceeded):
        run_one_llm()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)
    run_meta = load_run_meta(run_id, config)

    errors = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    run_ends = [e for e in events if e.get("event_type") == EventType.RUN_END.value]
    assert len(errors) == 1
    assert len(run_ends) == 1
    assert run_ends[0].get("payload", {}).get("status") == "error"
    assert run_meta.get("status") == "error"
    assert run_meta.get("counts", {}).get("errors") == 1


def test_guardrail_exception_re_raised(temp_data_dir):
    """Caller can catch AgentDbgGuardrailExceeded."""

    @trace(max_llm_calls=0)
    def run_one():
        record_llm_call("m", prompt="p", response="r")

    caught = None
    try:
        run_one()
    except GuardrailExceeded as e:
        caught = e

    assert caught is not None
    assert caught.guardrail == "max_llm_calls"


# ---------------------------------------------------------------------------
# Defaults unchanged
# ---------------------------------------------------------------------------


@trace
def _traced_no_guardrails():
    for _ in range(4):
        record_llm_call("m", prompt="p", response="r")
        record_tool_call("t", args={}, result=None)


def test_default_behavior_unchanged(temp_data_dir):
    """With no guardrail params (defaults), run completes normally."""
    _traced_no_guardrails()

    config = load_config()
    run_id = get_latest_run_id(config)
    run_meta = load_run_meta(run_id, config)
    events = load_events(run_id, config)

    assert run_meta.get("status") == "ok"
    assert run_meta.get("counts", {}).get("llm_calls") == 4
    assert run_meta.get("counts", {}).get("tool_calls") == 4
    errors = [e for e in events if e.get("event_type") == EventType.ERROR.value]
    assert len(errors) == 0


# ---------------------------------------------------------------------------
# Precedence: function args > env
# ---------------------------------------------------------------------------


def test_precedence_function_arg_over_env(temp_data_dir, monkeypatch):
    """@trace(max_llm_calls=2) overrides AGENTDBG_MAX_LLM_CALLS=10."""
    monkeypatch.setenv("AGENTDBG_MAX_LLM_CALLS", "10")

    @trace(max_llm_calls=2)
    def run_three():
        record_llm_call("m", prompt="p", response="r")
        record_llm_call("m", prompt="p", response="r")
        record_llm_call("m", prompt="p", response="r")

    with pytest.raises(GuardrailExceeded) as exc_info:
        run_three()

    assert exc_info.value.threshold == 2


# ---------------------------------------------------------------------------
# traced_run with guardrails
# ---------------------------------------------------------------------------


def test_traced_run_with_guardrails(temp_data_dir):
    """traced_run(max_llm_calls=N) enforces limit."""
    with pytest.raises(GuardrailExceeded):
        with traced_run(max_llm_calls=1):
            record_llm_call("a", prompt="p", response="r")
            record_llm_call("b", prompt="p", response="r")

    config = load_config()
    run_id = get_latest_run_id(config)
    run_meta = load_run_meta(run_id, config)
    assert run_meta.get("status") == "error"


# ---------------------------------------------------------------------------
# Edge cases: exception hierarchy and merge_guardrail_params
# ---------------------------------------------------------------------------


def test_loop_abort_is_subclass_of_guardrail_exceeded():
    """LoopAbort is a subclass of GuardrailExceeded."""
    exc = LoopAbort(threshold=3, actual=4, message="loop detected")
    assert isinstance(exc, GuardrailExceeded)
    assert exc.guardrail == "stop_on_loop"
    assert exc.threshold == 3
    assert exc.actual == 4


def test_loop_abort_caught_by_parent_class(temp_data_dir):
    """LoopAbort can be caught using the parent GuardrailExceeded type."""

    @trace(stop_on_loop=True, stop_on_loop_min_repetitions=3)
    def run_loop():
        for _ in range(3):
            record_tool_call("foo", args={}, result=None)
            record_llm_call("gpt", prompt="p", response="r")

    caught = None
    try:
        run_loop()
    except GuardrailExceeded as e:
        caught = e

    assert caught is not None
    assert isinstance(caught, LoopAbort)
    assert caught.guardrail == "stop_on_loop"


def test_merge_guardrail_params_min_repetitions_clamped_to_2():
    """stop_on_loop_min_repetitions values below 2 are clamped to 2."""
    from maida.guardrails import GuardrailParams, merge_guardrail_params

    base = GuardrailParams()
    out = merge_guardrail_params(base, stop_on_loop_min_repetitions=1)
    assert out.stop_on_loop_min_repetitions == 2

    out0 = merge_guardrail_params(base, stop_on_loop_min_repetitions=0)
    assert out0.stop_on_loop_min_repetitions == 2


def test_merge_guardrail_params_negative_max_llm_calls_ignored():
    """Negative max_llm_calls override is silently ignored; limit stays None."""
    from maida.guardrails import GuardrailParams, merge_guardrail_params

    base = GuardrailParams()
    out = merge_guardrail_params(base, max_llm_calls=-1)
    assert out.max_llm_calls is None


def test_merge_guardrail_params_negative_max_duration_s_clamped_to_zero():
    """Negative max_duration_s is clamped to 0.0, not ignored."""
    from maida.guardrails import GuardrailParams, merge_guardrail_params

    base = GuardrailParams()
    out = merge_guardrail_params(base, max_duration_s=-5.0)
    assert out.max_duration_s == 0.0


def test_max_duration_s_zero_triggers_immediately(temp_data_dir, monkeypatch):
    """max_duration_s=0.0 triggers on the first event because elapsed >= 0.0 is always true."""
    from maida import guardrails as guardrails_mod
    from maida import storage as storage_mod

    ts = "2026-01-01T12:00:00.000Z"
    monkeypatch.setattr(storage_mod, "utc_now_iso_ms_z", lambda: ts)
    monkeypatch.setattr(guardrails_mod, "utc_now_iso_ms_z", lambda: ts)

    with pytest.raises(GuardrailExceeded) as exc_info:
        with traced_run(max_duration_s=0.0):
            record_llm_call("m", prompt="p", response="r")

    assert exc_info.value.guardrail == "max_duration_s"
    assert exc_info.value.threshold == 0.0


# ---------------------------------------------------------------------------
# LOOP_WARNING deduplication (regression: duplicate warnings when stop_on_loop
# raises an exception that is caught by the calling framework)
# ---------------------------------------------------------------------------


def test_loop_warning_dedup_no_guardrail_emits_one_per_pattern(temp_data_dir):
    """With stop_on_loop=False, a long repeated sequence emits exactly one
    LOOP_WARNING per distinct pattern, not one per detection opportunity."""

    @trace(name="dedup-no-guardrail")
    def run_long_loop():
        for _ in range(6):
            record_llm_call("m", prompt="p", response="r")
            record_tool_call("t", args={}, result=None)

    run_long_loop()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)
    run_meta = load_run_meta(run_id, config)

    loop_warnings = [
        e for e in events if e.get("event_type") == EventType.LOOP_WARNING.value
    ]
    patterns = {e["payload"]["pattern"] for e in loop_warnings}
    assert len(patterns) <= 2, f"at most 2 distinct patterns, got {patterns}"
    assert len(loop_warnings) == len(patterns), (
        f"expected one LOOP_WARNING per distinct pattern, got {len(loop_warnings)} "
        f"warnings for {len(patterns)} patterns"
    )
    assert run_meta["counts"]["loop_warnings"] == len(loop_warnings)
    assert run_meta["status"] == "ok"


def test_loop_warning_dedup_with_stop_on_loop_emits_minimal_warnings(temp_data_dir):
    """With stop_on_loop=True, the first qualifying LOOP_WARNING triggers
    LoopAbort.  Even if the caller catches the exception and keeps
    emitting events (simulating the OpenAI Agents SDK behaviour), subsequent
    detections of already-emitted patterns must NOT produce extra LOOP_WARNINGs."""

    @trace(
        name="dedup-with-guardrail",
        stop_on_loop=True,
        stop_on_loop_min_repetitions=3,
    )
    def run_loop_swallowing_abort():
        for _ in range(6):
            try:
                record_llm_call("m", prompt="p", response="r")
            except LoopAbort:
                pass
            try:
                record_tool_call("t", args={}, result=None)
            except LoopAbort:
                pass

    run_loop_swallowing_abort()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)
    run_meta = load_run_meta(run_id, config)

    loop_warnings = [
        e for e in events if e.get("event_type") == EventType.LOOP_WARNING.value
    ]
    patterns = {e["payload"]["pattern"] for e in loop_warnings}
    assert len(loop_warnings) == len(patterns), (
        f"each pattern should produce exactly one LOOP_WARNING, "
        f"got {len(loop_warnings)} warnings for {len(patterns)} patterns"
    )
    assert len(loop_warnings) <= 2
    assert run_meta["counts"]["loop_warnings"] == len(loop_warnings)


def test_stop_on_loop_re_raises_after_swallowed_abort(temp_data_dir):
    """When stop_on_loop=True and the framework swallows LoopAbort,
    subsequent detections of the same pattern must keep raising the abort
    (without emitting duplicate LOOP_WARNING events)."""

    abort_count = 0

    @trace(
        name="re-raise-after-swallow",
        stop_on_loop=True,
        stop_on_loop_min_repetitions=3,
    )
    def run_loop_counting_aborts():
        nonlocal abort_count
        for _ in range(6):
            try:
                record_llm_call("m", prompt="p", response="r")
            except LoopAbort:
                abort_count += 1
            try:
                record_tool_call("t", args={}, result=None)
            except LoopAbort:
                abort_count += 1

    run_loop_counting_aborts()

    assert abort_count > 1, (
        f"expected multiple LoopAbort raises when framework keeps "
        f"swallowing, got {abort_count}"
    )

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)

    loop_warnings = [
        e for e in events if e.get("event_type") == EventType.LOOP_WARNING.value
    ]
    patterns = {e["payload"]["pattern"] for e in loop_warnings}
    assert len(loop_warnings) == len(patterns), (
        "dedup must still prevent duplicate LOOP_WARNING events"
    )


# ---------------------------------------------------------------------------
# Nested traced_run guardrail params
# ---------------------------------------------------------------------------


def test_nested_traced_run_applies_guardrail_params(temp_data_dir):
    """traced_run(stop_on_loop=True) inside @trace (which defaults to
    stop_on_loop=False) must apply the inner guardrail params."""

    @trace(name="outer-no-stop")
    def run_outer():
        with traced_run(stop_on_loop=True, stop_on_loop_min_repetitions=3):
            for _ in range(3):
                record_tool_call("foo", args={}, result=None)
                record_llm_call("gpt", prompt="p", response="r")

    with pytest.raises(LoopAbort):
        run_outer()

    config = load_config()
    run_id = get_latest_run_id(config)
    events = load_events(run_id, config)

    loop_warnings = [
        e for e in events if e.get("event_type") == EventType.LOOP_WARNING.value
    ]
    assert len(loop_warnings) >= 1, (
        "nested traced_run with stop_on_loop=True should emit LOOP_WARNING"
    )
