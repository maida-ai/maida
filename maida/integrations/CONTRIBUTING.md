# Writing Maida Integration Adapters

This guide captures the patterns and pitfalls discovered while building the LangChain, OpenAI Agents SDK, and CrewAI integrations. Follow these guidelines to avoid repeating the same issues.

---

## Adapter conformance contract

This section is normative. Every new adapter, and every existing adapter whose
event mapping changes, MUST satisfy this contract. An adapter translates a
framework's signals into Maida's public recording API; it does not define a
second trace schema.

### Required normalized signals

Conformance is judged from the persisted Maida trace, not only from mocked
`record_*` calls.

| Framework signal | Required Maida representation |
|---|---|
| Run start | An active `@trace` or `traced_run(...)` boundary produces one `RUN_START`. Adapters MUST attach to that run and MUST NOT create a second root run. |
| Run end | The same boundary produces exactly one terminal `RUN_END`, after adapter exit hooks have flushed pending work. Successful runs end with `status="ok"`; an unhandled exception or guardrail abort ends with `status="error"`. |
| LLM call | Each completed generation exposed by the framework produces one `LLM_CALL` through `record_llm_call`, including model, available input/output and usage, and `status`. Failed generations use `status="error"` and the normalized `error` argument. |
| Tool call | Each completed tool or equivalent operation exposed by the framework produces one `TOOL_CALL` through `record_tool_call`, including the stable tool name, available arguments/result, and `status`. Failed operations use `status="error"` and the normalized `error` argument. |
| Error | An LLM/tool failure stays on its normalized call with `status="error"`. An exception escaping the run is also represented by the core lifecycle as `ERROR`; adapters MUST NOT invent a duplicate framework-specific error event. |
| Guardrail | If adapter callbacks can trigger Maida guardrails, the adapter MUST preserve the core `LOOP_WARNING` or limit signal, stop subsequent framework operations, and let the run reach terminal `RUN_END` with `status="error"`. Use the abort pattern below when the framework swallows `Exception`. |
| State or other metadata | When a framework exposes a genuine state transition, use `record_state`. Context that does not map to a normalized signal belongs under namespaced `meta`, not in a new event type. |

Frameworks do not all expose every signal. "Where applicable" means the
adapter MUST map a signal when the framework supplies a reliable callback,
hook, or span for it. The adapter documentation MUST call out signals the
framework cannot expose. It MUST NOT synthesize successful calls that were not
observed.

Some frameworks emit a start hook without a matching completion hook when the
operation or run fails. An adapter that keeps pending operations MUST flush
them before run teardown as an `LLM_CALL` or `TOOL_CALL` with
`status="error"`. The trace must still contain exactly one terminal `RUN_END`.

### Deterministic offline conformance tests

Adapter tests MUST be deterministic and offline. Use fake framework modules,
in-memory callbacks, fixed payloads, and stub tools. Tests MUST NOT make real
provider calls, require API keys, or depend on network access, wall-clock
timing, random IDs, or framework services.

At minimum, the adapter test suite MUST prove:

1. Importing the core `maida` package works without the optional framework,
   while importing the adapter gives clear installation instructions when its
   extra is missing.
2. With an active run, the success path persists one `RUN_START`, the expected
   `LLM_CALL` and `TOOL_CALL` signals the framework exposes, and one terminal
   `RUN_END(status="ok")` in lifecycle order.
3. Failed LLM/tool operations persist `status="error"` with normalized error
   details, and an escaping run failure persists `ERROR` followed by one
   terminal `RUN_END(status="error")`.
4. Missing completion hooks are flushed as failed operations before
   `RUN_END`, if the framework has split start/end hooks.
5. Calls outside an active run follow the adapter's documented behavior and do
   not contaminate another run.
6. Guardrail propagation, aborting subsequent operations, and terminal error
   state work when callbacks can invoke guardrails.
7. Payload safety and metadata namespacing satisfy the two sections below.

Assertions SHOULD inspect the persisted `meta.json` and `spans.jsonl` through
Maida's storage/event projection APIs. Mock-call assertions are useful for
mapping details, but are not sufficient evidence of conformance by themselves.

### Redaction and truncation

Every framework-derived prompt, response, tool argument/result, error detail,
state value, and metadata value MUST flow through `record_llm_call`,
`record_tool_call`, or `record_state`. Those APIs apply Maida's configured
redaction and truncation before persistence. Adapters MUST NOT write raw
framework payloads directly to spans or files, and MUST NOT retain an
unredacted duplicate under `meta`.

