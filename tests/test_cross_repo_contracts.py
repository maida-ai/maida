"""Machine-readable contracts shared with Maida's sibling repositories."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.version import Version

from maida.cli import app
from maida.loopdetect import detect_loop
from maida.plan_contract import (
    PlanContractError,
    plan_artifact_from_resolved_signature,
    plan_metric_values,
    plan_set_values,
)
from maida.schema_versions import (
    BASELINE_SCHEMA_VERSION,
    PLAN_SCHEMA_VERSION,
    POLICY_SCHEMA_VERSION,
    REPORT_SCHEMA_VERSION,
    TRACE_SCHEMA_VERSION,
)
from maida.trace_validation import TraceValidationError, validate_trace_payload


ROOT = Path(__file__).resolve().parents[1]
CONTRACTS = ROOT / "contracts"


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_current_main_contract_matches_python_source_of_truth() -> None:
    contract = _read_json(CONTRACTS / "current-main.json")

    assert contract["schemas"] == {
        "trace": TRACE_SCHEMA_VERSION,
        "baseline": BASELINE_SCHEMA_VERSION,
        "policy": POLICY_SCHEMA_VERSION,
        "report": REPORT_SCHEMA_VERSION,
        "plan": PLAN_SCHEMA_VERSION,
    }
    assert contract["engine_ref"] != "main"
    assert re.fullmatch(
        r"v\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.post\d+)?",
        contract["engine_ref"],
    )
    assert re.fullmatch(r"maida-ai/maida-assert@v\d+\.\d+\.\d+", contract["action_ref"])
    engine_version = Version(contract["engine_ref"].removeprefix("v"))
    action_version = Version(contract["action_ref"].split("@v", 1)[1])
    assert action_version.release[:2] == engine_version.release[:2]
    assert contract["cli"]["primary_gate"] == "run"
    assert contract["cli"]["legacy_gate"] == "assert"
    pending = _read_json(CONTRACTS / "unreleased-cli.json")
    assert pending["based_on"] == contract["engine_ref"]
    for field, registered in (
        ("top_level_commands", app.registered_commands),
        ("command_groups", app.registered_groups),
    ):
        released = set(contract["cli"][field])
        added = {item["name"] for item in pending[field]}
        assert len(added) == len(pending[field])
        assert not released & added, "Released commands must be removed from the pending manifest"
        assert {item.name for item in registered} == released | added
        for item in pending[field]:
            assert (ROOT / item["documentation"]).is_file()


def test_primary_public_docs_use_the_released_channel() -> None:
    contract = _read_json(CONTRACTS / "current-main.json")
    for relative in ("README.md", "docs/index.md", "docs/getting-started.md", "docs/python-agent.md"):
        text = (ROOT / relative).read_text(encoding="utf-8")
        installs = re.findall(r"maida-ai(?:==|>=)[0-9][0-9.a-z]*", text)
        assert installs, f"No released install command in {relative}"
        release = Version(contract["engine_ref"].removeprefix("v"))
        assert release in Requirement(contract["install_requirement"]).specifier
        for install in installs:
            assert release in Requirement(install).specifier
        assert "git+https://github.com/maida-ai/maida.git@main" not in text

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "https://maida.ai/docs/getting-started/" in readme
    assert "docs/python-agent.md" in readme
    python_guide = (ROOT / "docs/python-agent.md").read_text(encoding="utf-8")
    assert f"maida {contract['cli']['primary_gate']} agent.py" in python_guide
    assert "maida assert --baseline" not in readme.split("## CLI reference", 1)[0]


def test_core_ci_covers_contract_sources_and_public_docs() -> None:
    unit_workflow = (ROOT / ".github" / "workflows" / "unittest-fast.yml").read_text(encoding="utf-8")
    for path_filter in ("README.md", "contracts/**", "docs/**", "scripts/**"):
        assert f"- {path_filter}" in unit_workflow

    sync_workflow = (ROOT / ".github" / "workflows" / "cross-repo-sync.yml").read_text(encoding="utf-8")
    assert "schedule:" in sync_workflow
    assert "workflow_dispatch:" in sync_workflow
    for repository in (
        "maida-ai/maida-assert",
        "maida-ai/maida-ts",
        "maida-ai/maida-ai.github.io",
        "maida-ai/maida-tutorials",
        "maida-ai/maida-workflows",
    ):
        assert f"repository: {repository}" in sync_workflow
    assert "scripts/check_cross_repo_sync.py" in sync_workflow


@pytest.mark.parametrize(
    "case",
    _read_json(CONTRACTS / "conformance" / "loop-vectors.json")["cases"],
    ids=lambda case: case["name"],
)
def test_loop_conformance_vectors(case: dict) -> None:
    assert detect_loop(case["events"], case["window"], case["repetitions"]) == case["expected"]


def _validation_case_payload(vectors: dict, case: dict) -> tuple[dict, list[dict]]:
    meta = copy.deepcopy(vectors["base"]["meta"])
    spans = copy.deepcopy(vectors["base"]["spans"])
    meta.update(case.get("meta_overrides", {}))
    for index, overrides in case.get("span_overrides", {}).items():
        spans[int(index)].update(overrides)
    for span_index, events in case.get("event_overrides", {}).items():
        for event_index, overrides in events.items():
            spans[int(span_index)]["events"][int(event_index)].update(overrides)
    return meta, spans


_VALIDATION_VECTORS = _read_json(CONTRACTS / "conformance" / "trace-validation-vectors.json")


@pytest.mark.parametrize(
    "case",
    _VALIDATION_VECTORS["cases"],
    ids=lambda case: case["name"],
)
def test_trace_validation_conformance_vectors(case: dict) -> None:
    meta, spans = _validation_case_payload(_VALIDATION_VECTORS, case)
    if case["expected_valid"]:
        validate_trace_payload(meta, spans)
        return

    with pytest.raises(TraceValidationError) as excinfo:
        validate_trace_payload(meta, spans)
    assert case["diagnostic_code"] in {diagnostic.code for diagnostic in excinfo.value.diagnostics}


_PLAN_VECTORS = _read_json(CONTRACTS / "conformance" / "plan-contract-vectors.json")


@pytest.mark.parametrize(
    "case",
    _PLAN_VECTORS["cases"],
    ids=lambda case: case["name"],
)
def test_plan_contract_conformance_vectors(case: dict) -> None:
    signature = copy.deepcopy(_PLAN_VECTORS["base"])
    signature.update(case.get("signature_overrides", {}))
    if not case["expected_valid"]:
        with pytest.raises(PlanContractError) as excinfo:
            plan_artifact_from_resolved_signature(signature)
        assert excinfo.value.code == case["diagnostic_code"]
        return

    artifact = plan_artifact_from_resolved_signature(signature)
    assert artifact.artifact_id == case["artifact_id"]
    assert plan_metric_values(artifact) == case["metrics"]
    assert {name: list(values) for name, values in plan_set_values(artifact).items()} == (case["sets"])
