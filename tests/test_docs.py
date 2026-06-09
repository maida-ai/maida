from pathlib import Path


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
