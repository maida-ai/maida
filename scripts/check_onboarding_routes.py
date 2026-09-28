#!/usr/bin/env python3
"""Check the public entry route against the released engine contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

CANONICAL_ROUTE = "https://maida.ai/docs/getting-started/"
ENTRY_POINTS = (
    "maida/README.md",
    "maida-assert/README.md",
    "maida-tutorials/README.md",
    "skills/README.md",
    "skills/product/README.md",
    "Demos/README.md",
    ".github/profile/README.md",
    "maida-ts/README.md",
    "opencode-plugin/README.md",
    "maida-workflows/README.md",
    "maida-heal/README.md",
    "maida-assert-smoke/README.md",
    "skills/product/maida-instrument-agent/SKILL.md",
    "skills/product/maida-add-regression-gate/SKILL.md",
    "skills/product/maida-debug-gate/SKILL.md",
    "maida-ai.github.io/templates/sections/home/workflow.html",
    "maida-ai.github.io/docs/index.md",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    contract = json.loads((args.workspace / "maida/contracts/current-main.json").read_text())
    release = contract["engine_ref"].removeprefix("v")
    errors = []
    for relative in ENTRY_POINTS:
        path = args.workspace / relative
        if not path.is_file():
            errors.append(f"{relative}: missing entry point")
            continue
        text = path.read_text(encoding="utf-8")
        expected = "/docs/getting-started/" if "/templates/" in relative else CANONICAL_ROUTE
        if relative.endswith("maida-ai.github.io/docs/index.md"):
            expected = 'href="getting-started/"'
        if expected not in text:
            errors.append(f"{relative}: missing canonical first-task route")
        # Inspection commands belong after the regression story, if shown at all.
        first_demo = re.search(r"maida demo(?: --[a-z-]+)?", text)
        if first_demo and first_demo.group() != "maida demo --regression":
            errors.append(f"{relative}: first demo is not the regression story")
        installs = re.findall(r'uv tool install ["\']maida-ai==([^"\']+)', text)
        if any(version != release for version in installs):
            errors.append(f"{relative}: standalone install differs from released {release}")
    for error in errors:
        print(error)
    if errors:
        return 1
    print(f"{len(ENTRY_POINTS)} public entry points share the released {release} route")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
