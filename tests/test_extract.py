"""Reviewable gate-draft extraction from native trace windows."""

from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

import jsonschema
import pytest
import yaml

from maida.baseline import load_baseline
from maida.config import load_config
from maida.drift import NativeTraceWindowSource, run_drift
from maida.extract import ExtractionInputError, extract_window
from maida.policy import load_policy
from maida.statistics import GateVerdict


ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "traces" / "current"


def _copy_trace(
    fixture: str,
    runs_dir: Path,
    *,
    trace_id: str,
    run_name: str | None,
    started_at: str,
) -> Path:
    destination = runs_dir / trace_id
    shutil.copytree(FIXTURES / fixture, destination)

    meta_path = destination / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    old_trace_id = meta["trace_id"]
    meta.update(
        trace_id=trace_id,
        run_name=run_name,
        started_at=started_at,
    )
    meta_path.write_text(json.dumps(meta), encoding="utf-8")

    spans_path = destination / "spans.jsonl"
    spans = []
    for line in spans_path.read_text(encoding="utf-8").splitlines():
        span = json.loads(line)
        assert span["trace_id"] == old_trace_id
        span["trace_id"] = trace_id
        spans.append(span)
    spans_path.write_text(
        "".join(json.dumps(span) + "\n" for span in spans), encoding="utf-8"
    )
    return destination


def _window(tmp_path: Path) -> Path:
    runs_dir = tmp_path / "partner-export" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace(
        "tool-call-spike",
        runs_dir,
        trace_id="2" * 32,
        run_name="Orders / Primary",
        started_at="2026-08-02T00:00:00.000Z",
    )
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="1" * 32,
        run_name="Orders / Primary",
        started_at="2026-08-01T00:00:00.000Z",
    )
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="4" * 32,
        run_name="Orders / Primary",
        started_at="2026-08-01T12:00:00.000Z",
    )
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="3" * 32,
        run_name="Billing",
        started_at="2026-08-03T00:00:00.000Z",
    )
    return runs_dir


