"""
Unit tests for CrewAI integration: pending logic, run-exit flush, and gating.

Tests avoid requiring CrewAI at runtime by mocking crewai.hooks for import
and using fake context objects shaped like CrewAI LLMCallHookContext / ToolCallHookContext.
No crewai package is imported in this module.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from maida._integration_utils import _clear_test_run_lifecycle_registry
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

    The module keeps per-run dicts at module scope, and patch.dict's restore
    can hand back a shared module instance between tests. Combined with random
    test ordering under xdist, residue (e.g. empty ``{run_id: {}}`` left by
    after-hooks) leaks across tests. Reset explicitly so each test starts clean.
    """
    crewai_mod._pending_llm.clear()
    crewai_mod._pending_tool.clear()
    crewai_mod._llm_stack.clear()
    crewai_mod._llm_next_seq.clear()
    crewai_mod._tool_next_seq.clear()


@pytest.fixture
def crewai_module_with_mocked_hooks():
    """Load maida.integrations.crewai with crewai.hooks mocked so no real CrewAI is required."""
    # Provide a minimal hooks module that has the four register functions (no-ops)
    hooks = MagicMock()
    with patch.dict("sys.modules", {"crewai": MagicMock(), "crewai.hooks": hooks}):
        # Force reimport so our patch is used
        for mod in list(sys.modules.keys()):
            if mod == "maida.integrations.crewai":
                del sys.modules[mod]
                break
        try:
            import maida.integrations.crewai as crewai_mod

            _reset_crewai_module_state(crewai_mod)
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
