"""Bundled demo agents for ``maida demo`` — simulated, local-only."""

from maida.demo._agents import (
    DEMO_RUN_NAME,
    ensure_demo_env,
    run_good_agent,
    run_refactored_agent,
)

__all__ = [
    "DEMO_RUN_NAME",
    "ensure_demo_env",
    "run_good_agent",
    "run_refactored_agent",
]
