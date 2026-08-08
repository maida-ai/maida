import hashlib
import json
import shutil
from pathlib import Path

from typer.testing import CliRunner

from maida.cli import app


FIXTURES = Path(__file__).parent / "fixtures" / "traces" / "claude-code" / "2.1.220"


def _install_fixture(name: str, session_id: str, data_dir: Path) -> Path:
    session_hash = hashlib.sha256(session_id.encode()).hexdigest()
    destination = data_dir / "captures" / "claude-code" / session_hash / "0001"
    shutil.copytree(FIXTURES / name, destination)
    return destination


def test_cli_import_json_and_idempotent_notice(temp_data_dir):
    _install_fixture("normal", "fixture-normal", temp_data_dir)
    runner = CliRunner()

    first = runner.invoke(
        app, ["import", "claude-code", "--session-id", "fixture-normal", "--json"]
    )
    assert first.exit_code == 0
    payload = json.loads(first.stdout)
    assert payload["imported"] is True
    assert len(payload["trace_id"]) == 32
    assert "Using Claude Code capture segment: 0001" in first.stderr

    second = runner.invoke(
        app, ["import", "claude-code", "--session-id", "fixture-normal", "--json"]
    )
    assert second.exit_code == 0
    assert json.loads(second.stdout)["imported"] is False


def test_cli_import_explicit_segment_text_output(temp_data_dir):
    _install_fixture("normal", "fixture-normal", temp_data_dir)
    result = CliRunner().invoke(
        app,
        [
            "import",
            "claude-code",
            "--session-id",
            "fixture-normal",
            "--segment",
            "0001",
        ],
    )
    assert result.exit_code == 0
    assert "Imported Claude Code capture" in result.stdout
    assert "Using Claude Code capture segment" not in result.stderr


def test_cli_import_missing_and_malformed_are_exit_two(temp_data_dir):
    malformed = _install_fixture("malformed", "fixture-malformed", temp_data_dir)
    runner = CliRunner()
    missing = runner.invoke(
        app, ["import", "claude-code", "--session-id", "../../missing", "--json"]
    )
    assert missing.exit_code == 2
    assert json.loads(missing.stdout)["error"]["kind"] == "invalid_capture"

    result = runner.invoke(
        app,
        ["import", "claude-code", "--session-id", "fixture-malformed", "--json"],
    )
    assert result.exit_code == 2
    assert json.loads(result.stdout)["error"]["kind"] == "invalid_capture"
    assert malformed.is_dir()
