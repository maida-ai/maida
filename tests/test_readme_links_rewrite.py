"""Unit coverage for the hatch README relative-link rewrite hook."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_HOOK_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "hatch_hooks"
    / "readme_links_rewrite.py"
)
_SPEC = importlib.util.spec_from_file_location("readme_links_rewrite", _HOOK_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_HOOK = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_HOOK)

BASE = "https://github.com/maida-ai/maida/blob/v0.5.0"


def test_markdown_preview_link_becomes_absolute_with_raw_true() -> None:
    content = "![Guardrails demo](docs/assets/guardrails.gif)"
    assert _HOOK.relative_preview_links(content, BASE) == (
        "![Guardrails demo]("
        "https://github.com/maida-ai/maida/blob/v0.5.0/docs/assets/guardrails.gif"
        "?raw=True)"
    )


def test_markdown_non_preview_link_becomes_absolute_without_raw() -> None:
    content = "See [docs/guardrails.md](docs/guardrails.md) for details."
    assert _HOOK.relative_non_preview_links(content, BASE) == (
        "See [docs/guardrails.md]("
        "https://github.com/maida-ai/maida/blob/v0.5.0/docs/guardrails.md) for details."
    )


def test_nested_badge_markdown_link_rewrites_outer_relative_target() -> None:
    content = (
        "[![License](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)"
    )
    assert _HOOK.relative_non_preview_links(content, BASE) == (
        "[![License](https://img.shields.io/badge/license-Apache--2.0-green)]"
        "(https://github.com/maida-ai/maida/blob/v0.5.0/LICENSE)"
    )


def test_html_preview_src_becomes_absolute_with_raw_true() -> None:
    content = '<img alt="demo" src="docs/assets/timeline.gif">'
    assert _HOOK.relative_html_preview_links(content, BASE) == (
        '<img alt="demo" src="'
        "https://github.com/maida-ai/maida/blob/v0.5.0/docs/assets/timeline.gif"
        '?raw=True">'
    )


def test_html_href_becomes_absolute_without_raw() -> None:
    content = '<a href="docs/cli.md">CLI</a>'
    assert _HOOK.relative_html_links(content, BASE) == (
        '<a href="https://github.com/maida-ai/maida/blob/v0.5.0/docs/cli.md">CLI</a>'
    )


@pytest.mark.parametrize(
    ("content", "rewriter"),
    [
        (
            "![badge](https://img.shields.io/pypi/v/maida-ai.svg)",
            "relative_preview_links",
        ),
        ("[PyPI](https://pypi.org/project/maida-ai/)", "relative_non_preview_links"),
        ('<img src="https://example.com/a.gif">', "relative_html_preview_links"),
        ('<a href="https://example.com/doc">x</a>', "relative_html_links"),
        ("[section](#try-it)", "relative_non_preview_links"),
        ('<a href="#try-it">section</a>', "relative_html_links"),
        ("[cdn](//cdn.example/x)", "relative_non_preview_links"),
        ("[mail](mailto:dev@example.com)", "relative_non_preview_links"),
    ],
)
def test_external_and_ignored_urls_are_unchanged(content: str, rewriter: str) -> None:
    fn = getattr(_HOOK, rewriter)
    assert fn(content, BASE) == content


def test_preview_preserves_fragment_and_appends_raw_after_query() -> None:
    content = "![shot](docs/assets/shot.gif?v=1#top)"
    assert _HOOK.relative_preview_links(content, BASE) == (
        "![shot]("
        "https://github.com/maida-ai/maida/blob/v0.5.0/docs/assets/shot.gif"
        "?v=1&raw=True#top)"
    )


def test_rewrite_readme_links_handles_markdown_and_html_together() -> None:
    content = "\n".join(
        [
            "![demo](docs/assets/demo.gif)",
            "See [policy](docs/reference/policy.md#overview).",
            '<img src="docs/assets/other.gif">',
            '<a href="LICENSE">License</a>',
            "[![PyPI version](https://img.shields.io/pypi/v/maida-ai.svg)]"
            "(https://pypi.org/project/maida-ai/)",
        ]
    )
    rewritten = _HOOK.rewrite_readme_links(content, BASE)
    assert (
        "https://github.com/maida-ai/maida/blob/v0.5.0/docs/assets/demo.gif?raw=True"
        in rewritten
    )
    assert (
        "https://github.com/maida-ai/maida/blob/v0.5.0/docs/reference/policy.md#overview"
        in rewritten
    )
    assert (
        'src="https://github.com/maida-ai/maida/blob/v0.5.0/docs/assets/other.gif?raw=True"'
        in rewritten
    )
    assert 'href="https://github.com/maida-ai/maida/blob/v0.5.0/LICENSE"' in rewritten
    assert "https://img.shields.io/pypi/v/maida-ai.svg" in rewritten
    assert "https://pypi.org/project/maida-ai/" in rewritten


def test_readme_base_url_uses_main_for_dev_versions() -> None:
    assert _HOOK.readme_base_url("0.5.0.dev3") == (
        "https://github.com/maida-ai/maida/blob/main/"
    )
    assert _HOOK.readme_base_url("0.5.0") == (
        "https://github.com/maida-ai/maida/blob/v0.5.0/"
    )