def _read_tree(path: Path) -> dict[str, bytes]:
    return {
        item.relative_to(path).as_posix(): item.read_bytes()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def _artifact_text(path: Path) -> str:
    return "\n".join(
        item.read_text(encoding="utf-8")
        for item in sorted(path.rglob("*"))
        if item.is_file()
    )


def test_native_window_load_all_validates_every_trace_and_sorts_oldest_first(
    tmp_path: Path,
) -> None:
    runs_dir = _window(tmp_path)
    before = _read_tree(runs_dir)

    source = NativeTraceWindowSource(runs_dir, load_config())
    traces = source.load_all()

    assert [item.trace_id for item in traces] == [
        "1" * 32,
        "4" * 32,
        "2" * 32,
        "3" * 32,
    ]
    assert source.load("Billing")[0].trace_id == "3" * 32
    assert _read_tree(runs_dir) == before

    running = runs_dir / ("3" * 32) / "meta.json"
    meta = json.loads(running.read_text(encoding="utf-8"))
    meta.update(status="running", ended_at=None, duration_ms=None)
    running.write_text(json.dumps(meta), encoding="utf-8")
    with pytest.raises(ValueError, match="only completed traces"):
        source.load_all()


def test_extract_window_writes_reviewable_multi_workflow_draft_atomically(
    tmp_path: Path,
) -> None:
    runs_dir = _window(tmp_path)
    source_before = _read_tree(runs_dir)
    out_dir = tmp_path / "gate-draft"

    draft = extract_window(runs_dir, out_dir=out_dir, config=load_config())

    assert draft == json.loads((out_dir / "draft.json").read_text(encoding="utf-8"))
    assert draft["draft_version"] == "1.0.0"
    assert draft["review_required"] is True
    assert [item["run_name"] for item in draft["workflows"]] == [
        "Billing",
        "Orders / Primary",
    ]
    orders = draft["workflows"][1]
    assert orders["trace_ids"] == ["1" * 32, "4" * 32, "2" * 32]
    assert orders["representative_trace_ids"] == ["1" * 32, "2" * 32]
    assert [cluster["representative_trace_id"] for cluster in orders["clusters"]] == [
        "1" * 32,
        "2" * 32,
    ]
    assert orders["clusters"][0]["trace_ids"] == ["1" * 32, "4" * 32]
    assert orders["clusters"][0]["count"] == 2
    assert orders["clusters"][0]["signature"] == {
        "tool_path": ["search"],
        "tool_call_sequence": ["search"],
        "tool_call_counts": {"search": 1},
        "llm_models_used": ["gpt-4o-mini"],
        "event_type_sequence": ["RUN_START", "LLM_CALL", "TOOL_CALL", "RUN_END"],
        "final_status": "ok",
    }
    assert orders["tools"] == {
        "intersection": ["search"],
        "union": ["calculator", "fetch_profile", "search", "summarize"],
    }
    assert orders["step_band"] == {"lower": 2, "upper": 5}
    assert orders["ceilings"] == {"tool_calls": 4, "tokens": 25}
    assert orders["terminal_states"] == ["ok"]

    artifact_paths = {
        item.relative_to(out_dir).as_posix()
        for item in out_dir.rglob("*")
        if item.is_file()
    }
    assert artifact_paths == {
        "draft.json",
        *{
            f"{workflow['artifact_dir']}/baseline.json"
            for workflow in draft["workflows"]
        },
        *{f"{workflow['artifact_dir']}/policy.yaml" for workflow in draft["workflows"]},
    }
    assert all(
        workflow["artifact_dir"].startswith("workflows/")
        and ".." not in workflow["artifact_dir"]
        and not Path(workflow["artifact_dir"]).is_absolute()
        for workflow in draft["workflows"]
    )
    assert _read_tree(runs_dir) == source_before


def test_extracted_baselines_and_policies_are_valid_and_pass_the_source_window(
    tmp_path: Path,
) -> None:
    runs_dir = _window(tmp_path)
    out_dir = tmp_path / "gate-draft"
    draft = extract_window(runs_dir, out_dir=out_dir, config=load_config())
    baseline_schema = json.loads(
        (ROOT / "schemas" / "baseline.schema.json").read_text(encoding="utf-8")
    )
    policy_schema = json.loads(
        (ROOT / "schemas" / "policy.schema.json").read_text(encoding="utf-8")
    )

    for workflow in draft["workflows"]:
        artifact_dir = out_dir / workflow["artifact_dir"]
        baseline = load_baseline(artifact_dir / "baseline.json")
        policy = load_policy(artifact_dir / "policy.yaml")
        policy_payload = yaml.safe_load(
            (artifact_dir / "policy.yaml").read_text(encoding="utf-8")
        )
        jsonschema.validate(baseline, baseline_schema)
        jsonschema.validate(policy_payload, policy_schema)
        assert baseline["source_run_ids"] == workflow["trace_ids"]
        assert baseline["trial_sample"]["trials"] == len(workflow["trace_ids"])
        assert policy.trials == len(workflow["trace_ids"])
        report = run_drift(
            runs_dir,
            baseline=baseline,
            policy=policy,
            config=load_config(),
            agent_name=workflow["run_name"],
        )
        assert report.verdict is GateVerdict.PASS

    policy_text = (
        out_dir / draft["workflows"][1]["artifact_dir"] / "policy.yaml"
    ).read_text(encoding="utf-8")
    assert "DRAFT" in policy_text
    assert "human review" in policy_text
    assert "required_tools:" in policy_text
    assert "stop_condition_reached:" in policy_text
    assert "no_loops:" in policy_text
    assert "no_guardrails:" in policy_text
    assert "direction: both" in policy_text
    assert "lower: 2" in policy_text
    assert "upper: 5" in policy_text
    assert "aggregate: max" in policy_text


def test_extraction_is_deterministic_and_omits_trace_payloads_and_paths(
    tmp_path: Path,
) -> None:
    runs_dir = _window(tmp_path)
    first = tmp_path / "draft-one"
    second = tmp_path / "draft-two"

    extract_window(runs_dir, out_dir=first, config=load_config())
    extract_window(runs_dir, out_dir=second, config=load_config())

    assert _read_tree(first) == _read_tree(second)
    output = _artifact_text(first)
    for private_value in (
        "find context",
        "use search",
        '"query":"maida"',
        '"results":1',
        "same plan",
        "call many tools",
        '"user":"ada"',
        '"tier":"pro"',
        "maida.cwd",
        "/workspace/maida",
        str(runs_dir.resolve()),
    ):
        assert private_value not in output
    assert "spans" not in json.loads((first / "draft.json").read_text())["workflows"][0]
    assert "meta" not in json.loads((first / "draft.json").read_text())["workflows"][0]


@pytest.mark.parametrize(
    ("selectors", "message"),
    [
        (["Missing"], "no traces for workflow"),
        (["Billing", "Billing"], "duplicate --workflow"),
        ([""], "must not be empty"),
    ],
)
def test_extract_window_rejects_invalid_workflow_selections(
    tmp_path: Path, selectors: list[str], message: str
) -> None:
    runs_dir = _window(tmp_path)
    out_dir = tmp_path / "draft"

    with pytest.raises(ExtractionInputError, match=message):
        extract_window(
            runs_dir,
            out_dir=out_dir,
            config=load_config(),
            workflows=selectors,
        )

    assert not out_dir.exists()


def test_extract_window_exact_selector_ignores_other_workflows_and_empty_names(
    tmp_path: Path,
) -> None:
    runs_dir = _window(tmp_path)
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="5" * 32,
        run_name=None,
        started_at="2026-08-04T00:00:00.000Z",
    )

    selected_dir = tmp_path / "selected"
    draft = extract_window(
        runs_dir,
        out_dir=selected_dir,
        config=load_config(),
        workflows=["Orders / Primary"],
    )
    assert [item["run_name"] for item in draft["workflows"]] == ["Orders / Primary"]

    all_dir = tmp_path / "all"
    all_draft = extract_window(runs_dir, out_dir=all_dir, config=load_config())
    assert [item["run_name"] for item in all_draft["workflows"]] == [
        "Billing",
        "Orders / Primary",
    ]


