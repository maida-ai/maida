from pathlib import Path

from maida.scaffold import (
    CHECKOUT_ACTION_REF,
    MAIDA_ACCEPT_ACTION_REF,
    MAIDA_ASSERT_ACTION_REF,
)

ROOT = Path(__file__).resolve().parents[1]


def test_public_docs_do_not_describe_legacy_trace_storage_contract():
    # TODO: This test must be removed to avoid leaking the legacy trace storage contract.
    docs = [
        "README.md",
        "docs/index.md",
        "docs/getting-started.md",
        "docs/cli.md",
        "docs/architecture.md",
        "docs/viewer.md",
        "docs/reference/config.md",
        "docs/regression-testing.md",
    ]
    legacy_snippets = [
        'spec_version":"0.1',
        'spec_version": "0.1',
        "~/.maida/runs/<run_id>/",
        "runs/<run_id>/",
        "first 8 chars of UUID",
        "full UUID",
        "run.json        # run metadata",
        "run.json - run metadata",
        "run.json).",
        "events.jsonl - one JSON object per line",
        "events_jsonl",
    ]

    offenders = []
    for rel_path in docs:
        text = (ROOT / rel_path).read_text(encoding="utf-8")
        for snippet in legacy_snippets:
            if snippet in text:
                offenders.append(f"{rel_path}: {snippet}")

    assert offenders == []


def test_trace_format_documents_current_storage_contract():
    text = (ROOT / "docs/reference/trace-format.md").read_text(encoding="utf-8")

    required_snippets = [
        "## Run storage layout",
        "`<data_dir>/runs/<trace_id>/`",
        "`meta.json` and `spans.jsonl` are the required files",
        '`spec_version` in `meta.json` (`"0.2"`) declares the storage contract version in-band',
        "`spans_to_events()` projection",
        "Stable for external tooling",
        "Internal and subject to change",
        "[`maida list`](../cli.md#maida-list)",
        "[`maida view`](../cli.md#maida-view)",
        "[`maida export`](../cli.md#maida-export)",
        "[`maida baseline`](../cli.md#maida-baseline)",
        "[`maida accept`](../cli.md#maida-accept)",
        "[`maida assert`](../cli.md#maida-assert)",
        "[`maida diff`](../cli.md#maida-diff)",
    ]

    missing = [snippet for snippet in required_snippets if snippet not in text]

    assert missing == []


def test_action_version_references_match_scaffold():
    docs = [
        "CHANGELOG.md",
        "README.md",
        "docs/cli.md",
        "docs/regression-testing.md",
    ]
    combined = "\n".join(
        (ROOT / rel_path).read_text(encoding="utf-8") for rel_path in docs
    )

    assert MAIDA_ASSERT_ACTION_REF in combined
    assert MAIDA_ACCEPT_ACTION_REF in combined
    assert "maida-ai/maida-assert@v2" not in combined
    assert "maida-ai/maida-assert@V2" not in combined

    workflow_text = (ROOT / "maida/scaffold.py").read_text(encoding="utf-8")
    assert CHECKOUT_ACTION_REF in workflow_text
    assert MAIDA_ASSERT_ACTION_REF in workflow_text
    assert MAIDA_ACCEPT_ACTION_REF in workflow_text
    assert "actions/checkout@v4" not in workflow_text
    assert "maida-ai/maida-assert@v2" not in workflow_text


def test_baseline_provenance_contract_is_documented():
    combined = "\n".join(
        (ROOT / rel_path).read_text(encoding="utf-8")
        for rel_path in ["README.md", "docs/cli.md", "docs/regression-testing.md"]
    )

    required_snippets = [
        "accepted_by",
        "accepted_at",
        "MAIDA_PR_NUMBER",
        "MAIDA_EXPECTED_HEAD_SHA",
        "Baseline provenance",
        "accepted-run verdict summary",
    ]

    missing = [snippet for snippet in required_snippets if snippet not in combined]
    assert missing == []


def test_adapter_conformance_contract_covers_required_behavior():
    text = " ".join(
        (ROOT / "maida/integrations/CONTRIBUTING.md")
        .read_text(encoding="utf-8")
        .split()
    )

    required_snippets = [
        "## Adapter conformance contract",
        "### Required normalized signals",
        "`RUN_START`",
        "`RUN_END`",
        "`LLM_CALL`",
        "`TOOL_CALL`",
        "`ERROR`",
        "`LOOP_WARNING`",
        "terminal `RUN_END`",
        "### Deterministic offline conformance tests",
        "real provider calls",
        "### Redaction and truncation",
        "`__REDACTED__`",
        "`__TRUNCATED__`",
        "### Framework-specific metadata",
        "`meta.<adapter_name>`",
        "MUST NOT add framework-specific event types",
    ]

    missing = [snippet for snippet in required_snippets if snippet not in text]

    assert missing == []


def test_openai_agents_docs_include_offline_success_and_regression_workflow():
    docs = (ROOT / "docs/integrations.md").read_text(encoding="utf-8")
    example = (ROOT / "examples/openai_agents/minimal.py").read_text(encoding="utf-8")

    required_docs = [
        'pip install "maida-ai[openai]"',
        "openai-agents-baseline.json",
        "examples/openai_agents/minimal.py --regression",
        "RUN_START -> LLM_CALL -> TOOL_CALL(lookup_docs) -> TOOL_CALL(handoff) -> RUN_END",
        "LOOP_WARNING",
        "exits with code `1`",
    ]
    missing_docs = [snippet for snippet in required_docs if snippet not in docs]

    assert missing_docs == []
    assert '"--regression"' in example
    assert "regression=args.regression" in example


def test_crewai_docs_cover_offline_success_and_strict_regression_workflow():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    integration_docs = (ROOT / "docs/integrations.md").read_text(encoding="utf-8")
    example = (ROOT / "examples/crewai/minimal.py").read_text(encoding="utf-8")

    for snippet in (
        'pip install "maida-ai[crewai]"',
        "examples/crewai/minimal.py",
        "--regression",
    ):
        assert snippet in readme or snippet in integration_docs

    for snippet in (
        "RUN_START -> LLM_CALL -> TOOL_CALL(search_docs) -> RUN_END",
        "three consecutive `search_docs` calls",
        "maida baseline --out crewai-baseline.json",
        "maida assert --baseline crewai-baseline.json --tool-call-tolerance 0",
        "exits with code `1`",
        "maida_crewai.raise_if_aborted()",
        "Mock%20CrewAI%20Agent.ipynb",
    ):
        assert snippet in integration_docs

    for snippet in (
        "LLMCallHookContext",
        "ToolCallHookContext",
        "get_before_llm_call_hooks",
        "get_after_tool_call_hooks",
        "crewai_event_bus.shutdown",
        "tool_calls = 3 if regression else 1",
    ):
        assert snippet in example
