"""
Unit tests for CrewAI integration: pending logic, run-exit flush, and gating.

Tests avoid requiring CrewAI at runtime by mocking crewai.hooks for import
and using fake context objects shaped like CrewAI LLMCallHookContext / ToolCallHookContext.
No crewai package is imported in this module.
"""

import importlib
import secrets
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from maida._integration_utils import _clear_test_run_lifecycle_registry
from maida.constants import REDACTED_MARKER, TRUNCATED_MARKER
from maida.integrations._error import MissingOptionalDependencyError


# --- Minimal fake context classes (CrewAI-shaped, no crewai import) ---


def make_fake_llm_context(
    *,
    executor=None,
    messages=None,
    agent_role="Researcher",
    task_description="Do research",
    crew=None,
    llm=None,
    iterations=0,
    response=None,
):
    """Fake LLMCallHookContext: executor, messages (mutable list), agent.role, task.description, crew, llm, iterations, response."""
    executor = executor if executor is not None else SimpleNamespace()
    messages = list(messages) if messages is not None else []
    crew = crew if crew is not None else SimpleNamespace()
    llm = llm if llm is not None else SimpleNamespace(model_name="gpt-4")
    return SimpleNamespace(
        executor=executor,
        messages=messages,
        agent=SimpleNamespace(role=agent_role),
        task=SimpleNamespace(description=task_description),
        crew=crew,
        llm=llm,
        iterations=iterations,
        response=response,
    )


def make_fake_tool_context(
    *,
    tool_name="search",
    tool_input=None,
    tool=None,
    agent_role="Researcher",
    task_description="Do research",
    crew=None,
    tool_result=None,
):
    """Fake ToolCallHookContext: tool_name, tool_input (mutable dict), tool (optional), agent, task, crew, tool_result."""
    tool_input = dict(tool_input) if tool_input is not None else {}
    crew = crew if crew is not None else SimpleNamespace()
    return SimpleNamespace(
        tool_name=tool_name,
        tool_input=tool_input,
        tool=tool,
        agent=SimpleNamespace(role=agent_role),
        task=SimpleNamespace(description=task_description),
        crew=crew,
        tool_result=tool_result,
    )


@pytest.fixture(autouse=True)
def clear_lifecycle_registry():
    """Clear run lifecycle callbacks so crewai's enter/exit don't persist across tests."""
    _clear_test_run_lifecycle_registry()
    yield
    _clear_test_run_lifecycle_registry()


def _make_fake_crewai_hooks_import_error():
    """Make 'from crewai.hooks import ...' raise ImportError so we test optional-deps message."""

    class HooksFake:
        def __getattr__(self, name):
            raise ImportError("No module named 'crewai.hooks'")

    crewai_fake = type(sys)("crewai")
    crewai_fake.hooks = HooksFake()
    return crewai_fake


CREWAI_MISSING_MSG = "CrewAI integration requires optional deps. Install with `pip install maida-ai[crewai]`."


def test_import_crewai_without_extra_raises_clear_error():
    """If CrewAI is not installed, importing maida.integrations.crewai raises that friendly error string."""
    # Pop every crewai-related module (including already-cached real
    # `crewai.hooks` submodules). When the crewai extra is installed, another
    # test may have imported the real `crewai.hooks` first; leaving it cached
    # would let `from crewai.hooks import ...` succeed and defeat the fake.
    to_restore_mods = []
    for mod in list(sys.modules.keys()):
        if (
            mod == "maida.integrations.crewai"
            or mod.startswith("maida.integrations.crewai.")
            or mod == "crewai"
            or mod.startswith("crewai.")
        ):
            to_restore_mods.append((mod, sys.modules.pop(mod)))
    fake = _make_fake_crewai_hooks_import_error()
    try:
        sys.modules["crewai"] = fake
        with pytest.raises(MissingOptionalDependencyError) as exc_info:
            __import__("maida.integrations.crewai")
        assert str(exc_info.value) == CREWAI_MISSING_MSG
    finally:
        sys.modules.pop("crewai", None)
        sys.modules.pop("maida.integrations.crewai", None)
        for mod, val in to_restore_mods:
            sys.modules[mod] = val
        if "maida.integrations.crewai" not in sys.modules:
            try:
                __import__("maida.integrations.crewai")
            except MissingOptionalDependencyError:
                pass


