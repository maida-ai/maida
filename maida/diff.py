"""Structural diff engine for comparing two runs or a run against a baseline.

Used after ``maida assert`` flags a regression to understand *what* changed.
"""

from collections import Counter
from dataclasses import dataclass, field

from maida.baseline import extract_run_metrics
from maida.config import MaidaConfig, load_config
from maida.storage import load_run_for_analysis

from maida._report.diff import format_diff_markdown, format_diff_text


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
    summary = dict(baseline.get("summary") or {})
    final_status = baseline.get("final_status") or summary.get("status") or "unknown"
    summary["status"] = final_status
    return {
        "summary": summary,
        "tool_path": tool_path,
        "tool_call_sequence": tool_call_sequence,
        "_tool_call_sequence_exact": exact_tool_sequence,
        "_tool_call_counts_exact": isinstance(baseline.get("tool_call_counts"), dict),
        "tool_call_counts": baseline.get("tool_call_counts", {}),
        "llm_models_used": baseline.get("llm_models_used", []),
        "event_type_sequence": baseline.get("event_type_sequence", []),
        "guardrail_events": baseline.get("guardrail_events", []),
        "final_status": final_status,
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
    counts_a = _as_int_counter(metrics_a.get("tool_call_counts")) or Counter(current_tool_sequence)
    counts_b = _as_int_counter(metrics_b.get("tool_call_counts"))
    counts_b_exact = bool(metrics_b.get("_tool_call_counts_exact", True))
    repeated_tools = {
        tool: (counts_b.get(tool, 0), current_count)
        for tool, current_count in sorted(counts_a.items())
        if current_count > 1 and (tool not in tools_b or (counts_b_exact and current_count > counts_b.get(tool, 0)))
    }

    common_tools = {tool for tool in counts_a if counts_a[tool] and (counts_b.get(tool) or tool in tools_b)}
    exact_a = bool(metrics_a.get("_tool_call_sequence_exact"))
    exact_b = bool(metrics_b.get("_tool_call_sequence_exact"))
    current_common_sequence = [tool for tool in current_tool_sequence if tool in common_tools]
    baseline_common_sequence = [tool for tool in baseline_tool_sequence if tool in common_tools]
    reordered_tools = exact_a and exact_b and current_common_sequence != baseline_common_sequence

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


__all__ = [
    "RunDiff",
    "compute_diff",
    "format_diff_markdown",
    "format_diff_text",
]
