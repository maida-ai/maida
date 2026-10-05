"""Quiet, best-effort recording of mechanically provable funnel stages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from maida._onboarding.constants import FUNNEL
from maida._onboarding.utils import _append, _load, _locked, _now, _path, _save

if TYPE_CHECKING:
    from maida.config import MaidaConfig


def record_automatically(milestone: str, *, root: Path | None = None) -> None:
    """Best-effort and quiet; never start measurement or change product results."""
    if milestone not in FUNNEL[:4]:
        raise ValueError("Only mechanically provable funnel stages may be recorded automatically")
    try:
        path = _path(root)
        if not path.is_file():
            return
        with _locked(path):
            data = _load(path)
            if not data["attempts"] or data["attempts"][-1]["outcome"] in {"blocked", "abandoned"}:
                return
            if _append(data["attempts"][-1], {"at": _now(), "actor": "user", "milestone": milestone}):
                _save(path, data)
    except (OSError, ValueError):
        # Invalid/unwritable journals remain available for explicit inspection.
        # Measurement must never break a passive capture hook or a valid report.
        return


def record_completed_capture(config: MaidaConfig, session_hash: str, segment: str) -> None:
    """Only complete, repository-scoped task receipts establish an own task."""
    if config.project_root is None:
        return
    try:
        receipt = json.loads((config.data_dir / "onboarding" / f"{session_hash}-{segment}.json").read_text())
        if receipt["state"] == "closed" and receipt["has_start"] is True and receipt["complete_tools"] is True:
            record_automatically("own-task-captured", root=config.project_root)
    except (OSError, ValueError, KeyError, TypeError):
        return