The conformance suite MUST persist a nested secret under a configured sensitive
key and an oversized string in both a normalized payload and framework
metadata. It must inspect the raw run artifacts to prove that the secret is
absent, redacted values use `__REDACTED__`, and bounded values use
`__TRUNCATED__`. This test must exercise the adapter path rather than only the
redaction helper.

### Framework-specific metadata

Normalized signal data belongs in the existing arguments to Maida's public
recorders. Framework-specific extras MUST be stored below
`meta.<adapter_name>` using a stable, lowercase adapter name, for example:

```python
meta = {
    "framework": "openai_agents",
    "openai_agents": {
        "span_type": "generation",
        "trace_metadata": trace_metadata,
    },
}
```

An adapter MAY include the common `meta.framework` discriminator, but its
framework-only fields still belong in the namespaced object. Adapters MUST NOT
add framework-specific event types, top-level trace fields, or core schema
fields. If a framework concept cannot be represented as an existing normalized
signal plus namespaced metadata, document the gap and discuss a
framework-agnostic core change separately; do not change the schema for one
framework.

---

## The core problem

Every agent framework has its own error handling around callbacks/hooks/processors. When maida detects a loop or a guardrail violation, it raises an exception. The framework almost always catches it:

| Framework | Hook mechanism | Error handling |
|---|---|---|
| LangChain / LangGraph | `BaseCallbackHandler` methods | `except Exception` in callback manager; graph executor also catches `Exception` from nodes |
| OpenAI Agents SDK | `TracingProcessor.on_span_end` | `except Exception` in `SynchronousMultiTracingProcessor` |
| CrewAI | Execution hooks | `except Exception` in hook runner |

Because `GuardrailExceeded` inherits from `Exception`, it gets caught and swallowed at **two levels**: once by the callback dispatcher and once by the framework's execution loop.

---

## The `_MaidaAbortSignal` pattern

The solution is `_MaidaAbortSignal`, an internal `BaseException` subclass defined in `maida.exceptions`. `BaseException` subclasses bypass `except Exception` blocks — the same mechanism Python uses for `KeyboardInterrupt`, `SystemExit`, and `asyncio.CancelledError`.

### How it works

```
Framework calls adapter hook
  → adapter calls record_llm_call / record_tool_call
    → guardrail fires → raises GuardrailExceeded (Exception)
  → adapter catches it, stores on _abort_exception
  → adapter raises _MaidaAbortSignal(cause) (BaseException)
→ framework's `except Exception` does NOT catch it
→ signal propagates through the framework's execution loop
→ signal reaches traced_run / @trace context manager
→ _run_context catches _MaidaAbortSignal
→ records ERROR + RUN_END
→ re-raises the wrapped GuardrailExceeded (the public exception type)
→ user sees LoopAbort or GuardrailExceeded
```

### Rules for using `_MaidaAbortSignal`

1. **Always wrap the original exception.** `_MaidaAbortSignal(cause)` stores the original `GuardrailExceeded` on `.cause`. The lifecycle layer unwraps it so the user sees the public exception type.

2. **Always store the original on `_abort_exception` before raising the signal.** This is a defensive fallback — if the signal is caught by the framework despite being a `BaseException`, the user can still call `raise_if_aborted()`.

3. **Use `raise _MaidaAbortSignal(e) from e`** to preserve the exception chain.

4. **Never raise `_MaidaAbortSignal` from the core SDK** (`record_llm_call`, `record_tool_call`, etc.). The core raises `GuardrailExceeded` (an `Exception`). Only integration adapters escalate to `_MaidaAbortSignal` because they know the framework will catch `Exception`.

---

## Guard subsequent callbacks after an abort

Once a guardrail fires, the framework may continue calling your adapter for subsequent operations. You must block these immediately:

```python
def _check_aborted(self) -> None:
    if self._abort_exception is not None:
        raise _MaidaAbortSignal(self._abort_exception)
```

Call `_check_aborted()` at the **start** of every hook method that initiates a new operation (`on_llm_start`, `on_tool_start`, `on_chat_model_start`, `on_span_start`, etc.). This prevents:
- New LLM API calls from being made (wasting tokens)
- New tool invocations from running
- Misleading events from being recorded after the abort

