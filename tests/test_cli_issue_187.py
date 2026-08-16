"""CLI contracts introduced with tier-aware report v2."""

from __future__ import annotations

import json
from types import SimpleNamespace

from typer.testing import CliRunner

from maida.cli import app
from maida.schema_versions import BASELINE_SCHEMA_VERSION
from maida.statistics import GateVerdict


runner = CliRunner()


def _report_payload() -> dict:
    signature = {
        "tool_path": [],
        "tool_call_sequence": [],
        "tool_call_counts": {},
        "llm_models_used": [],
        "event_type_sequence": ["RUN_START", "RUN_END"],
        "final_status": "ok",
    }
    return {
        "report_version": "2.0.0",
        "metadata": {
            "trials_used": 1,
            "trials_budgeted": 1,
            "environment_fingerprint": {"workspace": "abc"},
        },
        "trials": [
            {
                "trace_id": "1" * 32,
                "run_name": "agent",
                "metric_values": {
                    "step_count": 0,
                    "tool_call_count": 0,
                    "cost_tokens": 0,
                    "latency_ms": 1,
                },
                "invariant_outcomes": {"stop_condition_reached": True},
                "structural_signature": signature,
            }
        ],
    }


def test_baseline_cli_binds_full_report_sample(tmp_path) -> None:
    report_path = tmp_path / "report.json"
    baseline_path = tmp_path / "baseline.json"
    report_path.write_text(json.dumps(_report_payload()), encoding="utf-8")
    result = runner.invoke(
        app,
        [
            "baseline",
            "--from-report",
            str(report_path),
            "--out",
            str(baseline_path),
        ],
    )
    assert result.exit_code == 0, result.output
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert baseline["schema_version"] == BASELINE_SCHEMA_VERSION
    assert baseline["trial_sample"]["environment_fingerprint"] == {"workspace": "abc"}


def test_gate_exit_code_contract_keeps_inconclusive_neutral(
    tmp_path, monkeypatch
) -> None:
    script = tmp_path / "agent.py"
    script.write_text("pass\n", encoding="utf-8")

    def fake(verdict: GateVerdict):
        return SimpleNamespace(
            verdict=verdict,
            to_text=lambda: verdict.value,
            to_json=lambda: "{}",
            to_markdown=lambda: verdict.value,
        )

    monkeypatch.setattr(
        "maida.cli.run_trials", lambda *args, **kwargs: fake(GateVerdict.INCONCLUSIVE)
    )
    neutral = runner.invoke(app, ["run", str(script)])
    assert neutral.exit_code == 0

    monkeypatch.setattr(
        "maida.cli.run_trials", lambda *args, **kwargs: fake(GateVerdict.FAIL)
    )
    failed = runner.invoke(app, ["run", str(script)])
    assert failed.exit_code == 1

    missing = runner.invoke(app, ["run", str(tmp_path / "missing.py")])
    assert missing.exit_code == 2

    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("maida.cli.run_trials", explode)
    internal = runner.invoke(app, ["run", str(script)])
    assert internal.exit_code == 10


def test_run_markdown_forwards_baseline_path(tmp_path, monkeypatch) -> None:
    script = tmp_path / "agent.py"
    script.write_text("pass\n", encoding="utf-8")
    baseline_path = tmp_path / "accepted-baseline.json"
    seen: dict[str, str | None] = {}

    class FakeReport:
        verdict = GateVerdict.PASS

        def to_markdown(self, baseline_path: str | None = None) -> str:
            seen["baseline_path"] = baseline_path
            return "markdown"

    monkeypatch.setattr("maida.cli.load_baseline", lambda path: {"path": str(path)})
    monkeypatch.setattr("maida.cli.run_trials", lambda *args, **kwargs: FakeReport())

    result = runner.invoke(
        app,
        [
            "run",
            str(script),
            "--baseline",
            str(baseline_path),
            "--format",
            "markdown",
        ],
    )

    assert result.exit_code == 0, result.output
    assert seen == {"baseline_path": str(baseline_path)}
