# Weekly public download snapshot

From the repository root:

```bash
uv run python scripts/weekly_downloads.py --output _ai_report/weekly-downloads.csv
```

This maintainer command reads public aggregate download counts for PyPI
`maida-ai` and npm `@maida-ai/core`. It has no credentials, sends no local run
data, and adds no telemetry to the Maida CLI. It writes two Monday–Sunday UTC
periods ending on the last completed Sunday to CSV, plus source responses and
collection time in a sibling `.source.json`. Use dated output filenames to retain
weekly collections; existing output at the same path is replaced.

```bash
uv run python scripts/weekly_downloads.py --week-ending 2026-09-06 --weeks 2 --output _ai_report/weekly-downloads-20260906.csv
```

The registries have separate download, observed-day, status, and source columns.
Do not add their counts together to estimate users: downloads can include repeated
fetches and CI activity. They do not measure installs, unique users, successful
setup, or active weekly use.

`pypi_downloads` excludes known mirrors.
[PyPI Stats documents](https://pypistats.org/api/) daily updates and 180-day
retention. The npm collector uses the official
[npm download range API](https://github.com/npm/registry/blob/main/docs/download-counts.md)
for daily counts over the same inclusive dates. Each weekly total requires seven
explicit daily observations. Missing days are unknown, even if they might
represent zero downloads: the affected count stays blank with `incomplete`
status. Seven explicit zeros produce a complete zero count. Dates and counts
form weekly series suitable for plotting.

A fetch or validation failure marks only that registry `unavailable`; the other
registry's valid observations and both source outcomes are retained. If both
sources fail, existing output is preserved. Source errors record only the error
class, not response bodies or environment details. Exit 0 means all requested
weeks from both registries have seven observations; exit 1 indicates incomplete
or unavailable data or an output failure. Argument errors return 2. Inspect the
source JSON and retry after the source updates or choose an earlier period.
CSV and source writes are separate; check both after a filesystem error.

Private voluntary check-ins and the owner's private weekly sheet are maintained
separately. This command neither reads nor writes that sheet and collects no
check-in identities. It does not infer GitHub Actions installs from Marketplace
stars or code-search matches.

Run weekly with dated output paths. A two-week baseline requires two complete
periods and independent confirmation that their dates precede the intended
comparison event. Historical data alone cannot establish that timing. No
schedule, publication, or event date is configured by this script.
