"""Structural diff engine for comparing two runs or a run against a baseline.

Used after ``maida assert`` flags a regression to understand *what* changed.
"""

from collections import Counter
from dataclasses import dataclass, field

from maida.baseline import extract_run_metrics
from maida.config import MaidaConfig, load_config
from maida.storage import load_run_for_analysis


@dataclass
class RunDiff:
    """Structural comparison between two runs (or a run and a baseline)."""

    run_a_id: str
    run_b_id: str
    summary_diff: dict = field(default_factory=dict)
    tool_path_diff: dict = field(default_factory=dict)
    event_count_diff: dict = field(default_factory=dict)
    new_tools: list[str] = field(default_factory=list)
    removed_tools: list[str] = field(default_factory=list)
    repeated_tools: dict[str, tuple[int, int]] = field(default_factory=dict)
    reordered_tools: bool = False
    current_tool_sequence: list[str] = field(default_factory=list)
    baseline_tool_sequence: list[str] = field(default_factory=list)
    model_changes: dict = field(default_factory=dict)
    guardrail_event_diff: tuple[int, int] | None = None
    terminal_status_diff: tuple[str, str] | None = None


def _as_string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _as_int_counter(value: object) -> Counter[str]:
    counter: Counter[str] = Counter()
    if not isinstance(value, dict):
        return counter
    for key, count in value.items():
        if isinstance(key, str) and isinstance(count, int) and count >= 0:
            counter[key] = count
    return counter


def _metrics_from_baseline(baseline: dict) -> dict:
    """Normalise a baseline dict into the same shape as `extract_run_metrics`."""
    tool_path = _as_string_list(baseline.get("tool_path"))
    tool_call_sequence = _as_string_list(baseline.get("tool_call_sequence"))
    exact_tool_sequence = isinstance(baseline.get("tool_call_sequence"), list)
    if not tool_call_sequence:
        tool_call_sequence = tool_path
    return {
        "summary": baseline.get("summary", {}),
        "tool_path": tool_path,
        "tool_call_sequence": tool_call_sequence,
        "_tool_call_sequence_exact": exact_tool_sequence,
        "_tool_call_counts_exact": isinstance(baseline.get("tool_call_counts"), dict),
        "tool_call_counts": baseline.get("tool_call_counts", {}),
        "llm_models_used": baseline.get("llm_models_used", []),
        "event_type_sequence": baseline.get("event_type_sequence", []),
        "guardrail_events": baseline.get("guardrail_events", []),
        "final_status": baseline.get("final_status", "unknown"),
    }