def _reset_crewai_module_state(crewai_mod) -> None:
    """Clear crewai's process-global pending/sequence state.

    The module keeps per-run dicts at module scope, and import-isolation tests
    replace the module instance. Combined with random test ordering under xdist,
    residue (e.g. empty ``{run_id: {}}`` left by after-hooks) can otherwise leak
    across tests. Reset explicitly so each test starts clean.
    """
    crewai_mod._pending_llm.clear()
    crewai_mod._pending_tool.clear()
    crewai_mod._llm_stack.clear()
    crewai_mod._llm_next_seq.clear()
    crewai_mod._tool_next_seq.clear()
    crewai_mod._abort_exceptions.clear()


@pytest.fixture
def crewai_module_with_mocked_hooks(monkeypatch):
    """Load maida.integrations.crewai with crewai.hooks mocked so no real CrewAI is required."""
    # Patch only the modules this fixture owns. patch.dict(sys.modules) restores a
    # snapshot of the entire import cache on exit, which can resurrect a stale
    # maida.integrations.crewai module under randomized/xdist test scheduling.
    hooks = MagicMock()
    monkeypatch.setitem(sys.modules, "crewai", MagicMock())
    monkeypatch.setitem(sys.modules, "crewai.hooks", hooks)
    integrations_package = importlib.import_module("maida.integrations")
    monkeypatch.delattr(integrations_package, "crewai", raising=False)
    monkeypatch.delitem(sys.modules, "maida.integrations.crewai", raising=False)

    crewai_mod = importlib.import_module("maida.integrations.crewai")

    _reset_crewai_module_state(crewai_mod)
    try:
        yield crewai_mod
    finally:
        _reset_crewai_module_state(crewai_mod)


