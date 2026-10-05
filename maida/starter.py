"""Small, explicitly reviewed invariant starters from selected observations."""

from __future__ import annotations

import hashlib
import getpass
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from maida.baseline import extract_run_metrics, load_baseline
from maida.baseline_sample import create_baseline_from_report
from maida.config import MaidaConfig
from maida.gate import invariant_outcomes, numeric_metrics, structural_signature
from maida.policy import load_policy
from maida.policy_types import MetricKind
from maida.schema_versions import REPORT_SCHEMA_VERSION
from maida.storage import load_run_for_analysis, resolve_latest_run_id


STARTER_DIR = Path(".maida/starter")
STARTER_POLICY = STARTER_DIR / "policy.yaml"
STARTER_BASELINE = STARTER_DIR / "baseline.json"
STARTER_REVIEW = STARTER_DIR / "review.json"
ACTIVE_POLICY = Path(".maida/policy.yaml")
ACTIVE_BASELINE = Path(".maida/baselines/agent.json")


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def validate_targets(targets: dict[Path, str], *, force: bool, update: set[Path]) -> None:
    """Reject collisions and escaping/symlinked output before any writes."""
    root = Path.cwd().resolve()
    for path in targets:
        if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError(f"Refusing symlink or output outside this repository: {path}")
        if path.exists() and ((not force and path not in update) or not path.is_file()):
            raise ValueError(f"File already exists: {path}; review it or use --force to replace it")


