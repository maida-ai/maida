from pathlib import Path

from maida.scaffold import (
    CHECKOUT_ACTION_REF,
    MAIDA_ACCEPT_ACTION_REF,
    MAIDA_ASSERT_ACTION_REF,
)

ROOT = Path(__file__).resolve().parents[1]


def read_docs(*relatives: str) -> str:
    """Concatenate docs pages, expanding a directory into all its pages.

    These contracts care that something is documented somewhere the reader will
    find it, not which file it landed in. Passing a directory keeps them honest
    when a reference page is split into per-command pages.
    """
    parts: list[str] = []
    for relative in relatives:
        path = ROOT / relative
        if path.is_dir():
            parts.extend(
                child.read_text(encoding="utf-8")
                for child in sorted(path.rglob("*.md"))
            )
        else:
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


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
        '`spec_version` in `meta.json` (`"0.2.0"`) declares the storage contract version in-band',
        "`spans_to_events()` projection",
        "External tooling may rely on:",
        "External tooling should not rely on:",
        "[`maida list`](../cli/list.md)",
        "[`maida view`](../cli/view.md)",
        "[`maida export`](../cli/export.md)",
        "[`maida baseline`](../cli/baseline.md)",
        "[`maida accept`](../cli/accept.md)",
        "[`maida assert`](../cli/assert.md)",
        "[`maida diff`](../cli/diff.md)",
    ]

    missing = [snippet for snippet in required_snippets if snippet not in text]

    assert missing == []


def test_action_version_references_match_scaffold():
    combined = read_docs(
        "CHANGELOG.md",
        "README.md",
        "docs/cli.md",
        "docs/cli",
        "docs/regression-testing.md",
    )

    assert MAIDA_ASSERT_ACTION_REF == "maida-ai/maida-assert@v5"
    assert MAIDA_ASSERT_ACTION_REF in combined
    assert MAIDA_ACCEPT_ACTION_REF in combined
    assert "maida-ai/maida-assert@v2" not in combined
    assert "maida-ai/maida-assert@V2" not in combined
    assert "maida-ai/maida-assert@V4" not in combined
    assert "maida-ai/maida-assert@V5" not in combined

    workflow_text = (ROOT / "maida/scaffold.py").read_text(encoding="utf-8")
    assert CHECKOUT_ACTION_REF in workflow_text
    assert MAIDA_ASSERT_ACTION_REF in workflow_text
    assert MAIDA_ACCEPT_ACTION_REF in workflow_text
    assert "actions/checkout@v4" not in workflow_text
    assert "maida-ai/maida-assert@v2" not in workflow_text


