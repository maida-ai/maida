# Integrations

## Philosophy

Maida is **framework-agnostic** at the core. The SDK is a thin layer: you call `@trace` and `record_llm_call` / `record_tool_call` / `record_state` from any Python code. No required dependency on LangChain, OpenAI Agents SDK, or others.

**Adapters** are thin translation layers: they hook into a framework's callbacks and emit Maida events. They do not lock you into that framework for the rest of your app.

---

## Available

### LangChain / LangGraph callback handler

**Status: available.** An optional callback handler lives at `maida.integrations.langchain`. It records LLM calls and tool calls to the active Maida run automatically.

**Requirements:** `langchain-core` must be installed. Install the optional dependency group:

```bash
pip install "maida-ai[langchain]"
```

If `langchain-core` is not installed, importing the integration raises a clear `ImportError` with install instructions. The integration is optional; the core package does not depend on it.

**Usage:**

```python
from maida import trace
from maida.integrations import LangChainCallbackHandler

@trace
def run_agent():
    handler = LangChainCallbackHandler()
    config = {"callbacks": [handler]}

    # Use config with any LangChain chain, LLM, or tool:
    result = my_chain.invoke(input_data, config=config)
    return result
```

The handler captures:

- **LLM calls** (`on_llm_start` / `on_chat_model_start` -> `on_llm_end`): records model name, prompt, response, and token usage via `record_llm_call`.
- **Tool calls** (`on_tool_start` -> `on_tool_end` / `on_tool_error`): records tool name, args, result, and error status via `record_tool_call`.

For a deterministic, offline success case, run
[`examples/langchain/minimal.py`](../examples/langchain/minimal.py). It uses a
fake local LLM and a stub tool, so it needs no API key and makes no network
calls:

```bash
uv run --extra langchain python examples/langchain/minimal.py
maida view
```

For the failure/regression side of the workflow, run
[`examples/demo/langchain.py`](../examples/demo/langchain.py). It deterministically
records a repeated-tool `LOOP_WARNING` and a failed `TOOL_CALL(status="error")`
without calling a provider:

```bash
uv run --extra langchain python -m examples.demo.langchain
maida view
```

The minimal example demonstrates the known-good structural path; the demo makes
the loop and tool failure visible in the execution timeline so you can inspect
the behavior that a baseline and policy should reject before merge.

**Guardrails (e.g. `stop_on_loop`) with LangChain / LangGraph:**
All guardrails work with the callback handler. When a guardrail fires, the handler raises `_MaidaAbortSignal` (a `BaseException`) which bypasses both LangChain's callback error handling and LangGraph's graph executor — stopping the run immediately and preventing further token-wasting LLM calls. See [Guardrails](guardrails.md) for details. To reuse a handler across runs, call `handler.reset()` between runs.

**Notes:**

- The handler requires an active Maida run - wrap your entrypoint with `@trace` or set `MAIDA_IMPLICIT_RUN=1`.
- Tool errors are recorded as `TOOL_CALL` events with `status="error"` and include the error message.
- LLM errors are recorded as `LLM_CALL` events with `status="error"` (not as separate `ERROR` events).

---

### OpenAI Agents SDK tracing adapter

**Status: available.** An optional adapter lives at `maida.integrations.openai_agents`. Importing it registers an OpenAI Agents tracing processor that forwards SDK generation, function, and handoff spans into the active Maida run.

**Requirements:** `openai-agents` must be installed. Install Maida with the optional OpenAI dependency group:

```bash
pip install "maida-ai[openai]"
```

If `openai-agents` is not installed, importing the integration raises a clear `ImportError` with install instructions. The integration is optional; the core package does not depend on it.

**Usage:**

```python
from maida import trace
from maida.integrations import openai_agents  # registers hooks


@trace
def run_agent():
    # ... OpenAI Agents SDK code ...
    pass
```

The adapter captures:

- **LLM calls** (`GenerationSpanData`): records model, prompt, response, and usage via `record_llm_call`.
- **Tool calls** (`FunctionSpanData`): records tool name, args, result, and error status via `record_tool_call`.
- **Handoffs** (`HandoffSpanData`): records a `TOOL_CALL` named `handoff`, with framework-specific details stored in `meta`.

The runnable [`examples/openai_agents/minimal.py`](../examples/openai_agents/minimal.py) example uses only local SDK tracing spans with fixed payloads. It requires no API key, provider call, or network access:

```bash
uv run --extra openai python examples/openai_agents/minimal.py
maida baseline --out openai-agents-baseline.json
maida assert --baseline openai-agents-baseline.json
```

The known-good structural signature is `RUN_START -> LLM_CALL -> TOOL_CALL(lookup_docs) -> TOOL_CALL(handoff) -> RUN_END`, with one `gpt-4o-mini` call, the tool sequence `lookup_docs -> handoff`, 22 total tokens, and terminal status `ok`.

Run the same offline example in regression mode to repeat the documentation lookup:

```bash
uv run --extra openai python examples/openai_agents/minimal.py --regression
maida assert --baseline openai-agents-baseline.json
```

