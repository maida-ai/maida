"""Regenerate the deterministic decision-rule calibration table."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from maida.calibration import calibrate_decision_rule, render_calibration_markdown


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replications", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=187)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    args = parser.parse_args()

    cells = calibrate_decision_rule(
        replications=args.replications,
        seed=args.seed,
    )
    if args.json_out is not None:
        args.json_out.write_text(
            json.dumps(
                {
                    "seed": args.seed,
                    "replications_per_cell": args.replications,
                    "model_calls": 0,
                    "cells": [cell.to_dict() for cell in cells],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    rendered = render_calibration_markdown(cells)
    if args.markdown_out is not None:
        args.markdown_out.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
