from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_emitter_guide_documents_complete_external_contract() -> None:
    text = (ROOT / "docs" / "reference" / "trace-emitter.md").read_text(
        encoding="utf-8"
    )

    for snippet in (
        "maida validate-trace",
        "meta.json",
        "spans.jsonl",
        'spec_version: "0.2.0"',
        "Required fields",
        "Optional enrichments",
        "Main thread",
        "Subthreads",
        "parent_span_id",
        "Breaking changes",
        "Exit codes",
    ):
        assert snippet in text


def test_schema_changelog_and_trace_reference_define_version_policy() -> None:
    changelog = (ROOT / "schemas" / "trace" / "CHANGELOG.md").read_text(
        encoding="utf-8"
    )
    reference = (ROOT / "docs" / "reference" / "trace-format.md").read_text(
        encoding="utf-8"
    )

    assert "## 0.2.0" in changelog
    for snippet in (
        "versioned JSON Schemas",
        "Patch releases",
        "Minor releases",
        "Major releases",
        "immutable",
    ):
        assert snippet in reference


def test_public_docs_link_emitter_guide_and_validator() -> None:
    index = (ROOT / "docs" / "index.md").read_text(encoding="utf-8")
    # The CLI reference is an index page plus one page per command.
    cli = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [
            ROOT / "docs" / "cli.md",
            *sorted((ROOT / "docs" / "cli").rglob("*.md")),
        ]
    )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "reference/trace-emitter.md" in index
    assert "# `maida validate-trace`" in cli
    assert "maida validate-trace PATH [--json]" in cli
    assert "validate-trace" in readme
