"""Compatibility facade for the tier-aware fixed-budget runner."""

from maida.runner_v2 import (
    REPORT_VERSION,
    RunExecutionError,
    TrialRecord,
    TrialRunReport,
    run_trials,
)

__all__ = [
    "REPORT_VERSION",
    "RunExecutionError",
    "TrialRecord",
    "TrialRunReport",
    "run_trials",
]
