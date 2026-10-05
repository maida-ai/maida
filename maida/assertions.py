"""Assertion engine: policy checks and result aggregation.

``run_assertions`` compares a completed run against a baseline and/or
standalone thresholds.  Each enabled check produces an ``AssertionResult``;
results are collected into an ``AssertionReport`` with an overall pass/fail.

Report formatting lives in ``maida.report``.
"""

from maida._assertions.engine import run_assertions
from maida._assertions.types import (
    AssertionPolicy,
    AssertionReport,
    AssertionResult,
    RegressionReasonCode,
)

__all__ = [
    "AssertionPolicy",
    "AssertionReport",
    "AssertionResult",
    "RegressionReasonCode",
    "run_assertions",
]
