"""Baseline acceptance helpers for intentional behavior changes."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from maida.baseline import create_baseline, save_baseline
from maida.config import MaidaConfig
from maida.diff import RunDiff, compute_diff
from maida.events import utc_now_iso_ms_z

_STRUCTURAL_BASELINE_KEYS = (
    "schema_version",
    "summary",
    "tool_path",
    "tool_call_sequence",
    "_tool_call_sequence_exact",
    "tool_call_counts",
    "llm_models_used",
    "event_type_sequence",
    "guardrail_events",
    "final_status",
)


@dataclass(frozen=True)
class BaselineAcceptResult:
    """Result of accepting a run into an existing baseline."""

    updated: bool
    baseline_path: Path
    source_run_id: str
    previous_source_run_id: str | None
    previous_baseline_sha256: str
    diff: RunDiff


@dataclass(frozen=True)
class AcceptanceSource:
    """Identity and source revision for an accepted baseline change."""

    accepted_by: str
    repository: str | None = None
    pull_request: int | None = None
    pull_request_url: str | None = None
    commit_sha: str | None = None

    def as_dict(self) -> dict:
        pull_request = None
        if self.pull_request is not None:
            pull_request = {
                "number": self.pull_request,
                "url": self.pull_request_url,
            }
        return {
            "repository": self.repository,
            "pull_request": pull_request,
            "commit_sha": self.commit_sha,
        }


def _baseline_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _structural_snapshot(baseline: dict) -> dict:
    return {key: baseline.get(key) for key in _STRUCTURAL_BASELINE_KEYS}


def baseline_matches_run(existing_baseline: dict, new_baseline: dict) -> bool:
    """Return true when two baselines describe the same structural behavior.

    TODO: This assumes that all the values are strictly ordered and comparable.
          Tracked in #130
    """

    return _structural_snapshot(existing_baseline) == _structural_snapshot(new_baseline)


def _accepted_run_verdict(baseline: dict) -> dict[str, str]:
    summary = baseline.get("summary") or {}
    return {
        "outcome": "accepted",
        "summary": (
            f"Accepted run status {baseline.get('final_status') or 'unknown'}: "
            f"{summary.get('total_events', 0)} events, "
            f"{summary.get('tool_calls', 0)} tool calls, "
            f"{summary.get('errors', 0)} errors, "
            f"{summary.get('loop_warnings', 0)} loop warnings."
        ),
    }


def accept_baseline_update(
    *,
    run_id: str,
    baseline_path: Path,
    existing_baseline: dict,
    reason: str,
    maida_version: str,
    config: MaidaConfig,
    source: AcceptanceSource | None = None,
) -> BaselineAcceptResult:
    """Update *baseline_path* from *run_id* and attach acceptance metadata.

    The existing baseline is compared against the freshly created run baseline.
    If there is no structural change, the file is left untouched so review diffs
    stay meaningful.
    """

    previous_hash = _baseline_sha256(baseline_path)
    new_baseline = create_baseline(run_id, config)
    diff = compute_diff(run_id, baseline=existing_baseline, config=config)
    previous_source_run_id = existing_baseline.get("source_run_id")

    if baseline_matches_run(existing_baseline, new_baseline):
        return BaselineAcceptResult(
            updated=False,
            baseline_path=baseline_path,
            source_run_id=new_baseline["source_run_id"],
            previous_source_run_id=previous_source_run_id,
            previous_baseline_sha256=previous_hash,
            diff=diff,
        )

    source = source or AcceptanceSource(accepted_by="unknown")
    new_baseline["acceptance"] = {
        "accepted_at": utc_now_iso_ms_z(),
        "accepted_by": source.accepted_by,
        "reason": reason,
        "maida_version": maida_version,
        "source_run_id": new_baseline["source_run_id"],
        "source": source.as_dict(),
        "verdict": _accepted_run_verdict(new_baseline),
        "previous_baseline": {
            "path": str(baseline_path),
            "source_run_id": previous_source_run_id,
            "created_at": existing_baseline.get("created_at"),
            "schema_version": existing_baseline.get("schema_version"),
            "sha256": previous_hash,
        },
    }
    save_baseline(new_baseline, baseline_path)

    return BaselineAcceptResult(
        updated=True,
        baseline_path=baseline_path,
        source_run_id=new_baseline["source_run_id"],
        previous_source_run_id=previous_source_run_id,
        previous_baseline_sha256=previous_hash,
        diff=diff,
    )
