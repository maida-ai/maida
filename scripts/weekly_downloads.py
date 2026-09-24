"""Collect public PyPI and npm aggregates; do not infer Action installs from proxies."""

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
    if payload.get("type") != "overall_downloads" or not isinstance(payload.get("data"), list):
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
    return summarize_days(daily, ending, weeks, "pypi")


def summarize_days(daily: dict, ending: date, weeks: int, registry: str) -> list[dict]:
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
                f"{registry}_downloads": sum(daily[d] for d in days) if observed == 7 else "",
                f"{registry}_observed_days": observed,
                f"{registry}_status": "complete" if observed == 7 else "incomplete",
            }
        )
    return rows


def npm_source(ending: date, weeks: int) -> str:
    start = ending - timedelta(days=weeks * 7 - 1)
    return f"https://api.npmjs.org/downloads/range/{start}:{ending}/@maida-ai/core"


def fetch_npm_stats(ending: date, weeks: int) -> dict:
    with urlopen(npm_source(ending, weeks), timeout=30) as response:
        return json.load(response)


def npm_weekly_rows(payload: dict, ending: date, weeks: int) -> list[dict]:
    if not isinstance(payload, dict) or payload.get("package") != "@maida-ai/core":
        raise ValueError("Unexpected package in npm response")
    start = ending - timedelta(days=weeks * 7 - 1)
    if (
        payload.get("start") != start.isoformat()
        or payload.get("end") != ending.isoformat()
        or not isinstance(payload.get("downloads"), list)
    ):
        raise ValueError("Unexpected npm response range or shape")
    daily = {}
    for entry in payload["downloads"]:
        if not isinstance(entry, dict):
            raise ValueError("Invalid npm daily observation")
        day = date.fromisoformat(entry.get("day", ""))
        count = entry.get("downloads")
        if type(count) is not int or count < 0 or not start <= day <= ending:
            raise ValueError("Invalid npm daily count or date")
        if day in daily:
            raise ValueError("Unexpected duplicate npm daily observation")
        daily[day] = count
    return summarize_days(daily, ending, weeks, "npm")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("_ai_report/weekly-downloads.csv"))
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
    rows = summarize_days({}, ending, args.weeks, "pypi")
    evidence = {}
    successes = 0
    for registry, source, fetch, aggregate in [
        ("pypi", SOURCE, fetch_stats, weekly_rows),
        (
            "npm",
            npm_source(ending, args.weeks),
            lambda: fetch_npm_stats(ending, args.weeks),
            npm_weekly_rows,
        ),
    ]:
        evidence[registry] = {"source": source}
        try:
            payload = fetch()
            evidence[registry]["response"] = payload
            values = aggregate(payload, ending, args.weeks)
            successes += 1
        except (OSError, URLError, ValueError, TypeError) as exc:
            evidence[registry]["error"] = type(exc).__name__
            print(
                f"Could not collect {registry} ({type(exc).__name__}); check source availability and retry.",
                file=sys.stderr,
            )
            values = summarize_days({}, ending, args.weeks, registry)
            for row in values:
                row[f"{registry}_status"] = "unavailable"
        for row, value in zip(rows, values):
            row.update(value)
            row[f"{registry}_source"] = source
    if not successes:
        return 1
    try:
        collected = datetime.now(timezone.utc).isoformat()
        for row in rows:
            row["collected_at"] = collected
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.with_suffix(".source.json").write_text(
            json.dumps({"collected_at": collected, "sources": evidence}, indent=2) + "\n",
            encoding="utf-8",
        )
        args.output.write_text(buffer.getvalue(), encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        print(
            f"Could not write weekly snapshot ({type(exc).__name__}); check output path and retry.",
            file=sys.stderr,
        )
        return 1

    print(
        f"Wrote {args.output}. PyPI and npm downloads are separate counts, not installs.",
        file=sys.stderr,
    )
    if any(row[f"{registry}_status"] != "complete" for row in rows for registry in ("pypi", "npm")):
        print(
            "Some weeks have missing daily observations; retry after the source updates or choose an earlier period.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
