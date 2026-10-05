"""Markdown rendering for calibration grids."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maida.calibration import CalibrationCell


def render_calibration_markdown(cells: list[CalibrationCell]) -> str:
    """Render the full reproducible grid as a documentation table."""

    def rate(value: float | None) -> str:
        return "--" if value is None else f"{value:.2%}"

    lines = [
        "| N | θ | True pass rate | Status | False-fail | Inconclusive | Missed regression |",
        "| ---: | ---: | ---: | --- | ---: | ---: | ---: |",
    ]
    for cell in cells:
        lines.append(
            f"| {cell.trials} | {cell.threshold:.2f} | {cell.true_rate:.2f} | "
            f"{cell.status} | {rate(cell.false_fail_rate)} | "
            f"{rate(cell.inconclusive_rate)} | "
            f"{rate(cell.missed_regression_rate)} |"
        )
    return "\n".join(lines)