def test_gating_no_active_run_handlers_no_op_and_do_not_record(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    """When there is no active Maida run id, hook handlers no-op and do not record events."""
    crewai = crewai_module_with_mocked_hooks
    llm_ctx = make_fake_llm_context(messages=[{"role": "user", "content": "hi"}])
    tool_ctx = make_fake_tool_context(tool_name="search", tool_input={"q": "x"})
    with patch.object(crewai, "_get_active_run_id", return_value=None):
        with patch.object(crewai, "record_llm_call", MagicMock()) as record_llm:
            with patch.object(crewai, "record_tool_call", MagicMock()) as record_tool:
                crewai._before_llm_call(llm_ctx)
                crewai._after_llm_call(llm_ctx)
                crewai._before_tool_call(tool_ctx)
                crewai._after_tool_call(tool_ctx)
                record_llm.assert_not_called()
                record_tool.assert_not_called()
    assert not crewai._pending_llm
    assert not crewai._pending_tool


def test_before_llm_then_after_llm_emits_one_llm_call_with_duration_and_ok(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    """before_llm then after_llm emits one LLM_CALL with duration_ms and status='ok'."""
    crewai = crewai_module_with_mocked_hooks
    run_id = "test-run-llm"
    llm_ctx = make_fake_llm_context(
        messages=[{"role": "user", "content": "hi"}],
        response="Hello!",
    )
    with patch.object(crewai, "_get_active_run_id", return_value=run_id):
        crewai._before_llm_call(llm_ctx)
        with patch.object(crewai, "record_llm_call", MagicMock()) as record:
            crewai._after_llm_call(llm_ctx)
            record.assert_called_once()
            kw = record.call_args.kwargs
            assert kw["status"] == "ok"
            assert kw["response"] == "Hello!"
            assert kw["model"] == "gpt-4"
            meta = kw.get("meta") or {}
            assert meta.get("crewai", {}).get("duration_ms") is not None
            assert meta["crewai"]["duration_ms"] >= 0
    assert not crewai._pending_llm.get(run_id) or len(crewai._pending_llm[run_id]) == 0


def test_before_tool_then_after_tool_emits_one_tool_call_with_duration_and_ok(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    """before_tool then after_tool emits one TOOL_CALL with duration_ms and status='ok'."""
    crewai = crewai_module_with_mocked_hooks
    run_id = "test-run-tool"
    tool_ctx = make_fake_tool_context(
        tool_name="search", tool_input={"q": "x"}, tool_result={"hits": 2}
    )
    with patch.object(crewai, "_get_active_run_id", return_value=run_id):
        crewai._before_tool_call(tool_ctx)
        with patch.object(crewai, "record_tool_call", MagicMock()) as record:
            crewai._after_tool_call(tool_ctx)
            record.assert_called_once()
            kw = record.call_args.kwargs
            assert kw["status"] == "ok"
            assert kw["name"] == "search"
            assert kw["args"] == {"q": "x"}
            assert kw["result"] == {"hits": 2}
            meta = kw.get("meta") or {}
            assert meta.get("crewai", {}).get("duration_ms") is not None
            assert meta["crewai"]["duration_ms"] >= 0
    assert (
        not crewai._pending_tool.get(run_id) or len(crewai._pending_tool[run_id]) == 0
    )


def test_missing_after_tool_run_exit_emits_tool_call_error_missing_after_hook(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    """before_tool occurs; run exits with exception; one TOOL_CALL emitted with status='error' and meta.crewai.completion='missing_after_hook'."""
    crewai = crewai_module_with_mocked_hooks
    run_id = "missing-after-tool-run"
    tool_ctx = make_fake_tool_context(
        tool_name="fetch", tool_input={"url": "https://x.com"}
    )
    with patch.object(crewai, "_get_active_run_id", return_value=run_id):
        crewai._before_tool_call(tool_ctx)
    try:
        raise ValueError("run failed")
    except ValueError:
        exc_type, exc_value, tb = sys.exc_info()
    with patch.object(crewai, "record_tool_call", MagicMock()) as record:
        crewai._flush_pending_for_run(run_id, exc_type, exc_value, tb)
    record.assert_called_once()
    kw = record.call_args.kwargs
    assert kw["status"] == "error"
    assert (kw.get("meta") or {}).get("crewai", {}).get(
        "completion"
    ) == "missing_after_hook"
    assert kw.get("error") is not None
    assert kw["error"].get("error_type") == "ValueError"
    assert run_id not in crewai._pending_tool


def test_missing_after_llm_run_exit_emits_llm_call_error_missing_after_hook(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    """before_llm occurs; run exits with exception; one LLM_CALL emitted with status='error' and meta.crewai.completion='missing_after_hook'."""
    crewai = crewai_module_with_mocked_hooks
    run_id = "missing-after-llm-run"
    llm_ctx = make_fake_llm_context(messages=[{"role": "user", "content": "go"}])
    with patch.object(crewai, "_get_active_run_id", return_value=run_id):
        crewai._before_llm_call(llm_ctx)
    try:
        raise RuntimeError("run crashed")
    except RuntimeError:
        exc_type, exc_value, tb = sys.exc_info()
    with patch.object(crewai, "record_llm_call", MagicMock()) as record:
        crewai._flush_pending_for_run(run_id, exc_type, exc_value, tb)
    record.assert_called_once()
    kw = record.call_args.kwargs
    assert kw["status"] == "error"
    assert (kw.get("meta") or {}).get("crewai", {}).get(
        "completion"
    ) == "missing_after_hook"
    assert kw.get("error") is not None
    assert kw["error"].get("error_type") == "RuntimeError"
    assert run_id not in crewai._pending_llm


def test_flush_pending_on_run_exit_emits_error_events(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    """On run exit (no exception), pending LLM/tool entries get events with status=error and meta.crewai.completion=missing_after_hook."""
    crewai = crewai_module_with_mocked_hooks
    run_id = "flush-run"
    crewai._pending_llm[run_id] = {
        (0, 0, 0): {
            "start_ts": 0.0,
            "messages": [{"role": "user", "content": "x"}],
            "model": "gpt-4",
            "meta": {"framework": "crewai"},
        }
    }
    crewai._pending_tool[run_id] = {
        ("my_tool", 0): {
            "start_ts": 0.0,
            "tool_input": {"q": 1},
            "meta": {"framework": "crewai"},
        }
    }
    with patch.object(crewai, "record_llm_call", MagicMock()) as record_llm:
        with patch.object(crewai, "record_tool_call", MagicMock()) as record_tool:
            crewai._flush_pending_for_run(run_id, None, None, None)
    record_llm.assert_called_once()
    record_tool.assert_called_once()
    llm_kw = record_llm.call_args.kwargs
    tool_kw = record_tool.call_args.kwargs
    assert llm_kw.get("status") == "error"
    assert tool_kw.get("status") == "error"
    assert (llm_kw.get("meta") or {}).get("crewai", {}).get(
        "completion"
    ) == "missing_after_hook"
    assert (tool_kw.get("meta") or {}).get("crewai", {}).get(
        "completion"
    ) == "missing_after_hook"
    assert run_id not in crewai._pending_llm
    assert run_id not in crewai._pending_tool


def test_flush_pending_with_exception_attaches_error_payload(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    """When run exits with exception, flushed pending events get exception in error payload (error_type, message, stack)."""
    crewai = crewai_module_with_mocked_hooks
    run_id = "exc-run"
    crewai._pending_llm[run_id] = {
        (0, 0, 0): {
            "start_ts": 0.0,
            "messages": [],
            "model": "unknown",
            "meta": {},
        }
    }
    try:
        raise ValueError("run failed")
    except ValueError:
        import sys

        exc_type, exc_value, tb = sys.exc_info()
    with patch.object(crewai, "record_llm_call", MagicMock()) as record_llm:
        crewai._flush_pending_for_run(run_id, exc_type, exc_value, tb)
    record_llm.assert_called_once()
    call_kw = record_llm.call_args.kwargs
    assert call_kw.get("status") == "error"
    assert call_kw.get("error") is not None
    assert call_kw["error"].get("error_type") == "ValueError"
    assert "run failed" in str(call_kw["error"].get("message", ""))
    assert (
        call_kw["error"].get("stack") is not None
        and "ValueError" in call_kw["error"]["stack"]
    )


def _load_latest_events():
    from maida.config import load_config
    from maida.storage import list_runs, load_run_for_analysis

    config = load_config()
    run = list_runs(limit=1, config=config)[0]
    run_id = run.get("trace_id") or run["run_id"]
    return load_run_for_analysis(run_id, config)


def test_crewai_success_persists_exact_signature_and_namespaced_metadata(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    from maida import trace

    crewai = crewai_module_with_mocked_hooks
    llm_ctx = make_fake_llm_context(
        messages=[{"role": "user", "content": "Find the docs"}],
        response="Use search_docs",
    )
    tool_ctx = make_fake_tool_context(
        tool_name="search_docs",
        tool_input={"query": "CrewAI"},
        tool_result={"hits": 1},
    )

    @trace(name="CrewAI conformance success")
    def run():
        crewai._before_llm_call(llm_ctx)
        crewai._after_llm_call(llm_ctx)
        crewai._before_tool_call(tool_ctx)
        crewai._after_tool_call(tool_ctx)

    run()

    _, meta, events = _load_latest_events()
    assert [event["event_type"] for event in events] == [
        "RUN_START",
        "LLM_CALL",
        "TOOL_CALL",
        "RUN_END",
    ]
    assert meta["counts"] == {
        "llm_calls": 1,
        "tool_calls": 1,
        "errors": 0,
        "loop_warnings": 0,
    }
    for event in events[1:3]:
        assert event["payload"]["status"] == "ok"
        assert event["meta"]["framework"] == "crewai"
        assert event["meta"]["crewai"]["agent_role"] == "Researcher"
        assert event["meta"]["crewai"]["task_desc"] == "Do research"
        assert "agent_role" not in event["meta"]
        assert "task_desc" not in event["meta"]
    assert events[-1]["payload"] == {"status": "ok"}


def test_crewai_missing_completion_hooks_persist_failed_calls_before_run_end(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    from maida import trace

    crewai = crewai_module_with_mocked_hooks

    @trace(name="CrewAI incomplete calls")
    def run():
        crewai._before_llm_call(
            make_fake_llm_context(messages=[{"role": "user", "content": "fail"}])
        )
        crewai._before_tool_call(
            make_fake_tool_context(
                tool_name="search_docs", tool_input={"query": "fail"}
            )
        )
        raise RuntimeError("simulated CrewAI failure")

    with pytest.raises(RuntimeError, match="simulated CrewAI failure"):
        run()

    _, meta, events = _load_latest_events()
    assert [event["event_type"] for event in events] == [
        "RUN_START",
        "ERROR",
        "LLM_CALL",
        "TOOL_CALL",
        "RUN_END",
    ]
    for event in events[2:4]:
        assert event["payload"]["status"] == "error"
        assert event["payload"]["error"]["error_type"] == "RuntimeError"
        assert event["meta"]["crewai"]["completion"] == "missing_after_hook"
    assert meta["status"] == "error"
    assert events[-1]["payload"] == {"status": "error"}


def test_crewai_payloads_are_sanitized_before_persistence(
    crewai_module_with_mocked_hooks, temp_data_dir, monkeypatch
):
    """Adapter payloads use Maida's redaction/truncation storage boundary."""
    from maida import trace
    from maida.config import load_config

    crewai = crewai_module_with_mocked_hooks
    secret = secrets.token_hex(24)
    oversized = "public-" + ("x" * 200)
    monkeypatch.setenv("MAIDA_REDACT_KEYS", "api_key,message,stack")
    monkeypatch.setenv("MAIDA_MAX_FIELD_BYTES", "80")
    llm_ctx = make_fake_llm_context(
        messages=[{"role": "user", "api_key": secret, "content": oversized}],
        response={"api_key": secret, "content": oversized},
    )
    tool_ctx = make_fake_tool_context(
        tool_name="private_tool",
        tool_input={"api_key": secret, "query": oversized},
    )

    @trace(name="crewai privacy")
    def _run():
        crewai._before_llm_call(llm_ctx)
        crewai._after_llm_call(llm_ctx)
        crewai._before_tool_call(tool_ctx)
        raise RuntimeError(secret)

    with pytest.raises(RuntimeError, match=secret):
        _run()

    config = load_config()
    run_id, _, events = _load_latest_events()
    raw = (config.data_dir / "runs" / run_id / "spans.jsonl").read_text(
        encoding="utf-8"
    )
    assert secret not in raw

    llm = next(event for event in events if event["event_type"] == "LLM_CALL")
    tool = next(event for event in events if event["event_type"] == "TOOL_CALL")
    assert REDACTED_MARKER in llm["payload"]["prompt"]
    assert REDACTED_MARKER in llm["payload"]["response"]
    assert TRUNCATED_MARKER in llm["payload"]["prompt"]
    assert TRUNCATED_MARKER in llm["payload"]["response"]
    assert tool["payload"]["args"]["api_key"] == REDACTED_MARKER
    assert tool["payload"]["args"]["query"].endswith(TRUNCATED_MARKER)
    assert tool["payload"]["error"]["message"] == REDACTED_MARKER


def test_crewai_hooks_outside_a_run_do_not_contaminate_a_later_run(
    crewai_module_with_mocked_hooks, temp_data_dir, monkeypatch
):
    from maida import trace
    from maida.config import load_config
    from maida.storage import list_runs

    monkeypatch.delenv("MAIDA_IMPLICIT_RUN", raising=False)
    crewai = crewai_module_with_mocked_hooks
    outside = make_fake_tool_context(tool_name="outside_tool", tool_result="ignored")
    crewai._before_tool_call(outside)
    crewai._after_tool_call(outside)
    assert list_runs(limit=10, config=load_config()) == []

    inside = make_fake_tool_context(tool_name="inside_tool", tool_result="ok")

    @trace(name="CrewAI isolated run")
    def run():
        crewai._before_tool_call(inside)
        crewai._after_tool_call(inside)

    run()

    _, meta, events = _load_latest_events()
    assert [event["event_type"] for event in events] == [
        "RUN_START",
        "TOOL_CALL",
        "RUN_END",
    ]
    assert events[1]["payload"]["tool_name"] == "inside_tool"
    assert meta["counts"]["tool_calls"] == 1


def test_crewai_abort_signal_bypasses_exception_handlers_and_blocks_later_hooks(
    crewai_module_with_mocked_hooks, temp_data_dir
):
    from maida import trace
    from maida.exceptions import LoopAbort, _MaidaAbortSignal

    crewai = crewai_module_with_mocked_hooks
    completed = 0

    def framework_dispatch(hook, context):
        try:
            return hook(context)
        except Exception:
            return None

    @trace(
        name="CrewAI guarded loop",
        stop_on_loop=True,
        stop_on_loop_min_repetitions=3,
    )
    def looping_run():
        nonlocal completed
        for _ in range(10):
            context = make_fake_tool_context(
                tool_name="search_docs",
                tool_input={"query": "same query"},
                tool_result="same result",
            )
            framework_dispatch(crewai._before_tool_call, context)
            framework_dispatch(crewai._after_tool_call, context)
            completed += 1

    with pytest.raises(LoopAbort):
        looping_run()

    assert completed < 10
    _, meta, events = _load_latest_events()
    assert [event["event_type"] for event in events][-3:] == [
        "LOOP_WARNING",
        "ERROR",
        "RUN_END",
    ]
    assert meta["status"] == "error"
    assert events[-1]["payload"] == {"status": "error"}
    assert crewai._abort_exceptions == {}

    @trace(name="CrewAI clean reuse")
    def clean_run():
        context = make_fake_tool_context(tool_name="lookup", tool_result="ok")
        crewai._before_tool_call(context)
        crewai._after_tool_call(context)

    clean_run()
    _, clean_meta, clean_events = _load_latest_events()
    assert [event["event_type"] for event in clean_events] == [
        "RUN_START",
        "TOOL_CALL",
        "RUN_END",
    ]
    assert clean_meta["status"] == "ok"

    cause = LoopAbort(threshold=3, actual=3, message="already aborted")
    crewai._abort_exceptions["active-run"] = cause
    with patch.object(crewai, "_get_active_run_id", return_value="active-run"):
        with pytest.raises(_MaidaAbortSignal) as signal:
            framework_dispatch(crewai._before_llm_call, make_fake_llm_context())
        assert signal.value.cause is cause
        with pytest.raises(LoopAbort, match="already aborted"):
            crewai.raise_if_aborted()
