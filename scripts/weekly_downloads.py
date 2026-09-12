"""Collect public PyPI aggregates; do not infer Action installs from proxies."""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import urlopen

SOURCE = "https://pypistats.org/api/packages/maida-ai/overall?mirrors=false"


def fetch_stats() -> dict:
    with urlopen(SOURCE, timeout=30) as response:
        return json.load(response)


def weekly_rows(payload: dict, ending: date, weeks: int) -> list[dict]:
    """Only count weeks with seven explicit daily observations (UTC, Mon-Sun)."""
    if ending.weekday() != 6 or not 1 <= weeks <= 25:
        raise ValueError("Choose a Sunday and between 1 and 25 weeks")
    if not isinstance(payload, dict) or payload.get("package") != "maida-ai":
        raise ValueError("Unexpected package in PyPI response")
    if payload.get("type") != "overall_downloads" or not isinstance(
        payload.get("data"), list
    ):
        raise ValueError("Unexpected PyPI response shape")
    daily = {}
    for entry in payload["data"]:
        if not isinstance(entry, dict):
            raise ValueError("Invalid daily observation")
        if entry.get("category") != "without_mirrors":
            continue
        day = date.fromisoformat(entry.get("date", ""))
        count = entry.get("downloads")
        if type(count) is not int or count < 0:
            raise ValueError("Invalid download count")
        if day in daily:
            raise ValueError("Unexpected duplicate daily observation")
        daily[day] = count
    rows = []
    for offset in reversed(range(weeks)):
        end = ending - timedelta(weeks=offset)
        start = end - timedelta(days=6)
        days = [start + timedelta(days=i) for i in range(7)]
        observed = sum(day in daily for day in days)
        rows.append(
            {
                "week_start": start.isoformat(),
                "week_end": end.isoformat(),
                "pypi_downloads": sum(daily[d] for d in days) if observed == 7 else "",
                "observed_days": observed,
                "pypi_status": "complete" if observed == 7 else "incomplete",
                "marketplace_installs": "",
                "marketplace_status": "unavailable",
                "source": SOURCE,
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("_ai_report/weekly-downloads.csv")
    )
    parser.add_argument(
        "--week-ending",
        type=date.fromisoformat,
        help="UTC Sunday (default: last completed Sunday)",
    )
    parser.add_argument("--weeks", type=int, default=2)
    args = parser.parse_args(argv)
    today = datetime.now(timezone.utc).date()
    ending = args.week_ending or today - timedelta(days=today.weekday() + 1)
    if ending.weekday() != 6 or ending >= today or not 1 <= args.weeks <= 25:
        parser.error("Use a past Sunday and --weeks between 1 and 25")
    try:
        payload = fetch_stats()
        rows = weekly_rows(payload, ending, args.weeks)
        collected = datetime.now(timezone.utc).isoformat()
        for row in rows:
            row["collected_at"] = collected
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".source.json").write_text(
            json.dumps(
                {
                    "source": SOURCE,
                    "collected_at": collected,
                    "response": payload,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        args.output.write_text(buffer.getvalue(), encoding="utf-8")
    except (OSError, URLError, ValueError, TypeError) as exc:
        print(
            f"Could not collect weekly snapshot ({type(exc).__name__}); check source availability and output path, then retry.",
            file=sys.stderr,
        )
        return 1
    print(
        f"Wrote {args.output}. Marketplace installs unavailable; PyPI downloads are not installs.",
        file=sys.stderr,
    )
    if any(row["pypi_status"] != "complete" for row in rows):
        print(
            "Some weeks have missing daily observations; retry after the source updates or choose an earlier period.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
