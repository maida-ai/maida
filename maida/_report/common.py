"""Shared Markdown helpers for gate and trial reports."""

from __future__ import annotations


def markdown_table_cell(value: object) -> str:
    """Escape a value for use inside a Markdown table cell."""
    return str(value).replace("\n", " ").replace("|", "\\|")


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
