"""Deterministic CrewAI adapter example using public fake hook contexts.

Run from the repo root:
  CREWAI_DISABLE_TELEMETRY=true uv run --extra crewai python examples/crewai/minimal.py
  CREWAI_DISABLE_TELEMETRY=true uv run --extra crewai python examples/crewai/minimal.py --regression
"""

import argparse
import atexit
from types import SimpleNamespace

from crewai.events.event_bus import crewai_event_bus
from crewai.hooks import (
    LLMCallHookContext,
    ToolCallHookContext,
    get_after_llm_call_hooks,
    get_after_tool_call_hooks,
    get_before_llm_call_hooks,
    get_before_tool_call_hooks,
)

from maida import traced_run
from maida.integrations import crewai as maida_crewai


executor = SimpleNamespace(
    messages=[{"role": "user", "content": "Summarize Maida."}],
    agent=SimpleNamespace(role="Release reviewer"),
    task=SimpleNamespace(description="Explain the release gate."),
    crew=SimpleNamespace(),
    llm=SimpleNamespace(model="offline"),
    iterations=0,
)


def emit_fake_tool_call() -> None:
    """Send one deterministic lookup through CrewAI's public tool hooks."""
    context = ToolCallHookContext(
        tool_name="search_docs",
        tool_input={"query": "Maida integrations"},
        tool=SimpleNamespace(),
        agent=executor.agent,
        task=executor.task,
        crew=executor.crew,
        tool_result={"hits": 2},
    )
    for hook in get_before_tool_call_hooks():
        hook(context)
    for hook in get_after_tool_call_hooks():
        hook(context)
    maida_crewai.raise_if_aborted()


def emit_fake_crewai_calls(*, regression: bool = False) -> None:
    """Exercise CrewAI's public hook surface without starting an LLM."""
    with traced_run(name="CrewAI minimal example"):
        llm_context = LLMCallHookContext(executor)
        for hook in get_before_llm_call_hooks():
            hook(llm_context)
        llm_context.response = "Maida blocks behavioral regressions before merge."
        for hook in get_after_llm_call_hooks():
            hook(llm_context)
        maida_crewai.raise_if_aborted()

        tool_calls = 3 if regression else 1
        for _ in range(tool_calls):
            emit_fake_tool_call()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--regression",
        action="store_true",
        help="emit three repeated search_docs calls instead of one",
    )
    args = parser.parse_args()

    emit_fake_crewai_calls(regression=args.regression)
    print("Run complete. View with: maida view")


if __name__ == "__main__":
    main()

    # Current CrewAI releases can wait indefinitely in their event-bus atexit
    # callback after a fake-hook-only run. This one-shot script has no queued
    # events, so leave its daemon loop to interpreter shutdown instead.
    atexit.unregister(crewai_event_bus.shutdown)
