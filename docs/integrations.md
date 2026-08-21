# Integrations

Maida is framework-agnostic at its core. Adapters translate framework callbacks into the same `LLM_CALL` and `TOOL_CALL` structure; framework-specific details remain in `meta`, and every payload passes through Maida's redaction and truncation before local storage.

| Integration | Install or connect | Guide |
|---|---|---|
| LangChain / LangGraph | `uv add "maida-ai[langchain]>=0.5"` | [Callback handler](integrations/langchain-langgraph.md) |
| OpenAI Agents SDK | `uv add "maida-ai[openai]>=0.5"` | [Tracing adapter](integrations/openai-agents.md) |
| CrewAI | `uv add "maida-ai[crewai]>=0.5"` | [Execution-hook adapter](integrations/crewai.md) |
| Langfuse | Existing completed traces | [Langfuse import guide](langfuse.md) |

Download the deterministic offline examples directly:

- <a href="/docs/assets/examples/langchain-minimal.py" download>LangChain and LangGraph example</a>
- <a href="/docs/assets/examples/openai-agents-minimal.py" download>OpenAI Agents example</a>
- <a href="/docs/assets/examples/crewai-minimal.py" download>CrewAI example</a>

## Shared contract

- Framework packages are optional and loaded only when their adapter is imported.
- Equivalent model and tool activity produces framework-neutral trace events.
- Adapters require an active `@trace` or `traced_run(...)`; they never create unrelated runs.
- Framework errors remain the application's errors and are recorded as error-status activity.
- Prompts, responses, arguments, results, errors, and metadata use the same recursive redaction and byte limits as direct SDK calls.

If an optional dependency is absent, importing its adapter fails immediately with an `ImportError` that names the required Maida extra. Core Maida remains importable without any framework installed.

```{toctree}
:hidden:
:maxdepth: 1

integrations/langchain-langgraph
integrations/openai-agents
integrations/crewai
langfuse
```
