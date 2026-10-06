"""Offline langfuse langfuse tests."""

from __future__ import annotations
from tests.support.paths import FIXTURES_ROOT
import json
from typer.testing import CliRunner
from maida.baseline import create_baseline
from maida.cli import app
from maida.config import load_config
from maida.integrations.langfuse import (
    LangfuseClient,
)
from maida.storage import list_runs, load_validated_run
from tests.support.langfuse import _observation


runner = CliRunner()

FIXTURE_PATH = FIXTURES_ROOT / "langfuse" / "api-v2" / "observations.json"


def test_cli_imports_and_idempotently_skips_trace(monkeypatch, temp_data_dir):
    rows = [_observation("tool-1")]

    def fake_fetch(_self, params):
        return rows if params.get("traceId") else [rows[0]]

    monkeypatch.setattr(LangfuseClient, "fetch_observations", fake_fetch)
    env = {
        "LANGFUSE_PUBLIC_KEY": "public-key",
        "LANGFUSE_SECRET_KEY": "secret-key",
    }
    args = ["import", "langfuse", "--trace-id", "source-trace-good", "--json"]

    first = runner.invoke(app, args, env=env)
    second = runner.invoke(app, args, env=env)

    assert first.exit_code == 0, first.output
    first_payload = json.loads(first.stdout)
    assert len(first_payload["imported"]) == 1
    assert first_payload["skipped"] == []
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["skipped"][0]["reason"] == "already imported"

    config = load_config()
    runs = list_runs(10, config)
    assert len(runs) == 1
    trace_id = runs[0]["trace_id"]
    load_validated_run(trace_id, config)
    baseline = create_baseline(trace_id, config)
    assert baseline["source_run_name"] == "nightly-support-job"


def test_cli_missing_credentials_is_input_error(temp_data_dir):
    result = runner.invoke(
        app,
        ["import", "langfuse", "--trace-id", "source-trace-good"],
        env={"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""},
    )

    assert result.exit_code == 2
    assert "LANGFUSE_PUBLIC_KEY" in result.stderr
    assert "secret-key" not in result.output


def test_cli_json_errors_remain_machine_readable(temp_data_dir):
    result = runner.invoke(
        app,
        ["import", "langfuse", "--trace-id", "source-trace-good", "--json"],
        env={"LANGFUSE_PUBLIC_KEY": "", "LANGFUSE_SECRET_KEY": ""},
    )

    assert result.exit_code == 2
    payload = json.loads(result.stdout)
    assert payload["error"]["kind"] == "invalid_input"
    assert "LANGFUSE_PUBLIC_KEY" in payload["error"]["message"]
