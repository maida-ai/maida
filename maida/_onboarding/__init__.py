"""Local onboarding funnel measurement.

Private implementation behind ``maida.onboarding``. Journals attempts, milestones,
and effort on disk only; nothing is uploaded. Import the public API from
``maida.onboarding``. This ``__init__`` re-exports nothing so importing a leaf
submodule cannot pull the full package or create package-init cycles.
"""
