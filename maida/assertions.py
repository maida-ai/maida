"""Assertion engine: policy checks, result aggregation, and report formatting.

``run_assertions`` compares a completed run against a baseline and/or
standalone thresholds.  Each enabled check produces an ``AssertionResult``;
results are collected into an ``AssertionReport`` with an overall pass/fail.
"""

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from maida.diff import RunDiff

from maida.baseline import extract_run_metrics
from maida.config import MaidaConfig
from maida.events import spans_to_events
from maida.storage import load_run_meta, load_spans, resolve_trace_id


kDefaultTolerance = 0.5  # 50% global default


@dataclass
class AssertionPolicy:
    """Policy configuration for assert checks.

    Note: all tolerances are fractional, not percentage.
    """

    # Maximum allowed step count
    max_steps: int | None = None
    step_tolerance: float = kDefaultTolerance

    # Maximum allowed tool call count
    max_tool_calls: int | None = None
    tool_call_tolerance: float = kDefaultTolerance

    # Maximum allowed cost tokens
    max_cost_tokens: int | None = None
    cost_tolerance: float = kDefaultTolerance

    # Maximum allowed duration in milliseconds
    max_duration_ms: int | None = None
    duration_tolerance: float = kDefaultTolerance

    no_new_tools: bool = False  # Fail if run uses tools not in baseline
    no_loops: bool = False  # Fail if any LOOP_WARNING present
    no_guardrails: bool = False  # Fail if any guardrail was triggered
    expect_status: str | None = None  # Expected run status (ok or error)


@dataclass
class AssertionResult:
    """Result of a single assertion check."""

    check_name: str
    passed: bool
    message: str
    expected: str | None = None
    actual: str | None = None


@dataclass
class AssertionReport:
    """Full report from running assertions."""

    run_id: str
    baseline_run_id: str | None
    results: list[AssertionResult] = field(default_factory=list)
    passed: bool = True

    def add(self, result: AssertionResult) -> None:
        self.results.append(result)
        if not result.passed:
            self.passed = False


def _check_threshold(
    actual: int | float,
    baseline_value: int | float | None,
    tolerance: float,
    standalone_max: int | float | None,
    check_name: str,
    unit: str,
) -> AssertionResult | None:
    """Shared threshold/tolerance comparison for numeric metrics.

    Returns an ``AssertionResult`` if any check was enabled, else ``None``.
    """
    if baseline_value is not None and standalone_max is not None:
        if baseline_value > 0:
            limit = min(
                baseline_value * (1 + tolerance),
                float(standalone_max),
            )
        else:
            limit = float(standalone_max)
        passed = actual <= limit
        return AssertionResult(
            check_name=check_name,
            passed=passed,
            message=(
                f"{int(actual)} {unit} (baseline: {int(baseline_value)}, "
                f"tolerance: {tolerance:.0%}, cap: {standalone_max})"
            ),
            expected=str(int(limit)),
            actual=str(int(actual)),
        )

    if baseline_value is not None:
        if baseline_value > 0:
            limit = baseline_value * (1 + tolerance)
        else:
            limit = float(standalone_max) if standalone_max is not None else 0.0
        passed = actual <= limit
        return AssertionResult(
            check_name=check_name,
            passed=passed,
            message=(
                f"{int(actual)} {unit} (baseline: {int(baseline_value)}, "
                f"tolerance: {tolerance:.0%})"
            ),
            expected=str(int(limit)),
            actual=str(int(actual)),
        )

    if standalone_max is not None:
        passed = actual <= standalone_max
        return AssertionResult(
            check_name=check_name,
            passed=passed,
            message=f"{int(actual)} {unit} (max: {standalone_max})",
            expected=str(standalone_max),
            actual=str(int(actual)),
        )

    return None