def test_extract_window_rejects_wrong_layout_empty_and_invalid_windows(
    tmp_path: Path,
) -> None:
    wrong_layout = tmp_path / "wrong-layout"
    wrong_layout.mkdir()
    with pytest.raises(ExtractionInputError, match="ending in /runs"):
        extract_window(
            wrong_layout,
            out_dir=tmp_path / "wrong-draft",
            config=load_config(),
        )

    empty_runs = tmp_path / "empty" / "runs"
    empty_runs.mkdir(parents=True)
    with pytest.raises(ExtractionInputError, match="contains no traces"):
        extract_window(
            empty_runs,
            out_dir=tmp_path / "empty-draft",
            config=load_config(),
        )

    invalid_runs = tmp_path / "invalid" / "runs"
    invalid_runs.mkdir(parents=True)
    invalid_trace = _copy_trace(
        "normal",
        invalid_runs,
        trace_id="6" * 32,
        run_name="Orders",
        started_at="2026-08-06T00:00:00.000Z",
    )
    (invalid_trace / "spans.jsonl").write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ExtractionInputError, match="malformed JSON"):
        extract_window(
            invalid_runs,
            out_dir=tmp_path / "invalid-draft",
            config=load_config(),
        )

    assert not (tmp_path / "wrong-draft").exists()
    assert not (tmp_path / "empty-draft").exists()
    assert not (tmp_path / "invalid-draft").exists()


def test_extract_window_rejects_unsafe_output_and_cleans_failed_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runs_dir = _window(tmp_path)
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ExtractionInputError, match="already exists"):
        extract_window(runs_dir, out_dir=existing, config=load_config())
    assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"

    dangling = tmp_path / "dangling"
    dangling.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    with pytest.raises(ExtractionInputError, match="already exists"):
        extract_window(runs_dir, out_dir=dangling, config=load_config())
    assert dangling.is_symlink()
    assert not (tmp_path / "missing-target").exists()

    with pytest.raises(ExtractionInputError, match="inside the trace window"):
        extract_window(
            runs_dir,
            out_dir=runs_dir / "draft",
            config=load_config(),
        )

    def fail_verification(*args: object, **kwargs: object) -> None:
        raise RuntimeError("verification failed")

    monkeypatch.setattr("maida.extract._verify_workflow", fail_verification)
    failed_out = tmp_path / "failed-draft"
    with pytest.raises(RuntimeError, match="verification failed"):
        extract_window(runs_dir, out_dir=failed_out, config=load_config())

    assert not failed_out.exists()
    assert not list(tmp_path.glob(".failed-draft.*.tmp"))
    assert _read_tree(runs_dir)


def test_completed_error_window_fails_self_consistency_without_installing_output(
    tmp_path: Path,
) -> None:
    runs_dir = tmp_path / "error-window" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace(
        "guardrail",
        runs_dir,
        trace_id="7" * 32,
        run_name="Known error workflow",
        started_at="2026-08-07T00:00:00.000Z",
    )
    out_dir = tmp_path / "error-draft"

    with pytest.raises(RuntimeError, match="did not pass its source window"):
        extract_window(runs_dir, out_dir=out_dir, config=load_config())

    assert not out_dir.exists()
    assert not list(tmp_path.glob(".error-draft.*.tmp"))


def test_workflow_artifact_names_use_safe_slug_and_stable_hash(tmp_path: Path) -> None:
    runs_dir = tmp_path / "input" / "runs"
    runs_dir.mkdir(parents=True)
    _copy_trace(
        "normal",
        runs_dir,
        trace_id="5" * 32,
        run_name="../../ACME Orders!?",
        started_at="2026-08-05T00:00:00.000Z",
    )

    draft = extract_window(runs_dir, out_dir=tmp_path / "draft", config=load_config())
    artifact_dir = draft["workflows"][0]["artifact_dir"]
    expected_hash = sha256("../../ACME Orders!?".encode("utf-8")).hexdigest()[:12]

    assert artifact_dir == f"workflows/acme-orders-{expected_hash}"
    assert (tmp_path / "draft" / artifact_dir / "baseline.json").is_file()
