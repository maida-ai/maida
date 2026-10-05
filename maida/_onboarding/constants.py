"""Shared vocabulary for local onboarding measurement."""

PHASES = ("setup", "trial", "investigation", "maintenance")
ACTORS = ("user", "founder", "other")
ASSISTANCE = ("none", "founder", "other", "unknown")
FUNNEL = (
    "setup-ready",
    "own-task-captured",
    "first-report",
    "gate-configured",
    "local-gate-verified",
    "pr-gate-verified",
)
VERIFICATION_STEPS = ("gate-pass", "regression-caught", "repair-pass")
MILESTONES = (*FUNNEL, "demo-seen", *VERIFICATION_STEPS)
TASK_KINDS = ("coding-agent", "python-agent", "other")
