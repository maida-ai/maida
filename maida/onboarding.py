"""Explicit, local funnel measurement; never uploaded or implicitly started."""

from maida._onboarding.app import app
from maida._onboarding.recording import record_automatically, record_completed_capture
from maida._onboarding.report import summarize

__all__ = [
    "app",
    "record_automatically",
    "record_completed_capture",
    "summarize",
]
