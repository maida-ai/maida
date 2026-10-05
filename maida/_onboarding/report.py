"""Aggregate local onboarding journal counts into a report summary."""

from __future__ import annotations

from maida._onboarding.constants import ACTORS, FUNNEL, PHASES
from maida._onboarding.utils import _elapsed, _stages, _timestamp


def summarize(data: dict) -> dict:
    attempts = data["attempts"]
    activated = [a for a in attempts if a.get("activated_at")]
    effort = {actor: dict.fromkeys(PHASES, 0.0) for actor in ACTORS}
    for attempt in attempts:
        for event in attempt["events"]:
            if "phase" in event:
                effort[event["actor"]][event["phase"]] += event["minutes"]
    occurrences = [_stages(a) for a in attempts]
    funnel = []
    for index, stage in enumerate(FUNNEL):
        reached = [s[stage] for s in occurrences if stage in s]
        previous = FUNNEL[index - 1] if index else None
        eligible = [s for s in occurrences if previous in s] if previous else occurrences
        converted = sum(
            stage in s and (previous is None or _timestamp(s[stage]["at"]) >= _timestamp(s[previous]["at"]))
            for s in eligible
        )
        funnel.append(
            {
                "stage": stage,
                "reached": len(reached),
                "percentage_of_attempts": 100 * len(reached) / len(attempts) if attempts else None,
                "conversion_from_previous": converted / len(eligible) if eligible else None,
                "elapsed_seconds": [_elapsed(a, s[stage]["at"]) for a, s in zip(attempts, occurrences) if stage in s],
                "unassisted": sum(s["assistance"] == "none" for s in reached),
                "assisted": sum(s["assistance"] == "assisted" for s in reached),
                "unknown_assistance": sum(s["assistance"] == "unknown" for s in reached),
            }
        )
    elapsed = [_elapsed(a, a["activated_at"]) for a in activated]
    return {
        "measurement_version": 2,
        "evidence": "local command observations and explicit self-reported milestones; not evidence of understanding",
        "activation_stage": "first-report",
        "time_to_first_report_seconds": elapsed,
        "funnel": funnel,
        "engine_versions": sorted({a["engine_version"] for a in attempts}),
        "task_kinds": {
            kind: sum((a["task_kind"] or "unknown") == kind for a in attempts)
            for kind in sorted({a["task_kind"] or "unknown" for a in attempts})
        },
        "attempts": len(attempts),
        "activated": len(activated),
        "activation_rate": len(activated) / len(attempts) if attempts else None,
        "unassisted": sum(a["activation_assistance"] == "none" for a in activated),
        "assisted": sum(a["activation_assistance"] == "assisted" for a in activated),
        "unknown_assistance": sum(a["activation_assistance"] == "unknown" for a in activated),
        "blocked": sum(a["outcome"] == "blocked" for a in attempts),
        "abandoned": sum(a["outcome"] == "abandoned" for a in attempts),
        "in_progress": sum(a["outcome"] == "in-progress" for a in attempts),
        "time_to_activation_seconds": elapsed,
        "effort_minutes": effort,
        "local_gate_verified": funnel[4]["reached"],
        "pr_gate_verified": funnel[5]["reached"],
        "ci_verified": funnel[5]["reached"],
        "setup_without_first_report": sum("setup-ready" in s and "first-report" not in s for s in occurrences),
        "demo_seen": sum(any(e.get("milestone") == "demo-seen" for e in a["events"]) for a in attempts),
    }
