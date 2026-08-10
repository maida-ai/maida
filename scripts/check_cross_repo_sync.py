"""Compare the versioned contracts vendored across Maida repositories."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compare_file(source: Path, consumer: Path) -> list[str]:
    if not source.is_file():
        return [f"missing source file: {source}"]
    if not consumer.is_file():
        return [f"missing consumer file: {consumer}"]
    if _digest(source) != _digest(consumer):
        return [f"content differs: {source} != {consumer}"]
    return []


def compare_tree(source: Path, consumer: Path) -> list[str]:
    if not source.is_dir():
        return [f"missing source directory: {source}"]
    if not consumer.is_dir():
        return [f"missing consumer directory: {consumer}"]
    source_files = {
        path.relative_to(source): path for path in source.rglob("*") if path.is_file()
    }
    consumer_files = {
        path.relative_to(consumer): path
        for path in consumer.rglob("*")
        if path.is_file()
    }
    problems: list[str] = []
    for relative in sorted(source_files.keys() - consumer_files.keys()):
        problems.append(f"missing consumer file: {consumer / relative}")
    for relative in sorted(consumer_files.keys() - source_files.keys()):
        problems.append(f"unexpected consumer file: {consumer / relative}")
    for relative in sorted(source_files.keys() & consumer_files.keys()):
        problems.extend(compare_file(source_files[relative], consumer_files[relative]))
    return problems


def check_workspace(workspace: Path) -> list[str]:
    core = workspace / "maida"
    current_contract = core / "contracts" / "current-main.json"
    problems: list[str] = []
    for consumer in (
        workspace / "maida-assert" / "tests" / "contracts" / "current-main.json",
        workspace / "maida-ts" / "tests" / "contracts" / "current-main.json",
        workspace / "maida-ai.github.io" / "tests" / "contracts" / "current-main.json",
        workspace / "maida-tutorials" / "tests" / "contracts" / "current-main.json",
    ):
        problems.extend(compare_file(current_contract, consumer))

    problems.extend(
        compare_tree(
            core / "contracts" / "conformance",
            workspace / "maida-ts" / "tests" / "contracts" / "conformance",
        )
    )
    problems.extend(
        compare_tree(
            workspace / "maida-ts" / "tests" / "fixtures" / "traces",
            core / "tests" / "fixtures" / "traces" / "external" / "maida-ts",
        )
    )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="Directory containing maida and its sibling repositories",
    )
    args = parser.parse_args()
    problems = check_workspace(args.workspace.resolve())
    if problems:
        print("Cross-repository contract drift detected:")
        for problem in problems:
            print(f"- {problem}")
        return 1
    print("Cross-repository contracts and fixtures are synchronized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