def run_assertions(
    trace_id: str,
    policy: AssertionPolicy,
    baseline: dict | None = None,
    config: MaidaConfig | None = None,
) -> AssertionReport:
    """Run all enabled assertion checks against a completed run.

    Args:
        trace_id: The OTel trace ID (or prefix) for the run.
        policy: The assertion policy with thresholds.
        baseline: Optional baseline dict to compare against.
        config: MaidaConfig (loaded via ``load_config`` if ``None``).

    Returns:
        ``AssertionReport`` with all check results.
    """
    if config is None:
        from maida.config import load_config

        config = load_config()

    full_id = resolve_trace_id(trace_id, config)
    meta = load_run_meta(full_id, config)
    spans = load_spans(full_id, config)
    events = spans_to_events(spans)
    metrics = extract_run_metrics(meta, events)
    summary = metrics["summary"]
    b_summary = (baseline or {}).get("summary")

    report = AssertionReport(
        run_id=full_id,
        baseline_run_id=(baseline or {}).get("source_run_id"),
    )

    # --- step count ---
    r = _check_threshold(
        actual=summary["total_events"],
        baseline_value=b_summary["total_events"] if b_summary else None,
        tolerance=policy.step_tolerance,
        standalone_max=policy.max_steps,
        check_name="step_count",
        unit="steps",
    )
    if r:
        report.add(r)

    # --- tool calls ---
    r = _check_threshold(
        actual=summary["tool_calls"],
        baseline_value=b_summary["tool_calls"] if b_summary else None,
        tolerance=policy.tool_call_tolerance,
        standalone_max=policy.max_tool_calls,
        check_name="tool_calls",
        unit="tool calls",
    )
    if r:
        report.add(r)

    # --- no new tools ---
    if policy.no_new_tools and baseline is not None:
        baseline_tools = set(baseline.get("tool_path") or [])
        run_tools = set(metrics["tool_path"])
        new_tools = sorted(run_tools - baseline_tools)
        passed = len(new_tools) == 0
        report.add(
            AssertionResult(
                check_name="new_tools",
                passed=passed,
                message=(
                    "no new tools" if passed else f"unexpected tools used: {new_tools}"
                ),
                expected="none",
                actual=str(new_tools) if new_tools else "none",
            )
        )

    # --- no loops ---
    if policy.no_loops:
        loop_count = summary["loop_warnings"]
        passed = loop_count == 0
        report.add(
            AssertionResult(
                check_name="no_loops",
                passed=passed,
                message=(
                    "no loop warnings detected"
                    if passed
                    else f"{loop_count} loop warning(s) detected"
                ),
                actual=str(loop_count),
            )
        )

    # --- no guardrails ---
    if policy.no_guardrails:
        gr_count = len(metrics["guardrail_events"])
        passed = gr_count == 0
        report.add(
            AssertionResult(
                check_name="no_guardrails",
                passed=passed,
                message=(
                    "no guardrail events"
                    if passed
                    else f"{gr_count} guardrail event(s) detected"
                ),
                actual=str(gr_count),
            )
        )

    # --- cost tokens ---
    r = _check_threshold(
        actual=summary["total_tokens"],
        baseline_value=b_summary["total_tokens"] if b_summary else None,
        tolerance=policy.cost_tolerance,
        standalone_max=policy.max_cost_tokens,
        check_name="cost_tokens",
        unit="tokens",
    )
    if r:
        report.add(r)

    # --- duration ---
    r = _check_threshold(
        actual=summary["duration_ms"],
        baseline_value=b_summary["duration_ms"] if b_summary else None,
        tolerance=policy.duration_tolerance,
        standalone_max=policy.max_duration_ms,
        check_name="duration",
        unit="ms",
    )
    if r:
        report.add(r)

    # --- expect status ---
    if policy.expect_status is not None:
        actual_status = meta.get("status", "")
        passed = actual_status == policy.expect_status
        report.add(
            AssertionResult(
                check_name="expect_status",
                passed=passed,
                message=(
                    f"status is '{actual_status}'"
                    if passed
                    else f"expected '{policy.expect_status}', got '{actual_status}'"
                ),
                expected=policy.expect_status,
                actual=actual_status,
            )
        )

    return report


# ---------------------------------------------------------------------------
# Report formatters
# ---------------------------------------------------------------------------

_PASS = "\u2713"  # ✓
_FAIL = "\u2717"  # ✗


