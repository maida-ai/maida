"""Text and Markdown formatting for structural run diffs."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from maida.diff import RunDiff


def _pct_change(a: int | float, b: int | float) -> str:
    """Human-readable percentage change string."""
    if b == 0:
        return "NEW" if a else "unchanged"
    delta = ((a - b) / b) * 100
    if delta == 0:
        return "unchanged"
    return f"{delta:+.0f}%"


_SUMMARY_LABELS = {"total_events": "step_count"}
_SUMMARY_ORDER = [
    "total_events",
    "tool_calls",
    "total_tokens",
    "duration_ms",
    "llm_calls",
    "errors",
    "loop_warnings",
    "status",
]
_TOOL_PATH_PREVIEW_SIDE = 6
_PRIMARY_BEHAVIOR_ORDER = [
    "total_events",
    "tool_path",
    "loop_warnings",
    "guardrail_events",
    "status",
    "duration_ms",
    "total_tokens",
]
_PRIMARY_BEHAVIOR_LABELS = {
    "total_events": "Steps",
    "tool_path": "Tool path",
    "loop_warnings": "Loops/cycles",
    "guardrail_events": "Guardrail events",
    "status": "Terminal state",
    "duration_ms": "Latency envelope",
    "total_tokens": "Cost envelope",
    "tool_calls": "Tool calls",
    "llm_calls": "LLM calls",
    "errors": "Errors",
}


def _summary_keys(summary_diff: dict) -> list[str]:
    ordered = [key for key in _SUMMARY_ORDER if key in summary_diff]
    ordered += sorted(key for key in summary_diff if key not in _SUMMARY_ORDER)
    return ordered


def _summary_label(key: str) -> str:
    return _SUMMARY_LABELS.get(key, key)


def _format_tool_sequence(sequence: list[str]) -> str:
    preview_limit = _TOOL_PATH_PREVIEW_SIDE * 2
    if len(sequence) <= preview_limit:
        return " -> ".join(sequence) if sequence else "(none)"
    head = sequence[:_TOOL_PATH_PREVIEW_SIDE]
    tail = sequence[-_TOOL_PATH_PREVIEW_SIDE:]
    hidden = len(sequence) - len(head) - len(tail)
    return " -> ".join(head + [f"... ({hidden} more) ..."] + tail)


def _has_tool_path_changes(diff: RunDiff) -> bool:
    exact_sequences = bool(
        diff.tool_path_diff.get("current_sequence_exact") and diff.tool_path_diff.get("baseline_sequence_exact")
    )
    return bool(
        diff.new_tools
        or diff.removed_tools
        or diff.repeated_tools
        or diff.reordered_tools
        or (exact_sequences and diff.current_tool_sequence != diff.baseline_tool_sequence)
    )


def _table_cell(value: object) -> str:
    return str(value).replace("\n", " ").replace("|", "\\|")


def _format_behavior_value(key: str, value: object) -> str:
    if key == "duration_ms" and isinstance(value, (int, float)):
        return f"{int(value)} ms"
    if key == "total_tokens" and isinstance(value, (int, float)):
        return f"{int(value)} tokens"
    return str(value)


def _tool_path_change_summary(diff: RunDiff) -> str:
    parts: list[str] = []
    if diff.new_tools:
        parts.append(f"{len(diff.new_tools)} new")
    if diff.removed_tools:
        parts.append(f"{len(diff.removed_tools)} removed")
    if diff.repeated_tools:
        parts.append("repeated calls")
    if diff.reordered_tools:
        parts.append("order changed")
    if not parts:
        parts.append("sequence changed")
    return "; ".join(parts)


def _model_change_summary(diff: RunDiff) -> tuple[str, str, str] | None:
    added = diff.model_changes.get("added", [])
    removed = diff.model_changes.get("removed", [])
    if not added and not removed:
        return None
    baseline = ", ".join(removed) if removed else "(unchanged)"
    current = ", ".join(added) if added else "(unchanged)"
    parts: list[str] = []
    if added:
        parts.append(f"{len(added)} added")
    if removed:
        parts.append(f"{len(removed)} removed")
    return ("Models", baseline, current, "; ".join(parts))


def _behavior_change_rows(diff: RunDiff) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    emitted: set[str] = set()

    def add_summary_row(key: str) -> None:
        if key not in diff.summary_diff:
            return
        current, baseline = diff.summary_diff[key]
        label = _PRIMARY_BEHAVIOR_LABELS.get(key, _summary_label(key))
        if isinstance(current, (int, float)) and isinstance(baseline, (int, float)):
            change = _pct_change(current, baseline)
        else:
            change = "changed"
        rows.append(
            (
                label,
                _format_behavior_value(key, baseline),
                _format_behavior_value(key, current),
                change,
            )
        )
        emitted.add(key)

    for key in _PRIMARY_BEHAVIOR_ORDER:
        if key == "tool_path":
            if _has_tool_path_changes(diff):
                rows.append(
                    (
                        "Tool path",
                        _format_tool_sequence(diff.baseline_tool_sequence),
                        _format_tool_sequence(diff.current_tool_sequence),
                        _tool_path_change_summary(diff),
                    )
                )
                emitted.add(key)
            continue
        if key == "guardrail_events":
            if diff.guardrail_event_diff is not None:
                current, baseline = diff.guardrail_event_diff
                rows.append(
                    (
                        "Guardrail events",
                        str(baseline),
                        str(current),
                        _pct_change(current, baseline),
                    )
                )
                emitted.add(key)
            continue
        if key == "status":
            if diff.terminal_status_diff is not None:
                current, baseline = diff.terminal_status_diff
                rows.append(("Terminal state", baseline, current, "changed"))
                emitted.add(key)
            continue
        add_summary_row(key)

    model_row = _model_change_summary(diff)
    if model_row is not None:
        rows.append(model_row)

    for key in _summary_keys(diff.summary_diff):
        if key in emitted:
            continue
        add_summary_row(key)

    return rows


def format_diff_text(diff: RunDiff) -> str:
    """Format a ``RunDiff`` as human-readable text."""
    lines: list[str] = [f"Run comparison: {diff.run_a_id[:8]} vs {diff.run_b_id[:8]}"]

    if diff.summary_diff:
        lines.append("")
        lines.append("Summary:")
        for key in _summary_keys(diff.summary_diff):
            va, vb = diff.summary_diff[key]
            label = _summary_label(key)
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                lines.append(f"  {label}: {vb} -> {va} ({_pct_change(va, vb)})")
            else:
                lines.append(f"  {label}: {vb} -> {va}")
    else:
        lines.append("")
        lines.append("Summary: identical")

    if _has_tool_path_changes(diff):
        lines.append("")
        lines.append("Tool path:")
        lines.append(f"  baseline: {_format_tool_sequence(diff.baseline_tool_sequence)}")
        lines.append(f"  current: {_format_tool_sequence(diff.current_tool_sequence)}")
        lines.append("")
        lines.append("Tool call changes:")
        for t in diff.new_tools:
            lines.append(f"  + {t} (new)")
        for t in diff.removed_tools:
            lines.append(f"  - {t} (removed)")
        for tool, (baseline_count, current_count) in diff.repeated_tools.items():
            lines.append(f"  ~ {tool} repeated: {baseline_count} -> {current_count} calls")
        if diff.reordered_tools:
            lines.append("  ! order changed for shared tool calls")

    if diff.event_count_diff:
        lines.append("")
        lines.append("Event type distribution:")
        for et, (ca, cb) in sorted(diff.event_count_diff.items()):
            if ca == cb:
                lines.append(f"  {et}: {cb} -> {ca}")
            else:
                lines.append(f"  {et}: {cb} -> {ca} ({_pct_change(ca, cb)})")

    if diff.guardrail_event_diff is not None:
        current, baseline = diff.guardrail_event_diff
        lines.append("")
        lines.append(f"Guardrail events: {baseline} -> {current} ({_pct_change(current, baseline)})")

    if diff.terminal_status_diff is not None and "status" not in diff.summary_diff:
        current, baseline = diff.terminal_status_diff
        lines.append("")
        lines.append(f"Terminal state: {baseline} -> {current}")

    return "\n".join(lines)


def format_diff_markdown(diff: RunDiff) -> str:
    """Format a ``RunDiff`` as a Markdown "What changed" section.

    Designed to be embedded in the assert report posted as a PR comment.
    Returns an empty string when there are no structural changes.
    """
    sections: list[str] = []

    behavior_rows = _behavior_change_rows(diff)
    if behavior_rows:
        rows = ["| Behavior | Baseline | Current | Change |", "|---|---|---|---|"]
        for behavior, baseline, current, change in behavior_rows:
            rows.append(
                "| "
                f"{_table_cell(behavior)} | "
                f"{_table_cell(baseline)} | "
                f"{_table_cell(current)} | "
                f"{_table_cell(change)} |"
            )
        sections.append("\n".join(rows))

    if _has_tool_path_changes(diff):
        sections.append(
            "**Tool path:**\n"
            f"- Baseline: `{_format_tool_sequence(diff.baseline_tool_sequence)}`\n"
            f"- Current: `{_format_tool_sequence(diff.current_tool_sequence)}`"
        )

    tool_lines = [f"- ➕ `{t}` -- new tool, not in baseline" for t in diff.new_tools]
    tool_lines += [f"- ➖ `{t}` -- no longer called" for t in diff.removed_tools]
    tool_lines += [
        f"- 🔁 `{tool}` -- repeated {baseline_count} -> {current_count} calls"
        for tool, (baseline_count, current_count) in diff.repeated_tools.items()
    ]
    if diff.reordered_tools:
        tool_lines.append("- 🔀 Tool order changed for shared calls")
    if tool_lines:
        sections.append("**Tool changes:**\n" + "\n".join(tool_lines))

    model_added = diff.model_changes.get("added", [])
    model_removed = diff.model_changes.get("removed", [])
    model_lines = [f"- ➕ `{m}`" for m in model_added]
    model_lines += [f"- ➖ `{m}`" for m in model_removed]
    if model_lines:
        sections.append("**Model changes:**\n" + "\n".join(model_lines))

    if not sections:
        return ""
    return "### Top behavior changes\n\n" + "\n\n".join(sections)