---

## Do not auto-reset abort state

A common mistake: resetting `_abort_exception = None` at the start of each top-level callback. This defeats the abort guard because the framework keeps calling hooks.

**Wrong:**
```python
def on_llm_start(self, ...):
    if parent_run_id is None:
        self._abort_exception = None  # BAD: clears the abort
        self.raise_error = False
```

**Right:** Provide an explicit `reset()` method for handler reuse across separate runs:
```python
def reset(self) -> None:
    self._abort_exception = None
```

The user calls `reset()` or creates a new handler instance between runs.

---

## Handle errors in error callbacks

Frameworks call error hooks (`on_llm_error`, `on_tool_error`) when operations fail. If you call `record_llm_call(status="error")` inside these, loop detection may fire. You must catch `GuardrailExceeded` in error hooks too:

```python
def on_llm_error(self, error, **kwargs):
    try:
        record_llm_call(model=model, status="error", error=error, ...)
    except GuardrailExceeded as e:
        self._abort_exception = e
        raise _MaidaAbortSignal(e) from e
```

---

## Loop detection deduplication

The core loop detector emits one `LOOP_WARNING` per distinct pattern and deduplicates subsequent detections. When `stop_on_loop=True`, the core re-raises `LoopAbort` on every detection opportunity (even for already-emitted patterns) so that frameworks that swallow the first exception still get interrupted.

Your adapter does not need to handle dedup — the core handles it in `_maybe_emit_loop_warning`. Your adapter only needs to:
1. Catch `GuardrailExceeded` from `record_*` calls
2. Escalate to `_MaidaAbortSignal`
3. Guard subsequent hooks with `_check_aborted()`

---

## Async support

The `@trace` decorator detects async functions via `asyncio.iscoroutinefunction()` and uses an `async def` wrapper that `await`s inside the `_run_context`. Without this, the context manager tears down before the coroutine body executes, and no events are recorded.

If your integration involves async callbacks, ensure the adapter works in both sync and async contexts. The `_MaidaAbortSignal` pattern works identically in both — `BaseException` subclasses propagate through `await` chains.

---

## Nested `traced_run` inside `@trace`

When `traced_run(stop_on_loop=True)` is used inside an existing `@trace` run, the `_run_context` applies the inner guardrail params for the duration of the block. This is handled by the lifecycle layer — your adapter does not need to worry about it.

However, be aware that for sync code, the inner `traced_run` reuses the outer run (no new `run_id`). For async code where `@trace` wraps an async function, the outer context is properly maintained.

---

## Testing checklist

Every integration should have tests for:

1. **Normal event recording** — LLM calls and tool calls are recorded with correct payloads
2. **Error status recording** — failed operations record `status="error"` with error details
3. **Guardrail propagation** — when `stop_on_loop=True` fires, the abort signal propagates through the simulated framework and the run ends with `status="error"`
4. **Abort guard blocks subsequent operations** — after an abort, `on_*_start` methods raise immediately
5. **Handler reset** — `reset()` clears abort state for reuse
6. **Dedup** — only one `LOOP_WARNING` per distinct pattern, even when the abort is caught

Use `_simulate_framework_handle_event` helpers that mimic the framework's error handling:

```python
def _simulate_handle_event(handler, method_name, *args, **kwargs):
    """Simulate framework error handling: except Exception swallows errors."""
    try:
        getattr(handler, method_name)(*args, **kwargs)
    except Exception:
        # Framework swallows Exception but not BaseException
        pass
```

This ensures your tests verify that `_MaidaAbortSignal` (BaseException) propagates while `GuardrailExceeded` (Exception) would be swallowed.

---

## Quick reference

| Concern | Pattern |
|---|---|
| Guardrail exception from core | `GuardrailExceeded(Exception)` — caught by frameworks |
| Abort signal from adapter | `_MaidaAbortSignal(BaseException)` — bypasses frameworks |
| Store abort for fallback | `self._abort_exception = e` before raising signal |
| Guard subsequent hooks | `_check_aborted()` at start of every `on_*_start` / `on_span_start` |
| Reset for reuse | Explicit `reset()` method, never auto-reset in hooks |
| Error callbacks | Catch `GuardrailExceeded` in `on_*_error` too |
| Lifecycle handling | `_run_context` catches `_MaidaAbortSignal`, records ERROR + RUN_END, unwraps to public exception |
