"""Assertion engine: policy checks, result aggregation, and report formatting.

``run_assertions`` compares a completed run against a baseline and/or
standalone thresholds.  Each enabled check produces an ``AssertionResult``;
results are collected into an ``AssertionReport`` with an overall pass/fail.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from maida.diff import RunDiff

from maida.baseline import extract_run_metrics
from maida.config import MaidaConfig, load_config
from maida.diff import format_diff_markdown, format_diff_text
from maida.storage import load_run_for_analysis


kDefaultTolerance = 0.5  # 50% global default

KNOWN_CHECK_NAMES = frozenset(
    {
        "step_count",
        "tool_calls",
        "new_tools",
        "no_loops",
        "no_guardrails",
        "cost_tokens",
        "duration",
        "expect_status",
    }
)


class RegressionReasonCode(str, Enum):
    """Stable reason codes for assertion decisions and PR comment grouping."""

    NO_REGRESSION = "no_regression"
    STEP_COUNT_EXCEEDED = "step_count_exceeded"
    NEW_TOOL_PATH = "new_tool_path"
    TOOL_CALL_COUNT_EXCEEDED = "tool_call_count_exceeded"
    LOOP_DETECTED = "loop_detected"
    CYCLE_DETECTED = "cycle_detected"
    TERMINAL_STATE_MISSING = "terminal_state_missing"
    GUARDRAIL_EVENT_CHANGED = "guardrail_event_changed"
    LATENCY_ENVELOPE_EXCEEDED = "latency_envelope_exceeded"
    COST_ENVELOPE_EXCEEDED = "cost_envelope_exceeded"


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

    # Explicitly ignored checks (skipped even when thresholds/baseline are set)
    ignored_checks: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.ignored_checks is None:
            self.ignored_checks = []


@dataclass
class AssertionResult:
    """Result of a single assertion check."""

    check_name: str
    passed: bool
    message: str
    reason_code: RegressionReasonCode = RegressionReasonCode.NO_REGRESSION
    expected: str | None = None
    actual: str | None = None
    ignored: bool = False


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

    @property
    def reason_codes(self) -> list[RegressionReasonCode]:
        """Failure reason codes in result order, de-duplicated for machines."""
        return list(
            dict.fromkeys(
                result.reason_code for result in self.results if not result.passed
            )
        )


def _reason_code_for(
    passed: bool, failure_code: RegressionReasonCode
) -> RegressionReasonCode:
    return RegressionReasonCode.NO_REGRESSION if passed else failure_code


def _reason_code_text(reason_code: RegressionReasonCode | str) -> str:
    if isinstance(reason_code, RegressionReasonCode):
        return reason_code.value
    return str(reason_code)


def _loop_signature_summary(events: list[dict], limit: int = 3) -> str:
    loop_events = [e for e in events if e.get("event_type") == "LOOP_WARNING"]
    summaries = []
    for event in loop_events[:limit]:
        payload = event.get("payload") or {}
        pattern = payload.get("pattern") or "unknown"
        pattern_type = payload.get("pattern_type") or "loop"
        repetitions = payload.get("repetitions")
        if repetitions is None:
            summaries.append(f"{pattern_type}: {pattern}")
        else:
            summaries.append(f"{pattern_type} x{repetitions}: {pattern}")
    if len(loop_events) > limit:
        summaries.append(f"+{len(loop_events) - limit} more")
    return "; ".join(summaries)


def _loop_reason_code(events: list[dict]) -> RegressionReasonCode:
    """Return the most specific reason code for recorded loop warnings."""
    for event in events:
        if event.get("event_type") != "LOOP_WARNING":
            continue
        payload = event.get("payload") or {}
        if payload.get("pattern_type") == "cycle":
            return RegressionReasonCode.CYCLE_DETECTED
    return RegressionReasonCode.LOOP_DETECTED


def _check_threshold(
    actual: int | float,
    baseline_value: int | float | None,
    tolerance: float,
    standalone_max: int | float | None,
    check_name: str,
    reason_code: RegressionReasonCode,
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
            reason_code=_reason_code_for(passed, reason_code),
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
            reason_code=_reason_code_for(passed, reason_code),
            expected=str(int(limit)),
            actual=str(int(actual)),
        )

    if standalone_max is not None:
        passed = actual <= standalone_max
        return AssertionResult(
            check_name=check_name,
            passed=passed,
            message=f"{int(actual)} {unit} (max: {standalone_max})",
            reason_code=_reason_code_for(passed, reason_code),
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
        config = load_config()

    full_id, meta, events = load_run_for_analysis(trace_id, config)
    metrics = extract_run_metrics(meta, events)
    summary = metrics["summary"]
    b_summary = (baseline or {}).get("summary")

    report = AssertionReport(
        run_id=full_id,
        baseline_run_id=(baseline or {}).get("source_run_id"),
    )

    _ignored = set(policy.ignored_checks)
    if unknown := _ignored - KNOWN_CHECK_NAMES:
        raise ValueError(
            f"Unknown check name(s) in ignored_checks: {', '.join(sorted(unknown))}. "
            f"Known checks: {', '.join(sorted(KNOWN_CHECK_NAMES))}"
        )

    # --- per-check runner helpers (return None when check is not enabled) ---
    def _threshold(
        name: str,
        actual: int | float,
        baseline_val: int | float | None,
        tolerance: float,
        max_val: int | float | None,
        reason: RegressionReasonCode,
        unit: str,
    ) -> Callable[[], AssertionResult | None]:
        return lambda: _check_threshold(
            actual, baseline_val, tolerance, max_val, name, reason, unit
        )

    def _check_new_tools() -> AssertionResult | None:
        if not (policy.no_new_tools and baseline is not None):
            return None
        bl_tools = set(baseline.get("tool_path") or [])
        run_tools = set(metrics["tool_path"])
        new_tools = sorted(run_tools - bl_tools)
        passed = len(new_tools) == 0
        return AssertionResult(
            check_name="new_tools",
            passed=passed,
            message=(
                "no new tools" if passed else f"unexpected tools used: {new_tools}"
            ),
            reason_code=_reason_code_for(passed, RegressionReasonCode.NEW_TOOL_PATH),
            expected="none",
            actual=str(new_tools) if new_tools else "none",
        )

    def _check_no_loops() -> AssertionResult | None:
        if not policy.no_loops:
            return None
        loop_count = summary["loop_warnings"]
        passed = loop_count == 0
        sig = _loop_signature_summary(events) if not passed else ""
        message = "no loop warnings detected"
        if not passed:
            message = f"{loop_count} loop warning(s) detected"
            if sig:
                message += f": {sig}"
        return AssertionResult(
            check_name="no_loops",
            passed=passed,
            message=message,
            reason_code=_reason_code_for(passed, _loop_reason_code(events)),
            actual=str(loop_count),
        )

    def _check_no_guardrails() -> AssertionResult | None:
        if not policy.no_guardrails:
            return None
        gr_count = len(metrics["guardrail_events"])
        passed = gr_count == 0
        return AssertionResult(
            check_name="no_guardrails",
            passed=passed,
            message=(
                "no guardrail events"
                if passed
                else f"{gr_count} guardrail event(s) detected"
            ),
            reason_code=_reason_code_for(
                passed, RegressionReasonCode.GUARDRAIL_EVENT_CHANGED
            ),
            actual=str(gr_count),
        )

    def _check_expect_status() -> AssertionResult | None:
        if policy.expect_status is None:
            return None
        actual_status = meta.get("status", "")
        passed = actual_status == policy.expect_status
        return AssertionResult(
            check_name="expect_status",
            passed=passed,
            message=(
                f"status is '{actual_status}'"
                if passed
                else f"expected '{policy.expect_status}', got '{actual_status}'"
            ),
            reason_code=_reason_code_for(
                passed, RegressionReasonCode.TERMINAL_STATE_MISSING
            ),
            expected=policy.expect_status,
            actual=actual_status,
        )

    # --- unified runner dispatch ---
    runners: dict[str, Callable[[], AssertionResult | None]] = {
        "step_count": _threshold(
            "step_count",
            summary["total_events"],
            b_summary["total_events"] if b_summary else None,
            policy.step_tolerance,
            policy.max_steps,
            RegressionReasonCode.STEP_COUNT_EXCEEDED,
            "steps",
        ),
        "tool_calls": _threshold(
            "tool_calls",
            summary["tool_calls"],
            b_summary["tool_calls"] if b_summary else None,
            policy.tool_call_tolerance,
            policy.max_tool_calls,
            RegressionReasonCode.TOOL_CALL_COUNT_EXCEEDED,
            "tool calls",
        ),
        "new_tools": _check_new_tools,
        "no_loops": _check_no_loops,
        "no_guardrails": _check_no_guardrails,
        "cost_tokens": _threshold(
            "cost_tokens",
            summary["total_tokens"],
            b_summary["total_tokens"] if b_summary else None,
            policy.cost_tolerance,
            policy.max_cost_tokens,
            RegressionReasonCode.COST_ENVELOPE_EXCEEDED,
            "tokens",
        ),
        "duration": _threshold(
            "duration",
            summary["duration_ms"],
            b_summary["duration_ms"] if b_summary else None,
            policy.duration_tolerance,
            policy.max_duration_ms,
            RegressionReasonCode.LATENCY_ENVELOPE_EXCEEDED,
            "ms",
        ),
        "expect_status": _check_expect_status,
    }

    for name, runner in runners.items():
        r = runner()
        if r and name in _ignored:
            report.add(
                AssertionResult(
                    check_name=name,
                    passed=True,
                    message="check ignored",
                    ignored=True,
                )
            )
        elif r:
            report.add(r)

    return report


# ---------------------------------------------------------------------------
# Report formatters
# ---------------------------------------------------------------------------

_PASS = "\u2713"  # ✓
_FAIL = "\u2717"  # ✗


def _markdown_table_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _markdown_scope(report: AssertionReport) -> str:
    scope = f"run `{report.run_id[:8]}`"
    if report.baseline_run_id:
        scope += f" vs baseline `{str(report.baseline_run_id)[:8]}`"
    return scope


def _markdown_next_steps(
    report: AssertionReport,
    *,
    short_run: str,
    baseline_path: str | None,
) -> list[str]:
    if report.passed:
        return [
            f"- No gate action needed; inspect the trace with `maida view {short_run}` if desired.",
        ]

    steps: list[str] = []
    if baseline_path:
        steps.append(
            "- Inspect the full diff: "
            f"`maida diff {short_run} --baseline {baseline_path}`"
        )
    else:
        steps.append("- Review the failed checks and policy thresholds above.")
    steps += [
        f"- Open the trace locally: `maida view {short_run}`",
    ]
    if baseline_path:
        steps.append(
            "- If this behavior change is intentional, accept it explicitly: "
            f'`maida accept {short_run} --baseline {baseline_path} --reason "..."`'
        )
        steps.append(
            "- Review and commit the baseline diff; otherwise fix the agent behavior and rerun the gate."
        )
    else:
        steps.append(
            "- If this is expected, update the policy; otherwise fix the agent behavior and rerun the gate."
        )
    return steps


def format_report_text(report: AssertionReport, diff: "RunDiff | None" = None) -> str:
    """Format report as human-readable text for CLI output.

    When *diff* is provided and the report failed, the structural diff is
    appended so the terminal tells the same "what changed" story as the
    Markdown PR comment.
    """
    lines: list[str] = []
    for r in report.results:
        if r.ignored:
            lines.append(f"  - {r.check_name} [ignored]")
        else:
            mark = _PASS if r.passed else _FAIL
            lines.append(
                f"  {mark} {r.check_name} [{_reason_code_text(r.reason_code)}]: {r.message}"
            )
    total = len(report.results)
    failed = sum(1 for r in report.results if not r.passed)
    ignored_count = sum(1 for r in report.results if r.ignored)
    active = total - ignored_count
    if total == 0:
        lines.append("  (no checks enabled)")
    verdict = "PASSED" if report.passed else "FAILED"
    lines.append("")
    parts = []
    if active == 0:
        parts.append(f"RESULT: {verdict} (all checks ignored)")
    elif failed:
        parts.append(f"RESULT: {verdict} ({failed} of {active} active checks failed)")
    else:
        parts.append(f"RESULT: {verdict} ({active} active checks passed)")
    if ignored_count:
        parts.append(f"({ignored_count} ignored)")
    lines.append(" ".join(parts))
    if diff is not None and not report.passed:
        lines.append("")
        lines.append(format_diff_text(diff))
    return "\n".join(lines)


def format_report_json(report: AssertionReport) -> str:
    """Format report as JSON for machine consumption."""
    data: dict[str, Any] = {
        "run_id": report.run_id,
        "baseline_run_id": report.baseline_run_id,
        "passed": report.passed,
        "reason_codes": [_reason_code_text(code) for code in report.reason_codes],
        "results": [
            {
                "check_name": r.check_name,
                "passed": r.passed,
                "reason_code": _reason_code_text(r.reason_code),
                "message": r.message,
                "expected": r.expected,
                "actual": r.actual,
                "ignored": r.ignored,
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

    The comment starts with a verdict, then surfaces top behavior changes,
    failed checks grouped by reason code, concise next steps, and a collapsed
    local-repro block. Passing checks are collapsed to keep the default comment
    readable.
    """
    failed = [r for r in report.results if not r.passed]
    passed = [r for r in report.results if r.passed and not r.ignored]
    ignored = [r for r in report.results if r.ignored]
    short_run = report.run_id[:8]

    if report.passed:
        lines = ["## \u2705 Maida verdict: pass", ""]
    else:
        lines = ["## \u274c Maida verdict: fail", ""]

    scope = _markdown_scope(report)
    if not report.results:
        lines.append(f"**No checks enabled** \u00b7 {scope}")
    elif failed:
        lines.append(
            f"**{len(failed)} of {len(report.results)} checks failed** \u00b7 {scope}"
        )
    elif active := len(report.results) - len(ignored):
        parts = [f"**All {active} checks passed** \u00b7 {scope}"]
        if ignored:
            parts.append(f"({len(ignored)} ignored)")
        lines.append(" ".join(parts))
    else:
        parts = [f"**All checks ignored** \u00b7 {scope}"]
        if ignored:
            parts.append(f"({len(ignored)} ignored)")
        lines.append(" ".join(parts))

    diff_md = format_diff_markdown(diff) if diff is not None else ""
    if diff_md:
        if report.passed:
            lines += [
                "",
                "<details>",
                "<summary>Top behavior changes within tolerance</summary>",
                "",
                diff_md,
                "",
                "</details>",
            ]
        else:
            lines += ["", diff_md]

    if failed:
        lines += ["", "### Failed checks by reason code"]
        for reason_code in report.reason_codes:
            reason_failures = [r for r in failed if r.reason_code == reason_code]
            lines += [
                "",
                f"#### `{_reason_code_text(reason_code)}`",
                "",
                "| Check | Expected | Actual | Details |",
                "|---|---|---|---|",
            ]
            for r in reason_failures:
                expected = r.expected or "\u2014"
                actual = r.actual or "\u2014"
                lines.append(
                    "| \u274c "
                    f"`{_markdown_table_cell(r.check_name)}` | "
                    f"{_markdown_table_cell(expected)} | "
                    f"{_markdown_table_cell(actual)} | "
                    f"{_markdown_table_cell(r.message)} |"
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
            lines.append(
                "| \u2705 "
                f"`{_markdown_table_cell(r.check_name)}` | "
                f"{_markdown_table_cell(r.message)} |"
            )
        lines += ["", "</details>"]

    if ignored:
        lines += [
            "",
            "<details>",
            f"<summary>\u2796 {len(ignored)} ignored checks</summary>",
            "",
            "| Check |",
            "|---|",
        ]
        for r in ignored:
            lines.append(f"| \u2796 `{_markdown_table_cell(r.check_name)}` |")
        lines += ["", "</details>"]

    lines += [
        "",
        "### Next steps",
        "",
        *_markdown_next_steps(report, short_run=short_run, baseline_path=baseline_path),
    ]

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
