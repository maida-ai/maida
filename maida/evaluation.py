"""Reusable evaluation of a stored run against a loaded baseline.

This module intentionally has no CLI dependencies. Scenario runners and other
local workflows can evaluate an already-installed run and render the same
reports as ``maida assert`` without routing output through Typer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from maida.assertions import (
    AssertionPolicy,
    AssertionReport,
    format_report_json,
    format_report_markdown,
    format_report_text,
    run_assertions,
)
from maida.config import MaidaConfig, load_config
from maida.diff import RunDiff, compute_diff


@dataclass(frozen=True)
class StoredRunEvaluation:
    """Policy verdict and structural diff for one stored run and baseline."""

    report: AssertionReport
    diff: RunDiff

    @property
    def passed(self) -> bool:
        """Whether every enabled policy assertion passed."""
        return self.report.passed

    def render(
        self,
        output_format: str = "text",
        *,
        baseline_path: str | Path | None = None,
    ) -> str:
        """Render with the same formatters used by the assertion CLI/PR comment."""
        if output_format == "text":
            return format_report_text(self.report, diff=self.diff)
        if output_format == "json":
            return format_report_json(self.report)
        if output_format == "markdown":
            return format_report_markdown(
                self.report,
                diff=self.diff,
                baseline_path=str(baseline_path) if baseline_path is not None else None,
            )
        raise ValueError("output format must be text, json, or markdown")


def evaluate_stored_run_against_baseline(
    trace_id: str,
    baseline: dict,
    policy: AssertionPolicy,
    config: MaidaConfig | None = None,
) -> StoredRunEvaluation:
    """Evaluate one installed trace against a previously loaded baseline.

    Loading and validation of baseline/policy files belongs at the caller's
    boundary. Keeping this seam data-oriented makes it suitable for both the
    CLI and multi-scenario aggregation.
    """
    if config is None:
        config = load_config()
    report = run_assertions(trace_id, policy, baseline=baseline, config=config)
    run_diff = compute_diff(trace_id, baseline=baseline, config=config)
    return StoredRunEvaluation(report=report, diff=run_diff)