def format_report_text(report: AssertionReport, diff: "RunDiff | None" = None) -> str:
    """Format report as human-readable text for CLI output.

    When *diff* is provided and the report failed, the structural diff is
    appended so the terminal tells the same "what changed" story as the
    Markdown PR comment.
    """
    lines: list[str] = []
    for r in report.results:
        mark = _PASS if r.passed else _FAIL
        lines.append(f"  {mark} {r.check_name}: {r.message}")
    total = len(report.results)
    failed = sum(1 for r in report.results if not r.passed)
    if total == 0:
        lines.append("  (no checks enabled)")
    verdict = "PASSED" if report.passed else "FAILED"
    lines.append("")
    if failed:
        lines.append(f"RESULT: {verdict} ({failed} of {total} checks failed)")
    else:
        lines.append(f"RESULT: {verdict} ({total} checks passed)")
    if diff is not None and not report.passed:
        from maida.diff import format_diff_text

        lines.append("")
        lines.append(format_diff_text(diff))
    return "\n".join(lines)


def format_report_json(report: AssertionReport) -> str:
    """Format report as JSON for machine consumption."""
    data: dict[str, Any] = {
        "run_id": report.run_id,
        "baseline_run_id": report.baseline_run_id,
        "passed": report.passed,
        "results": [
            {
                "check_name": r.check_name,
                "passed": r.passed,
                "message": r.message,
                "expected": r.expected,
                "actual": r.actual,
            }
            for r in report.results
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2)


def format_report_markdown(
    report: AssertionReport,
    diff: "RunDiff | None" = None,
    baseline_path: str | None = None,
) -> str:
    """Format report as Markdown for GitHub PR comments.

    Failed checks come first so reviewers see the regression immediately;
    passing checks are collapsed. When *diff* is provided, a "What changed
    vs baseline" section explains the structural difference, and
    *baseline_path* (when known) makes the local-repro snippet copy-pasteable.
    """
    failed = [r for r in report.results if not r.passed]
    passed = [r for r in report.results if r.passed]
    short_run = report.run_id[:8]

    if report.passed:
        lines = ["## \u2705 Maida gate: no behavioral regression", ""]
    else:
        lines = ["## \u274c Maida gate: agent behavior regressed", ""]

    scope = f"run `{short_run}`"
    if report.baseline_run_id:
        scope += f" vs baseline `{str(report.baseline_run_id)[:8]}`"
    if not report.results:
        lines.append(f"**No checks enabled** \u00b7 {scope}")
    elif failed:
        lines.append(
            f"**{len(failed)} of {len(report.results)} checks failed** \u00b7 {scope}"
        )
    else:
        lines.append(f"**All {len(report.results)} checks passed** \u00b7 {scope}")

    if failed:
        lines += ["", "| Check | Expected | Actual | Details |", "|---|---|---|---|"]
        for r in failed:
            expected = r.expected or "\u2014"
            actual = r.actual or "\u2014"
            lines.append(
                f"| \u274c `{r.check_name}` | {expected} | {actual} | {r.message} |"
            )

    if passed:
        lines += [
            "",
            "<details>",
            f"<summary>\u2705 {len(passed)} passing checks</summary>",
            "",
            "| Check | Details |",
            "|---|---|",
        ]
        for r in passed:
            lines.append(f"| \u2705 `{r.check_name}` | {r.message} |")
        lines += ["", "</details>"]

    if diff is not None:
        from maida.diff import format_diff_markdown

        diff_md = format_diff_markdown(diff)
        if diff_md:
            if report.passed:
                lines += [
                    "",
                    "<details>",
                    "<summary>What changed vs baseline (within tolerance)</summary>",
                    "",
                    diff_md,
                    "",
                    "</details>",
                ]
            else:
                lines += ["", diff_md]

    repro = ["pip install maida-ai"]
    if baseline_path:
        repro.append(f"maida diff {short_run} --baseline {baseline_path}")
    repro.append(f"maida view {short_run}")
    lines += [
        "",
        "<details>",
        "<summary>Reproduce locally</summary>",
        "",
        "```bash",
        *repro,
        "```",
        "",
        "</details>",
        "",
        "---",
        "*Gated by [Maida](https://maida.ai) \u2014 the local-first behavioral"
        " regression gate for AI agents.*",
    ]
    return "\n".join(lines)
