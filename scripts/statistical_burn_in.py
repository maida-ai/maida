"""Measure the zero-token Maida gate burn-in.

Exit zero when measurement completes, even if individual gates fail. Inspect the
reported verdicts and rates; completion is not a statistical acceptance guarantee.
Invalid settings, execution failures, and exceeded wall-time budgets exit nonzero.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from maida.burn_in import run_burn_in


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gates", type=int, default=50)
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument("--seed", type=int, default=137)
    parser.add_argument("--pass-probability", type=float, default=0.99)
    parser.add_argument("--max-wall-time-seconds", type=float, default=600.0)
    parser.add_argument("--json-out", type=Path)
    args = parser.parse_args()

    report = run_burn_in(
        gates=args.gates,
        trials_per_gate=args.trials,
        seed=args.seed,
        pass_probability=args.pass_probability,
        max_wall_time_seconds=args.max_wall_time_seconds,
    )
    print(report.to_text())
    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(report.to_json() + "\n", encoding="utf-8")
    # Completion means the measurement succeeded; verdicts remain in the report.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
