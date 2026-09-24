# Integrations

Maida is framework-agnostic at its core. Adapters translate framework callbacks into the same `LLM_CALL` and `TOOL_CALL` structure; framework-specific details remain in `meta`, and every payload passes through Maida's redaction and truncation before local storage.

| Integration | Install or connect | Guide |
|---|---|---|
| LangChain / LangGraph | `uv add "maida-ai[langchain]>=0.5"` | [Callback handler](integrations/langchain-langgraph.md) |
| OpenAI Agents SDK | `uv add "maida-ai[openai]>=0.5"` | [Tracing adapter](integrations/openai-agents.md) |
| CrewAI | unsupported after v0.5.3 (pin `maida-ai[crewai]==0.5.3`) | [Execution-hook adapter](integrations/crewai.md) |
| Langfuse | Existing completed traces | [Langfuse import guide](langfuse.md) |

CrewAI support was dropped after v0.5.3 because crewai dependency conflicts
block Python 3.14 and openai upgrades. The adapter code remains in-tree; the
`[crewai]` extra does not. See the [CrewAI guide](integrations/crewai.md) for
details and the v0.5.3 pin.

Download the deterministic offline examples directly:

- <a href="/docs/assets/examples/langchain-minimal.py" download>LangChain and LangGraph example</a>
- <a href="/docs/assets/examples/openai-agents-minimal.py" download>OpenAI Agents example</a>
- <a href="/docs/assets/examples/crewai-minimal.py" download>CrewAI example</a> (historical; requires installing `crewai` yourself on post-v0.5.3 checkouts)

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
