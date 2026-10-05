"""Assertion engine: policy checks, result aggregation, and report formatting.

``run_assertions`` compares a completed run against a baseline and/or
standalone thresholds.  Each enabled check produces an ``AssertionResult``;
results are collected into an ``AssertionReport`` with an overall pass/fail.
"""

from maida._assertions.engine import run_assertions
from maida._assertions.report import (
    format_report_json,
    format_report_markdown,
    format_report_text,
    markdown_baseline_provenance,
    markdown_table_cell,
)
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
    "format_report_json",
    "format_report_markdown",
    "format_report_text",
    "markdown_baseline_provenance",
    "markdown_table_cell",
    "run_assertions",
]
