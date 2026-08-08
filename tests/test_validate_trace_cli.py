import json
from pathlib import Path

from typer.testing import CliRunner

import maida.cli as cli
from maida.cli import app


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "tests"
    / "fixtures"
    / "traces"
    / "external"
    / "emitter"
    / "current"
    / "multithread"
)
runner = CliRunner()


def test_validate_trace_text_success_for_directory_and_meta() -> None:
    for path in (FIXTURE, FIXTURE / "meta.json"):
        result = runner.invoke(app, ["validate-trace", str(path)])

        assert result.exit_code == 0
        assert "Valid Maida trace 80000000" in result.stdout
        assert "0.2.0" in result.stdout
        assert "5 spans" in result.stdout
        assert result.stderr == ""


def test_validate_trace_json_success_is_machine_readable() -> None:
    result = runner.invoke(app, ["validate-trace", str(FIXTURE), "--json"])

    assert result.exit_code == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "diagnostics": [],
        "span_count": 5,
        "spec_version": "0.2.0",
        "status": "ok",
        "trace_id": "80000000000000000000000000000001",
        "valid": True,
    }


def test_validate_trace_invalid_content_exits_one_without_leaking(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text(
        '{"secret":"sk-test-DO-NOT-LEAK"', encoding="utf-8"
    )
    (run_dir / "spans.jsonl").write_text("{}\n", encoding="utf-8")

    result = runner.invoke(app, ["validate-trace", str(run_dir)])

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Invalid Maida trace" in result.stderr
    assert "meta.json" in result.stderr
    assert "malformed JSON" in result.stderr
    assert "sk-test-DO-NOT-LEAK" not in result.stderr


def test_validate_trace_json_invalid_content_has_structured_diagnostic(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "meta.json").write_text("{}", encoding="utf-8")
    (run_dir / "spans.jsonl").write_text("{}\n", encoding="utf-8")

    result = runner.invoke(app, ["validate-trace", str(run_dir), "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 1
    assert result.stderr == ""
    assert payload["valid"] is False
    assert payload["span_count"] == 1
    assert payload["diagnostics"]
    assert set(payload["diagnostics"][0]) == {"code", "location", "message"}


def test_validate_trace_missing_input_exits_two_in_json_mode(tmp_path: Path) -> None:
    result = runner.invoke(app, ["validate-trace", str(tmp_path / "missing"), "--json"])

    payload = json.loads(result.stdout)
    assert result.exit_code == 2
    assert result.stderr == ""
    assert payload["valid"] is False
    assert payload["diagnostics"][0]["code"] == "path_not_found"


def test_validate_trace_internal_failure_exits_ten(monkeypatch) -> None:
    def fail(_path):
        raise RuntimeError("sk-test-DO-NOT-LEAK")

    monkeypatch.setattr(cli, "validate_trace_path", fail)

    result = runner.invoke(app, ["validate-trace", str(FIXTURE), "--json"])

    assert result.exit_code == 10
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "diagnostics": [
            {
                "code": "internal_error",
                "location": "trace",
                "message": "trace validation failed unexpectedly",
            }
        ],
        "span_count": None,
        "spec_version": None,
        "status": None,
        "trace_id": None,
        "valid": False,
    }
    assert "sk-test-DO-NOT-LEAK" not in result.stdout