The regression records three consecutive `lookup_docs` calls. Its structural signature is `RUN_START -> LLM_CALL -> TOOL_CALL(lookup_docs) -> TOOL_CALL(lookup_docs) -> TOOL_CALL(lookup_docs) -> LOOP_WARNING -> TOOL_CALL(handoff) -> RUN_END`. The run itself still ends with status `ok`, but the final gate reports the increased step and tool-call counts and exits with code `1`.

Maida's surrounding `@trace` boundary owns `RUN_START` and `RUN_END`. The adapter maps completed generation, function, and handoff spans exposed by the SDK; it does not synthesize successful calls or framework-specific event types for SDK signals it cannot observe.

**Guardrails with OpenAI Agents SDK:**
All guardrails work with the tracing processor. When a guardrail fires, the processor raises `_MaidaAbortSignal` (a `BaseException`) which bypasses the SDK's `except Exception` error handling — stopping the run immediately:

```python
from maida import trace, LoopAbort

@trace(stop_on_loop=True)
def run_agent():
    result = Runner.run_sync(agent, input)
    return result
```

As a defensive fallback, the exception is also stored on `PROCESSOR.abort_exception` with a `PROCESSOR.raise_if_aborted()` convenience method.

**Notes:**

- The adapter records events only while an explicit Maida run is active; wrap your entrypoint with `@trace` or `traced_run(...)`.
- Framework-specific span details stay in `meta.openai_agents.*`, not the event payload.
- The example uses low-level SDK tracing spans with deterministic fake data, so it needs no API key and makes no model calls.

---

### CrewAI execution-hook adapter

**Status: available.** An optional adapter lives at `maida.integrations.crewai`. Importing it registers CrewAI execution hooks that automatically record LLM and tool calls into the active Maida run.

**Requirements:** `crewai[tools]` must be installed. Install the optional dependency group:

```bash
pip install "maida-ai[crewai]"
```

If `crewai` is not installed, importing the integration raises a clear `ImportError` with install instructions.

**Usage:**

```python
import maida
from maida.integrations import crewai as maida_crewai  # registers hooks

@maida.trace
def run_crew():
    # ... your CrewAI crew.kickoff() or flow.kickoff() ...
    pass
```

The adapter captures:

- **LLM calls** (`before_llm_call` / `after_llm_call`): records model, prompt messages, and response via `record_llm_call`.
- **Tool calls** (`before_tool_call` / `after_tool_call`): records tool name, args, result, and timing via `record_tool_call`.

Framework-specific context (agent role, task description, executor ID) is stored in `meta.crewai.*`.

The [offline CrewAI example](../examples/crewai/minimal.py) sends deterministic
fake data through CrewAI's public hook contexts. It starts no crew or LLM, uses
no API key, and makes no network calls:

```bash
CREWAI_DISABLE_TELEMETRY=true python examples/crewai/minimal.py
maida baseline --out crewai-baseline.json
maida assert --baseline crewai-baseline.json --tool-call-tolerance 0
```

The normal signature is
`RUN_START -> LLM_CALL -> TOOL_CALL(search_docs) -> RUN_END`, with one LLM
call, one `search_docs` call, and terminal status `ok`.

Run the deterministic regression and apply the same strict assertion:

```bash
CREWAI_DISABLE_TELEMETRY=true python examples/crewai/minimal.py --regression
maida assert --baseline crewai-baseline.json --tool-call-tolerance 0
```

Regression mode records three consecutive `search_docs` calls, producing
`RUN_START -> LLM_CALL -> TOOL_CALL -> TOOL_CALL -> TOOL_CALL -> LOOP_WARNING -> RUN_END`.
The run itself remains `ok`, but the assertion reports the tool-call increase
and exits with code `1`.

When a guardrail fires in a hook, the adapter stores the public
`GuardrailExceeded`, raises an internal `BaseException` signal past CrewAI's
`except Exception` handling, and lets the Maida boundary record `ERROR` plus
`RUN_END(status="error")`. As a defensive fallback inside an active run, call
`maida_crewai.raise_if_aborted()` after framework execution.

**Notes:**

- The adapter requires an active Maida run — wrap your entrypoint with `@trace` or `traced_run(...)`.
- Hook ordering caveat: if another before-hook returns `False` and blocks execution, that specific call may not be captured.
- The example unregisters CrewAI's event-bus shutdown callback after its fake-hook-only run to avoid waiting on an idle daemon during interpreter shutdown.
- For a fuller success, incomplete-call, and guardrail walkthrough, see the [CrewAI tutorial](https://github.com/maida-ai/maida-tutorials/blob/main/CrewAI/Mock%20CrewAI%20Agent.ipynb).

---

## Planned

Planned framework adapters (not yet implemented):

1. **Agno** - optional adapter for Agno-based agents.
2. Others as needed (e.g. AutoGen, custom loops).

For guidance on adding new integrations, start with [CONTRIBUTING.md](../CONTRIBUTING.md#adding-integrations--adapters) and satisfy the normative [adapter conformance contract](../maida/integrations/CONTRIBUTING.md#adapter-conformance-contract), including normalized signals, deterministic offline tests, payload safety, and namespaced framework metadata.
