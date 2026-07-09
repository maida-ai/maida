from pathlib import Path

from maida.scaffold import CHECKOUT_ACTION_REF, MAIDA_ASSERT_ACTION_REF

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
    assert "maida-ai/maida-assert@v2" not in combined
    assert "maida-ai/maida-assert@V2" not in combined

    workflow_text = (ROOT / "maida/scaffold.py").read_text(encoding="utf-8")
    assert CHECKOUT_ACTION_REF in workflow_text
    assert MAIDA_ASSERT_ACTION_REF in workflow_text
    assert "actions/checkout@v4" not in workflow_text
    assert "maida-ai/maida-assert@v2" not in workflow_text
