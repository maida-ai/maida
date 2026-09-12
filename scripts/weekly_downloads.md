# Weekly public download snapshot

From the repository root:

```bash
uv run python scripts/weekly_downloads.py --output _ai_report/weekly-downloads.csv
```

This maintainer command reads public aggregate counts from PyPI Stats. It has no
credentials, sends no local run data, and adds no telemetry to the Maida CLI.
It writes two Monday–Sunday UTC periods ending on the last completed Sunday to
CSV, plus the source response and collection time in a sibling `.source.json`.
Choose a dated output filename to retain each collection. Existing output at the
same path is replaced. To select a historical period within source retention:

```bash
uv run python scripts/weekly_downloads.py --week-ending 2026-09-06 --weeks 2 --output _ai_report/weekly-downloads-20260906.csv
```

`pypi_downloads` excludes known mirrors. Downloads include repeated downloads and
CI activity; they do not measure installs, unique users, or successful setup.
[PyPI Stats documents](https://pypistats.org/api/) daily updates and 180-day
retention. This command requires seven explicit daily observations per week.
Missing days are unknown, even if they might represent zero downloads: the weekly
count remains blank and `pypi_status` is `incomplete`. A complete zero count is
valid. CSV dates and counts provide a weekly series suitable for plotting.

`marketplace_installs` is blank and `marketplace_status` is `unavailable`.
[GitHub Marketplace listing metrics](https://docs.github.com/en/apps/github-marketplace/creating-apps-for-github-marketplace/viewing-metrics-for-your-listing)
apply to GitHub Apps, not Actions. No documented Actions install-count endpoint
has been identified for this collector. Stars, code-search matches, and public
workflow references are not install counts and are not substituted. An
owner-approved data source or a separately defined usage metric is required to
complete that part of collection.

Exit 0 means all requested PyPI weeks have seven daily observations; it does not
mean Marketplace data exists. Exit 1 means a collection/output failure or an
incomplete PyPI period. Argument errors return 2. On incomplete weeks, inspect the
retained response and retry after the source updates or select another period.
On a fetch or validation error, existing output is preserved. CSV and source
writes are separate; check both artifacts after a filesystem error.

Run the command weekly with dated paths. A two-week baseline requires two complete
periods and independent confirmation that their dates precede the intended
comparison event. Historical data alone cannot establish that timing. No schedule,
publication, or event date is configured by this script.