def test_baseline_provenance_contract_is_documented():
    combined = read_docs(
        "README.md", "docs/cli.md", "docs/cli", "docs/regression-testing.md"
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


def test_scheduled_checks_document_current_external_emitter_contract():
    text = " ".join(
        (ROOT / "docs/scheduled-checks.md").read_text(encoding="utf-8").split()
    )

    required_snippets = [
        "External emitters that follow the native trace contract",
        "`maida export` JSON window inputs remain a future source format",
        "Directory fanout is intentionally reserved",
    ]

    assert [snippet for snippet in required_snippets if snippet not in text] == []
    assert "[#172]" not in text


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
    docs = read_docs("docs/integrations.md", "docs/integrations")
    example = (ROOT / "examples/openai_agents/minimal.py").read_text(encoding="utf-8")

    required_docs = [
        'uv add "maida-ai[openai]>=0.5"',
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
    integration_docs = read_docs("docs/integrations.md", "docs/integrations")
    example = (ROOT / "examples/crewai/minimal.py").read_text(encoding="utf-8")

    for snippet in (
        'uv add "maida-ai[crewai]>=0.5"',
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


def test_langfuse_docs_cover_read_only_import_and_mapping_contract():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    cli_docs = (ROOT / "docs/cli.md").read_text(encoding="utf-8")
    integration_docs = (ROOT / "docs/integrations.md").read_text(encoding="utf-8")
    langfuse_docs = (ROOT / "docs/langfuse.md").read_text(encoding="utf-8")
    combined = "\n".join([readme, cli_docs, integration_docs, langfuse_docs])

    for snippet in (
        "Langfuse tells you what happened; Maida tells you whether it changed.",
        "maida import langfuse --trace-id",
        "GET /api/public/v2/observations",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "One Langfuse trace becomes one Maida run",
        "structural span",
        "Missing parents",
        "ClickHouse",
        "fully synthetic",
        "read-only",
        "trace-command:",
        "maida-ai/maida-assert@v5",
        "fixed one-trial gate",
        "maida-tutorials/tree/main/demos/langfuse_import",
    ):
        assert snippet in combined

    assert "Maida is an observability platform" not in combined
    assert "Maida is a monitoring platform" not in combined


def test_scheduled_checks_document_drift_contract_and_future_inputs():
    guide = (ROOT / "docs/scheduled-checks.md").read_text(encoding="utf-8")
    cli = (ROOT / "docs/cli.md").read_text(encoding="utf-8")
    combined = "\n".join([guide, cli])

    for snippet in (
        "maida drift",
        "--window",
        "one baseline per invocation",
        "Canary promotion",
        "report_kind: drift",
        "maida export",
        "native trace contract",
        "Directory fanout",
        "INCONCLUSIVE",
    ):
        assert snippet in combined

    assert "monitoring" not in combined.lower()


def test_extraction_docs_require_review_and_preserve_local_storage() -> None:
    guide = (ROOT / "docs/extraction.md").read_text(encoding="utf-8")
    cli = (ROOT / "docs/cli.md").read_text(encoding="utf-8")
    index = (ROOT / "docs/index.md").read_text(encoding="utf-8")
    combined = "\n".join([guide, cli, index])

    for snippet in (
        "maida extract --window",
        "--workflow",
        "draft_version: 1.0.0",
        "review_required: true",
        "draft.json",
        "baseline.json",
        "policy.yaml",
        "human review",
        "never writes to `.maida`",
        "prompts, responses, tool arguments, or tool results",
        "Exit `0`",
        "Exit `2`",
        "Exit `10`",
    ):
        assert snippet in combined

    assert "auto-adopt" not in combined.lower()
    assert "automatically activates" not in combined.lower()


def test_two_tier_acceptance_design_covers_v3_safety_contract():
    design_path = ROOT / "docs/design/policy-v3-two-tier-acceptance.md"
    text = design_path.read_text(encoding="utf-8")
    normalized = " ".join(text.replace("**", "").replace(">", "").casefold().split())

    required_sections = [
        "## Status and scope",
        "## Proposed policy v3 shape",
        "## Acceptance matrix",
        "## Cumulative human-anchor rule",
        "## Provenance contract",
        "## Canary and drift verdicts",
        "## Self-improvement flow",
        "## Follow-up implementation issues",
    ]
    required_contracts = [
        "Non-normative, non-indexed design proposal",
        "does not change current Maida behavior",
        "future breaking `version: 3`",
        "human-owned `envelope`",
        "plastic `baseline`",
        "allowed tools",
        "data surfaces",
        "approval requirements",
        "hard ceilings",
        "cumulative refresh bounds",
        "auto-refresh is disabled by default",
        "Every envelope change requires human acceptance",
        "regardless of whether its author is a human or an agent",
        "Agents may author changes, but can never approve an envelope change",
        "Baseline-only + PASS",
        "FAIL holds promotion and refresh",
        "INCONCLUSIVE defers promotion and refresh",
        "last human-approved anchor",
        "never merely against the rolling baseline",
        "`max_updates`",
        "`max_age`",
        "`anchor_tolerances`",
        "replaces the full baseline sample",
        "does not append or accumulate traces",
        "author identity",
        "automatic acceptance identity",
        "approver identity",
        "source revision",
        "source report",
        "previous artifact hash",
        "human-anchor hash",
        "Policy loading and migration",
        "Two-tier evaluation",
        "Baseline refresh and provenance",
        "Accept-flow UX",
        "Drift and canary integration",
        "Both founders must review and approve this proposal before merge",
    ]

    assert [section for section in required_sections if section not in text] == []
    assert [
        item for item in required_contracts if item.casefold() not in normalized
    ] == []

    index = (ROOT / "docs/index.md").read_text(encoding="utf-8")
    assert design_path.name not in index
