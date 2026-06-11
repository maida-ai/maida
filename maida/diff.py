"""Structural diff engine for comparing two runs or a run against a baseline.

Used after ``maida assert`` flags a regression to understand *what* changed.
"""

from collections import Counter
from dataclasses import dataclass, field

from maida.baseline import extract_run_metrics
from maida.config import MaidaConfig, load_config
from maida.events import spans_to_events
from maida.storage import load_run_meta, load_spans, resolve_trace_id


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
    model_changes: dict = field(default_factory=dict)


def _metrics_from_baseline(baseline: dict) -> dict:
    """Normalise a baseline dict into the same shape as ``extract_run_metrics``."""
    return {
        "summary": baseline.get("summary", {}),
        "tool_path": baseline.get("tool_path", []),
        "tool_call_counts": baseline.get("tool_call_counts", {}),
        "llm_models_used": baseline.get("llm_models_used", []),
        "event_type_sequence": baseline.get("event_type_sequence", []),
        "guardrail_events": baseline.get("guardrail_events", []),
        "final_status": baseline.get("final_status", ""),
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

    full_a = resolve_trace_id(run_a_id, config)
    meta_a = load_run_meta(full_a, config)
    spans_a = load_spans(full_a, config)
    events_a = spans_to_events(spans_a)
    metrics_a = extract_run_metrics(meta_a, events_a)

    if baseline is not None:
        metrics_b = _metrics_from_baseline(baseline)
        b_id = baseline.get("source_run_id", "baseline")
    elif run_b_id is not None:
        full_b = resolve_trace_id(run_b_id, config)
        meta_b = load_run_meta(full_b, config)
        spans_b = load_spans(full_b, config)
        events_b = spans_to_events(spans_b)
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
    tools_a = set(metrics_a["tool_path"])
    tools_b = set(metrics_b["tool_path"])
    tool_path_diff = {
        "added": sorted(tools_a - tools_b),
        "removed": sorted(tools_b - tools_a),
        "common": sorted(tools_a & tools_b),
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

    return RunDiff(
        run_a_id=full_a,
        run_b_id=b_id,
        summary_diff=summary_diff,
        tool_path_diff=tool_path_diff,
        event_count_diff=event_count_diff,
        new_tools=tool_path_diff["added"],
        removed_tools=tool_path_diff["removed"],
        model_changes=model_changes,
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


def format_diff_text(diff: RunDiff) -> str:
    """Format a ``RunDiff`` as human-readable text."""
    lines: list[str] = [f"Run comparison: {diff.run_a_id[:8]} vs {diff.run_b_id[:8]}"]

    if diff.summary_diff:
        lines.append("")
        lines.append("Summary:")
        for key, (va, vb) in sorted(diff.summary_diff.items()):
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                lines.append(f"  {key}: {vb} -> {va} ({_pct_change(va, vb)})")
            else:
                lines.append(f"  {key}: {vb} -> {va}")
    else:
        lines.append("")
        lines.append("Summary: identical")

    if diff.new_tools or diff.removed_tools:
        lines.append("")
        lines.append("Tool path changes:")
        for t in diff.new_tools:
            lines.append(f"  + {t} (new)")
        for t in diff.removed_tools:
            lines.append(f"  - {t} (removed)")

    if diff.event_count_diff:
        lines.append("")
        lines.append("Event type distribution:")
        for et, (ca, cb) in sorted(diff.event_count_diff.items()):
            if ca == cb:
                lines.append(f"  {et}: {cb} -> {ca}")
            else:
                lines.append(f"  {et}: {cb} -> {ca} ({_pct_change(ca, cb)})")

    return "\n".join(lines)


def format_diff_markdown(diff: RunDiff) -> str:
    """Format a ``RunDiff`` as a Markdown "What changed" section.

    Designed to be embedded in the assert report posted as a PR comment.
    Returns an empty string when there are no structural changes.
    """
    sections: list[str] = []

    if diff.summary_diff:
        rows = ["| Metric | Baseline | Current | Change |", "|---|---|---|---|"]
        for key, (va, vb) in sorted(diff.summary_diff.items()):
            if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
                rows.append(f"| {key} | {vb} | {va} | {_pct_change(va, vb)} |")
            else:
                rows.append(f"| {key} | {vb} | {va} | changed |")
        sections.append("\n".join(rows))

    tool_lines = [f"- ➕ `{t}` — new tool, not in baseline" for t in diff.new_tools]
    tool_lines += [f"- ➖ `{t}` — no longer called" for t in diff.removed_tools]
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
    return "### What changed vs baseline\n\n" + "\n\n".join(sections)