def write_files(targets: dict[Path, str], *, force: bool = False, update: set[Path] | None = None) -> None:
    """Preflight the whole scaffold and roll back file contents on write failure."""
    validate_targets(targets, force=force, update=update or set())
    previous = {path: path.read_bytes() if path.exists() else None for path in targets}
    written = []
    try:
        for path, content in targets.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
                    temporary = Path(stream.name)
                    stream.write(content.encode("utf-8"))
                    stream.flush()
                # Windows cannot rename or unlink a temporary file while open.
                os.replace(temporary, path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            written.append(path)
    except Exception:
        for path in reversed(written):
            if previous[path] is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(previous[path])
        raise


def draft_starter(run_ids: list[str], config: MaidaConfig) -> dict[Path, str]:
    """Propose only observed terminal, loop and guardrail invariants."""
    evidence = []
    for run_id in run_ids:
        selected = resolve_latest_run_id(config) if run_id == "latest" else run_id
        trace_id, meta, events = load_run_for_analysis(selected, config)
        if meta.get("status") != "ok" or not meta.get("ended_at"):
            raise ValueError("A starter needs completed successful observations; inspect this run with maida view")
        if not isinstance(meta.get("run_name"), str) or not meta["run_name"].strip():
            raise ValueError("Each observation needs a workflow run_name before creating a starter")
        evidence.append((trace_id, meta, extract_run_metrics(meta, events)))
    if len({meta["run_name"] for _, meta, _ in evidence}) != 1:
        raise ValueError("Select observations of the same workflow; create separate starters for other workflows")
    trace_ids = [trace_id for trace_id, _, _ in evidence]
    if len(set(trace_ids)) != len(trace_ids):
        raise ValueError("Select each observation once; duplicate runs are not independent evidence")

    candidates = {"stop_condition_reached": {"kind": "invariant", "require": True}}
    if all(metrics["summary"]["loop_warnings"] == 0 for _, _, metrics in evidence):
        candidates["no_loops"] = {"kind": "invariant", "require": True}
    if all(not metrics["guardrail_events"] for _, _, metrics in evidence):
        candidates["no_guardrails"] = {"kind": "invariant", "require": True}
    policy_text = (
        "# CANDIDATES: review every rule before maida init --reviewed --reason '...'.\n"
        f"# Observed in {len(evidence)} selected run(s); this is not a guarantee of future behavior.\n"
        "# Delete rules that are not part of your task's contract.\n"
        "# Checks cover observed completion, loop warnings and guardrail events only.\n"
        "# Answer correctness, unobserved actions and population pass rates are outside scope.\n"
        + yaml.safe_dump(
            {"version": 2, "trials": len(evidence), "fail_fast": False, "metrics": candidates}, sort_keys=False
        )
    )
    report = {
        "report_version": REPORT_SCHEMA_VERSION,
        "metadata": {"trials_used": len(evidence), "trials_budgeted": len(evidence), "environment_fingerprint": None},
        "trials": [
            {
                "trace_id": trace_id,
                "run_name": meta["run_name"],
                "metric_values": numeric_metrics(metrics),
                "invariant_outcomes": {name: True for name in candidates},
                "structural_signature": structural_signature(metrics),
            }
            for trace_id, meta, metrics in evidence
        ],
    }
    baseline_text = _json(create_baseline_from_report(report))
    review = {
        "starter_version": 1,
        "review_required": True,
        "run_name": evidence[0][1]["run_name"],
        "source_trace_ids": trace_ids,
        "observations": len(evidence),
        "baseline_sha256": _digest(baseline_text),
        "candidates": list(candidates),
        "coverage": "Selected completed observations only; no correctness or population guarantee.",
    }
    return {STARTER_POLICY: policy_text, STARTER_BASELINE: baseline_text, STARTER_REVIEW: _json(review)}


def reviewed_starter(reason: str, config: MaidaConfig) -> tuple[dict[Path, str], dict]:
    """Validate the user's edited candidates before an explicit activation."""
    if not reason or not reason.strip():
        raise ValueError("Review each candidate first, then pass --reviewed --reason 'why these rules fit your task'")
    review = json.loads(STARTER_REVIEW.read_text(encoding="utf-8"))
    if review.get("starter_version") != 1 or not review.get("source_trace_ids"):
        raise ValueError("Invalid starter review record; regenerate with maida init --from-run RUN_ID")
    baseline_text = STARTER_BASELINE.read_text(encoding="utf-8")
    if _digest(baseline_text) != review.get("baseline_sha256"):
        raise ValueError("Starter baseline changed; regenerate from the selected observations before review")
    baseline = load_baseline(STARTER_BASELINE)
    policy = load_policy(STARTER_POLICY)
    if not policy.metrics or any(metric.kind is not MetricKind.INVARIANT for metric in policy.metrics.values()):
        raise ValueError(
            "The starter must contain at least one reviewed invariant; advanced policies can be added later"
        )
    for trace_id in review["source_trace_ids"]:
        _, meta, events = load_run_for_analysis(trace_id, config)
        if meta.get("status") != "ok" or meta.get("run_name") != review.get("run_name"):
            raise ValueError("Source observation changed; regenerate the starter before review")
        outcomes = invariant_outcomes(extract_run_metrics(meta, events), policy, baseline)
        if outcomes.keys() != policy.metrics.keys():
            raise ValueError("Edited policy requires observation semantics unavailable in these runtime traces")
        if not all(outcomes.values()):
            raise ValueError("The edited policy fails an observed run; review that run or revise the candidate")
    policy_text = STARTER_POLICY.read_text(encoding="utf-8")
    review.update(
        {
            "review_required": False,
            "accepted_at": datetime.now(timezone.utc).isoformat(),
            "accepted_by": getpass.getuser(),
            "reason": reason.strip(),
            "accepted_policy_sha256": _digest(policy_text),
            "accepted_baseline_sha256": _digest(baseline_text),
        }
    )
    return {ACTIVE_POLICY: policy_text, ACTIVE_BASELINE: baseline_text}, review


def validate_agent_script(path: Path | None) -> str:
    if path is None:
        raise ValueError(
            "--github requires --agent-script path/to/your_traced_agent.py; stored captures are gated locally"
        )
    if path.is_absolute() or ".." in path.parts or "${{" in str(path) or any(ord(c) < 32 for c in str(path)):
        raise ValueError("--agent-script must be a literal project-relative Python file")
    if path.suffix != ".py" or not path.is_file() or not path.resolve().is_relative_to(Path.cwd().resolve()):
        raise ValueError(f"Agent script not found in this repository: {path}; pass the script you actually run")
    return path.as_posix()


def verify_active_starter() -> None:
    review = json.loads(STARTER_REVIEW.read_text(encoding="utf-8"))
    if review.get("review_required") is not False:
        raise ValueError("Review candidates first: maida init --reviewed --reason 'why these rules fit your task'")
    for path, field in ((ACTIVE_POLICY, "accepted_policy_sha256"), (ACTIVE_BASELINE, "accepted_baseline_sha256")):
        if _digest(path.read_text(encoding="utf-8")) != review.get(field):
            raise ValueError(f"Reviewed starter changed: {path}; review its current configuration before adding CI")
