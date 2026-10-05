"""Human and machine-readable gate report formatting.

Formats assertion and trial results for the CLI and PR comment. Presentation
only; evaluation lives in ``maida.assertions`` and ``maida.runner``.
"""

from maida._report.assertions import (
    format_report_json,
    format_report_markdown,
    format_report_text,
)
from maida._report.common import markdown_baseline_provenance, markdown_table_cell
from maida._report.trials import render_trial_report_markdown, render_trial_report_text

__all__ = [
    "format_report_json",
    "format_report_markdown",
    "format_report_text",
    "markdown_baseline_provenance",
    "markdown_table_cell",
    "render_trial_report_markdown",
    "render_trial_report_text",
]
