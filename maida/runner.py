"""Compatibility facade for the tier-aware fixed-budget runner."""

from maida._runner.trials import run_trials
from maida._runner.types import (
    REPORT_VERSION,
    RunExecutionError,
    TrialRecord,
    TrialRunReport,
)

__all__ = [
    "REPORT_VERSION",
    "RunExecutionError",
    "TrialRecord",
    "TrialRunReport",
    "run_trials",
]
