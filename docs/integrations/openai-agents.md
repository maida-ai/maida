# OpenAI Agents SDK

**Status: available.** An optional adapter lives at `maida.integrations.openai_agents`. Importing it registers an OpenAI Agents tracing processor that forwards SDK generation, function, and handoff spans into the active Maida run.

**Requirements:** `openai-agents` must be installed. Install Maida with the OpenAI extra:

```bash
uv add "maida-ai[openai]>=0.5"
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

The offline example — <a href="/docs/assets/examples/openai-agents-minimal.py" download>download it</a>, or run
[`examples/openai_agents/minimal.py`](https://github.com/maida-ai/maida/blob/main/examples/openai_agents/minimal.py)
from a checkout — constructs SDK tracing spans with fixed payloads and replaces
the SDK processor list with Maida's processor. It requires no API key, provider
call, or network access:

```bash
uv add "maida-ai[openai]>=0.5"
uv run --extra openai python examples/openai_agents/minimal.py
maida view
```

The known-good structural signature is:

- event sequence: `RUN_START -> LLM_CALL -> TOOL_CALL(lookup_docs) -> TOOL_CALL(handoff) -> RUN_END`
- tool sequence: `lookup_docs -> handoff` (two calls)
- LLM calls: one `gpt-4o-mini` call
- terminal status: `ok`

Capture that known-good behavior and confirm it passes the gate:

```bash
uv run --extra openai python examples/openai_agents/minimal.py
maida baseline --out openai-agents-baseline.json
maida assert --baseline openai-agents-baseline.json
```

Then run the deterministic regression mode, which repeats the documentation
lookup:

```bash
uv run --extra openai python examples/openai_agents/minimal.py --regression
maida assert --baseline openai-agents-baseline.json --tool-call-tolerance 0
```

The regression records three consecutive `lookup_docs` calls, giving
`RUN_START -> LLM_CALL -> TOOL_CALL(lookup_docs) -> TOOL_CALL(lookup_docs) -> TOOL_CALL(lookup_docs) -> LOOP_WARNING -> TOOL_CALL(handoff) -> RUN_END`.
The run still ends `ok`, but the gate reports the increased step and tool-call
counts and exits with code `1`.

Maida's surrounding `@trace` boundary owns `RUN_START` and `RUN_END`. The
adapter maps completed generation, function, and handoff spans exposed by the
SDK; it does not synthesize successful calls or framework-specific event types
for SDK signals it cannot observe.

For an end-to-end agent workflow and guardrail walkthrough, continue with the [full OpenAI Agents tutorial](https://github.com/maida-ai/maida-tutorials/blob/main/OpenAI/Mock%20OpenAI%20Agent.ipynb).

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
- Importing the adapter registers a process-wide tracing processor. If your application later replaces the SDK processor list, include `openai_agents.PROCESSOR` in the replacement list.
- Framework-specific span details stay in `meta.openai_agents.*`, not the event payload.
- The example uses low-level SDK tracing spans with deterministic fake data, so it needs no API key and makes no model calls.
