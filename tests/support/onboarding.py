"""Reusable test helpers."""


def v1_payload():
    stages = ("captured", "baseline-reviewed", "gate-pass", "regression-caught", "repair-pass", "ci-verified")
    attempt = {
        "id": "a" * 32,
        "started_at": "2026-09-28T12:00:00+00:00",
        "engine_version": "0.6.0",
        "task_kind": "coding-agent",
        "assistance": "none",
        "outcome": "activated",
        "events": [
            {"at": f"2026-09-28T12:0{i + 1}:00+00:00", "actor": "user", "milestone": stage}
            for i, stage in enumerate(stages)
        ],
        "activated_at": "2026-09-28T12:05:00+00:00",
        "activation_assistance": "none",
    }
    return {"journal_version": 1, "attempts": [attempt]}
