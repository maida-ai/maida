"""
Minimal OpenAI Agents tracing example with fake spans only using Maida.

Run from the repo root:
  uv run --extra openai python examples/openai_agents/minimal.py

Then:
  maida view
"""

from maida import trace
from maida.integrations import openai_agents
from agents.tracing import (
    function_span,
    generation_span,
    handoff_span,
    set_trace_processors,
    trace as agents_trace,
)


@trace(name="OpenAI Agents minimal example")
def run_agent():
    """Emit deterministic SDK spans without making any model or network calls."""
    # Keep the SDK tracing local-only for this example: no backend exporter, no API key.
    set_trace_processors([openai_agents.PROCESSOR])

    with agents_trace("Maida OpenAI Agents example"):
        with generation_span(
            input=[{"role": "user", "content": "Summarize Maida in one sentence."}],
            output=[
                {
                    "role": "assistant",
                    "content": "Maida is a local-first behavioral regression gate for AI agents.",
                }
            ],
            model="gpt-4o-mini",
            model_config={"temperature": 0.0},
            usage={"prompt_tokens": 10, "completion_tokens": 12, "total_tokens": 22},
        ):
            pass

        with function_span(
            name="lookup_docs",
            input={"query": "Maida integrations"},
            output={"hits": 2},
        ):
            pass

        with handoff_span(from_agent="router_agent", to_agent="docs_agent"):
            pass


if __name__ == "__main__":
    run_agent()
    print("Run complete. View with: maida view")
