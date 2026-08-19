# LangChain and LangGraph

**Status: available.** An optional callback handler lives at `maida.integrations.langchain`. It records LLM calls and tool calls to the active Maida run automatically.

**Requirements:** `langchain-core` must be installed. Install Maida with the LangChain extra:

```bash
uv add "maida-ai[langchain]>=0.5"
```

If `langchain-core` is missing, accessing `LangChainCallbackHandler` raises an `ImportError` that identifies the LangChain extra. Importing core `maida` remains safe.

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

The <a href="/docs/assets/examples/langchain-minimal.py" download>offline LangChain example</a> uses `FakeListLLM` and a local tool, so it requires no API key or network call:

```bash
python langchain-minimal.py
maida view
```

The normal run has this structural signature:

- event sequence: `RUN_START -> TOOL_CALL -> LLM_CALL -> RUN_END`
- tool sequence: `lookup` (one call)
- LLM calls: one `FakeListLLM` call
- terminal status: `ok`

The deterministic examples below retain the legacy single-run interface only
as a migration aid for comparing already-completed adapter traces. New
multi-trial gates should execute the agent with `maida run` and policy v2.

Use the LangChain regression mode to see the compatibility check catch one
extra tool call:

```bash
# Capture the known-good behavior.
python langchain-minimal.py
maida baseline --out langchain-baseline.json

# Simulate a code change that repeats the local lookup.
python langchain-minimal.py --regression
maida assert --baseline langchain-baseline.json --tool-call-tolerance 0
```

The regression signature is `RUN_START -> TOOL_CALL -> TOOL_CALL -> LLM_CALL -> RUN_END`, with the tool sequence `lookup -> lookup`, one `FakeListLLM` call, and terminal status `ok`. The final command reports the tool-call increase from 1 to 2 and exits with code `1`, so the same check can block a pull request even though the agent itself completed successfully.

For a multi-node graph, loop failure, and guardrail walkthrough, continue with the [full LangGraph tutorial](https://github.com/maida-ai/maida-tutorials/blob/main/LangChain/Mock%20LangGraph%20Agent.ipynb).

**Guardrails (e.g. `stop_on_loop`) with LangChain / LangGraph:**
All guardrails work with the callback handler. When a guardrail fires, the handler raises `_MaidaAbortSignal` (a `BaseException`) which bypasses both LangChain's callback error handling and LangGraph's graph executor -- stopping the run immediately and preventing further token-wasting LLM calls. See [Guardrails](../guardrails.md) for details. To reuse a handler across runs, call `handler.reset()` between runs.

**Notes:**

- The handler requires an active Maida run - wrap your entrypoint with `@trace` or set `MAIDA_IMPLICIT_RUN=1`.
- Only callbacks delivered to this handler are recorded. Calls made without the handler in their callback config are invisible to Maida.
- Tool errors are recorded as `TOOL_CALL` events with `status="error"` and include the error message.
- LLM errors are recorded as `LLM_CALL` events with `status="error"` (not as separate `ERROR` events).