def compute_diff(
    run_a_id: str,
    run_b_id: str | None = None,
    baseline: dict | None = None,
    config: MaidaConfig | None = None,
) -> RunDiff:
    """Compute a structural diff between two runs or a run and a baseline.

    Exactly one of *run_b_id* or *baseline* must be provided.
    """
    if config is None:
        config = load_config()

    full_a, meta_a, events_a = load_run_for_analysis(run_a_id, config)
    metrics_a = extract_run_metrics(meta_a, events_a)

    if baseline is not None:
        metrics_b = _metrics_from_baseline(baseline)
        b_id = baseline.get("source_run_id", "baseline")
    elif run_b_id is not None:
        full_b, meta_b, events_b = load_run_for_analysis(run_b_id, config)
        metrics_b = extract_run_metrics(meta_b, events_b)
        b_id = full_b
    else:
        raise ValueError("Either run_b_id or baseline must be provided")

    # --- summary diff ---
    summary_diff: dict = {}
    sum_a = metrics_a["summary"]
    sum_b = metrics_b["summary"]
    for key in sum_a:
        va = sum_a.get(key)
        vb = sum_b.get(key)
        if va != vb:
            summary_diff[key] = (va, vb)

    # --- tool path diff ---
    tools_a = set(_as_string_list(metrics_a.get("tool_path")))
    tools_b = set(_as_string_list(metrics_b.get("tool_path")))
    new_tools = sorted(tools_a - tools_b)
    removed_tools = sorted(tools_b - tools_a)

    current_tool_sequence = _as_string_list(metrics_a.get("tool_call_sequence"))
    baseline_tool_sequence = _as_string_list(metrics_b.get("tool_call_sequence"))
    counts_a = _as_int_counter(metrics_a.get("tool_call_counts")) or Counter(
        current_tool_sequence
    )
    counts_b = _as_int_counter(metrics_b.get("tool_call_counts"))
    counts_b_exact = bool(metrics_b.get("_tool_call_counts_exact", True))
    repeated_tools = {
        tool: (counts_b.get(tool, 0), current_count)
        for tool, current_count in sorted(counts_a.items())
        if current_count > 1
        and (
            tool not in tools_b
            or (counts_b_exact and current_count > counts_b.get(tool, 0))
        )
    }

    common_tools = {
        tool
        for tool in counts_a
        if counts_a[tool] and (counts_b.get(tool) or tool in tools_b)
    }
    exact_a = bool(metrics_a.get("_tool_call_sequence_exact"))
    exact_b = bool(metrics_b.get("_tool_call_sequence_exact"))
    current_common_sequence = [
        tool for tool in current_tool_sequence if tool in common_tools
    ]
    baseline_common_sequence = [
        tool for tool in baseline_tool_sequence if tool in common_tools
    ]
    reordered_tools = (
        exact_a and exact_b and current_common_sequence != baseline_common_sequence
    )

    tool_path_diff = {
        "new": new_tools,
        "removed": removed_tools,
        "repeated": repeated_tools,
        "reordered": reordered_tools,
        "current_sequence_exact": exact_a,
        "baseline_sequence_exact": exact_b,
    }
    # --- event count diff ---
    seq_a = Counter(metrics_a["event_type_sequence"])
    seq_b = Counter(metrics_b["event_type_sequence"])
    all_types = sorted(set(seq_a) | set(seq_b))
    event_count_diff = {t: (seq_a.get(t, 0), seq_b.get(t, 0)) for t in all_types}

    # --- model changes ---
    models_a = set(metrics_a["llm_models_used"])
    models_b = set(metrics_b["llm_models_used"])
    model_changes = {
        "added": sorted(models_a - models_b),
        "removed": sorted(models_b - models_a),
    }

    # --- guardrail event + terminal status changes ---
    guardrails_a = metrics_a.get("guardrail_events") or []
    guardrails_b = metrics_b.get("guardrail_events") or []
    guardrail_event_diff = None
    if len(guardrails_a) != len(guardrails_b):
        guardrail_event_diff = (len(guardrails_a), len(guardrails_b))

    status_a = str(metrics_a.get("final_status") or sum_a.get("status") or "unknown")
    status_b = str(metrics_b.get("final_status") or sum_b.get("status") or "unknown")
    terminal_status_diff = (status_a, status_b) if status_a != status_b else None

    return RunDiff(
        run_a_id=full_a,
        run_b_id=b_id,
        summary_diff=summary_diff,
        tool_path_diff=tool_path_diff,
        event_count_diff=event_count_diff,
        new_tools=tool_path_diff["new"],
        removed_tools=tool_path_diff["removed"],
        repeated_tools=repeated_tools,
        reordered_tools=reordered_tools,
        current_tool_sequence=current_tool_sequence,
        baseline_tool_sequence=baseline_tool_sequence,
        model_changes=model_changes,
        guardrail_event_diff=guardrail_event_diff,
        terminal_status_diff=terminal_status_diff,
    )


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


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
        diff.tool_path_diff.get("current_sequence_exact")
        and diff.tool_path_diff.get("baseline_sequence_exact")
    )
    return bool(
        diff.new_tools
        or diff.removed_tools
        or diff.repeated_tools
        or diff.reordered_tools
        or (
            exact_sequences
            and diff.current_tool_sequence != diff.baseline_tool_sequence
        )
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
        lines.append(
            f"  baseline: {_format_tool_sequence(diff.baseline_tool_sequence)}"
        )
        lines.append(f"  current: {_format_tool_sequence(diff.current_tool_sequence)}")
        lines.append("")
        lines.append("Tool call changes:")
        for t in diff.new_tools:
            lines.append(f"  + {t} (new)")
        for t in diff.removed_tools:
            lines.append(f"  - {t} (removed)")
        for tool, (baseline_count, current_count) in diff.repeated_tools.items():
            lines.append(
                f"  ~ {tool} repeated: {baseline_count} -> {current_count} calls"
            )
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
        lines.append(
            f"Guardrail events: {baseline} -> {current} "
            f"({_pct_change(current, baseline)})"
        )

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

    tool_lines = [f"- ➕ `{t}` — new tool, not in baseline" for t in diff.new_tools]
    tool_lines += [f"- ➖ `{t}` — no longer called" for t in diff.removed_tools]
    tool_lines += [
        f"- 🔁 `{tool}` — repeated {baseline_count} -> {current_count} calls"
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
