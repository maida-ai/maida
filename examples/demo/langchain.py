"""
LangChain demo: produces a single run containing:
- TOOL_CALL / LLM_CALL captured automatically via LangChainCallbackHandler
- LOOP_WARNING from repeated tool calls
- TOOL_CALL error recorded via on_tool_error callback (no network)
- optional final LLM_CALL after the tool error

Run from repo root after installing extras:
  uv pip install -e ".[langchain]"
  uv run python --extra langchain -m examples.demo.langchain
Then:
  maida view
"""

import os
import sys

from maida import trace


def _ensure_demo_defaults() -> None:
    os.environ.setdefault("MAIDA_LOOP_WINDOW", "12")
    os.environ.setdefault("MAIDA_LOOP_REPETITIONS", "3")


def _require_langchain() -> None:
    try:
        import langchain  # noqa: F401
    except Exception:
        print("[demo] LangChain not installed.")
        print('[demo] Install with: uv pip install -e ".[langchain]"')
        print("[demo] or run with: uv run --extra langchain ... (from repo root)")
        sys.exit(2)


@trace(name="langchain demo")
def run_demo() -> None:
    from maida.integrations import LangChainCallbackHandler

    handler = LangChainCallbackHandler()
    config = {"callbacks": [handler]}

    # Local-only fake LLM (no network)
    from langchain_core.language_models.fake import FakeListLLM
    from langchain_core.tools import tool

    llm = FakeListLLM(
        responses=[
            "This is a traced LLM response (fake).",
            "A follow-up after the tool error (fake).",
        ]
    )

    @tool
    def search_docs(query: str) -> str:
        """Stub tool for the demo."""
        return f"hit: {query}"

    @tool
    def flaky_tool(x: str) -> str:
        """Tool that fails to demonstrate TOOL_CALL status=error via callbacks."""
        raise ValueError("schema mismatch: expected JSON object with fields {id, value}")

    # One tool + one llm (both captured via callbacks)
    _ = search_docs.invoke({"query": "demo"}, config=config)
    _ = llm.invoke("Summarize what the tool returned.", config=config)

    # Loop warning: repeated tool calls back-to-back (m=1)
    for _i in range(6):
        _ = search_docs.invoke({"query": "repeat"}, config=config)

    # Tool error captured by callbacks; swallow exception so we don't get a top-level ERROR event.
    try:
        _ = flaky_tool.invoke({"x": "boom"}, config=config)
    except Exception:
        pass

    # Optional: one more LLM call after the failure (still captured)
    _ = llm.invoke("What failed and why?", config=config)


if __name__ == "__main__":
    _require_langchain()
    _ensure_demo_defaults()
    run_demo()
    print("[demo] run complete. View with: maida view")
