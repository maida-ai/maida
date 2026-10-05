"""Human and machine-readable assertion report formatting."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from maida._assertions.types import AssertionReport, RegressionReasonCode
from maida.diff import format_diff_markdown, format_diff_text

if TYPE_CHECKING:
    from maida.diff import RunDiff

_PASS = "\u2713"  # ✓
_FAIL = "\u2717"  # ✗


def _reason_code_text(reason_code: RegressionReasonCode | str) -> str:
    if isinstance(reason_code, RegressionReasonCode):
        return reason_code.value
    return str(reason_code)


def markdown_table_cell(value: object) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return str(value).replace("\n", " ").replace("|", "\\|")


def _markdown_scope(report: AssertionReport) -> str:
    scope = f"run `{report.run_id[:8]}`"
    if report.baseline_run_id:
        scope += f" vs baseline `{str(report.baseline_run_id)[:8]}`"
    return scope


def markdown_baseline_provenance(acceptance: dict | None) -> list[str]:
    """Render baseline acceptance metadata as Markdown lines for PR comments."""
    if not isinstance(acceptance, dict):
        return []

    accepted_by = markdown_table_cell(acceptance.get("accepted_by") or "unknown")
    accepted_at = markdown_table_cell(acceptance.get("accepted_at") or "unknown")
    reason = markdown_table_cell(acceptance.get("reason") or "not recorded")
    source = acceptance.get("source")
    source = source if isinstance(source, dict) else {}
    pull_request = source.get("pull_request")
    pull_request = pull_request if isinstance(pull_request, dict) else {}
    pr_number = pull_request.get("number")
    pr_url = pull_request.get("url")

    if isinstance(pr_number, int) and isinstance(pr_url, str) and pr_url.startswith(("https://", "http://")):
        source_text = f"[PR #{pr_number}]({pr_url})"
    elif isinstance(pr_number, int):
        source_text = f"PR #{pr_number}"
    else:
        source_text = "local acceptance"

    commit_sha = source.get("commit_sha")
    if isinstance(commit_sha, str) and commit_sha:
        source_text += f" at `{markdown_table_cell(commit_sha[:8])}`"

    verdict = acceptance.get("verdict")
    verdict = verdict if isinstance(verdict, dict) else {}
    outcome = markdown_table_cell(verdict.get("outcome") or "accepted")
    summary = markdown_table_cell(verdict.get("summary") or "not recorded")

    return [
        "",
        "### Baseline provenance",
        "",
        "| Accepted by | Accepted at | Source |",
        "|---|---|---|",
        f"| `{accepted_by}` | `{accepted_at}` | {source_text} |",
        "",
        f"**Acceptance verdict:** {outcome} -- {summary}",
        "",
        f"**Reason:** {reason}",
    ]


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
        steps.append(f"- Inspect the full diff: `maida diff {short_run} --baseline {baseline_path}`")
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
        steps.append("- Review and commit the baseline diff; otherwise fix the agent behavior and rerun the gate.")
    else:
        steps.append("- If this is expected, update the policy; otherwise fix the agent behavior and rerun the gate.")
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
            lines.append(f"  {mark} {r.check_name} [{_reason_code_text(r.reason_code)}]: {r.message}")
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
        lines.append(f"**No checks enabled** | {scope}")
    elif failed:
        lines.append(f"**{len(failed)} of {len(report.results)} checks failed** | {scope}")
    elif active := len(report.results) - len(ignored):
        parts = [f"**All {active} checks passed** | {scope}"]
        if ignored:
            parts.append(f"({len(ignored)} ignored)")
        lines.append(" ".join(parts))
    else:
        parts = [f"**All checks ignored** | {scope}"]
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
                expected = r.expected or "--"
                actual = r.actual or "--"
                lines.append(
                    "| \u274c "
                    f"`{markdown_table_cell(r.check_name)}` | "
                    f"{markdown_table_cell(expected)} | "
                    f"{markdown_table_cell(actual)} | "
                    f"{markdown_table_cell(r.message)} |"
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
            lines.append(f"| \u2705 `{markdown_table_cell(r.check_name)}` | {markdown_table_cell(r.message)} |")
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
            lines.append(f"| \u2796 `{markdown_table_cell(r.check_name)}` |")
        lines += ["", "</details>"]

    lines += markdown_baseline_provenance(report.baseline_acceptance)

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
        "*Gated by [Maida](https://maida.ai) -- the local-first behavioral regression gate for AI agents.*",
    ]
    return "\n".join(lines)
