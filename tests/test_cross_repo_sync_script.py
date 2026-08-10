"""Unit coverage for the read-only sibling repository sync checker."""

from __future__ import annotations

from pathlib import Path

from scripts.check_cross_repo_sync import compare_file, compare_tree


def test_compare_file_reports_missing_and_changed_consumers(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    consumer = tmp_path / "consumer.json"
    source.write_text('{"version": 1}\n', encoding="utf-8")

    assert compare_file(source, consumer) == [f"missing consumer file: {consumer}"]
    consumer.write_text('{"version": 2}\n', encoding="utf-8")
    assert compare_file(source, consumer) == [
        f"content differs: {source} != {consumer}"
    ]
    consumer.write_bytes(source.read_bytes())
    assert compare_file(source, consumer) == []


def test_compare_file_reports_a_missing_authoritative_source(tmp_path: Path) -> None:
    source = tmp_path / "missing-source.json"
    consumer = tmp_path / "consumer.json"
    consumer.write_text('{"version": 1}\n', encoding="utf-8")

    assert compare_file(source, consumer) == [f"missing source file: {source}"]


def test_compare_tree_requires_exact_file_set_and_content(tmp_path: Path) -> None:
    source = tmp_path / "source"
    consumer = tmp_path / "consumer"
    source.mkdir()
    consumer.mkdir()
    (source / "one.txt").write_text("one\n", encoding="utf-8")
    (consumer / "extra.txt").write_text("extra\n", encoding="utf-8")

    problems = compare_tree(source, consumer)

    assert f"missing consumer file: {consumer / 'one.txt'}" in problems
    assert f"unexpected consumer file: {consumer / 'extra.txt'}" in problems


def test_compare_tree_requires_both_owned_directories(tmp_path: Path) -> None:
    source = tmp_path / "source"
    consumer = tmp_path / "consumer"

    assert compare_tree(source, consumer) == [f"missing source directory: {source}"]
    source.mkdir()
    assert compare_tree(source, consumer) == [f"missing consumer directory: {consumer}"]
