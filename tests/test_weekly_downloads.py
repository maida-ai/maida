"""Offline checks for weekly public download aggregates."""

import csv
from datetime import date, timedelta
from urllib.error import URLError

import pytest

from scripts import weekly_downloads as snapshot


def payload(days=14):
    return {
        "package": "maida-ai",
        "type": "overall_downloads",
        "data": [
            {
                "date": (date(2026, 8, 24) + timedelta(days=i)).isoformat(),
                "category": "without_mirrors",
                "downloads": i,
            }
            for i in range(days)
        ],
    }


def test_two_complete_weeks_exclude_mirrors():
    data = payload()
    data["data"].append(
        {"date": "2026-08-24", "category": "with_mirrors", "downloads": 999}
    )
    rows = snapshot.weekly_rows(data, date(2026, 9, 6), 2)
    assert [r["pypi_downloads"] for r in rows] == [21, 70]
    assert all(r["pypi_status"] == "complete" for r in rows)
    assert all(r["marketplace_installs"] == "" for r in rows)
    assert all(r["marketplace_status"] == "unavailable" for r in rows)


def test_missing_day_is_unknown_not_zero():
    rows = snapshot.weekly_rows(payload(13), date(2026, 9, 6), 2)
    assert rows[1]["pypi_downloads"] == ""
    assert rows[1]["observed_days"] == 6
    assert rows[1]["pypi_status"] == "incomplete"


@pytest.mark.parametrize("count", [-1, True, "4", 1.5])
def test_invalid_counts_rejected(count):
    data = payload()
    data["data"][0]["downloads"] = count
    with pytest.raises(ValueError):
        snapshot.weekly_rows(data, date(2026, 9, 6), 2)


def test_duplicate_date_and_wrong_package_rejected():
    data = payload()
    data["data"].append(data["data"][0])
    with pytest.raises(ValueError, match="duplicate"):
        snapshot.weekly_rows(data, date(2026, 9, 6), 2)
    data = payload()
    data["package"] = "other"
    with pytest.raises(ValueError, match="package"):
        snapshot.weekly_rows(data, date(2026, 9, 6), 2)


def test_cli_writes_csv_and_retains_raw_source(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "fetch_stats", payload)
    output = tmp_path / "snapshot.csv"
    assert snapshot.main(["--week-ending", "2026-09-06", "--output", str(output)]) == 0
    rows = list(csv.DictReader(output.open()))
    assert rows[1]["pypi_downloads"] == "70"
    assert output.with_suffix(".source.json").exists()


def test_transport_failure_leaves_existing_snapshot(tmp_path, monkeypatch, capsys):
    def fail():
        raise URLError("offline")

    monkeypatch.setattr(snapshot, "fetch_stats", fail)
    output = tmp_path / "snapshot.csv"
    output.write_text("previous")
    assert snapshot.main(["--output", str(output)]) == 1
    assert output.read_text() == "previous"
    assert "Could not collect" in capsys.readouterr().err


def test_non_sunday_rejected_before_network(monkeypatch):
    monkeypatch.setattr(snapshot, "fetch_stats", lambda: pytest.fail("network"))
    with pytest.raises(SystemExit):
        snapshot.main(["--week-ending", "2026-09-05"])


def test_incomplete_cli_writes_unknown_and_returns_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot, "fetch_stats", lambda: payload(13))
    output = tmp_path / "snapshot.csv"
    assert snapshot.main(["--week-ending", "2026-09-06", "--output", str(output)]) == 1
    with output.open() as handle:
        rows = list(csv.DictReader(handle))
    assert rows[1]["pypi_status"] == "incomplete"
    assert rows[1]["pypi_downloads"] == ""


def test_fetch_uses_fixed_public_url_and_timeout(monkeypatch):
    import io
    import json

    calls = []

    def open_url(url, *, timeout):
        calls.append((url, timeout))
        return io.StringIO(json.dumps(payload()))

    monkeypatch.setattr(snapshot, "urlopen", open_url)
    assert snapshot.fetch_stats() == payload()
    assert calls == [(snapshot.SOURCE, 30)]


@pytest.mark.parametrize(
    "data",
    [
        {"package": "maida-ai", "data": []},
        {"package": "maida-ai", "type": "overall_downloads", "data": [3]},
    ],
)
def test_malformed_payload_rejected(data):
    with pytest.raises(ValueError):
        snapshot.weekly_rows(data, date(2026, 9, 6), 2)
