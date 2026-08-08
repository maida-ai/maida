from typer.testing import CliRunner

from maida.cli import app


def test_capture_claude_code_starts_loopback_receiver(monkeypatch, temp_data_dir):
    invocation = {}

    def fake_run(**kwargs):
        invocation.update(kwargs)

    monkeypatch.setattr("uvicorn.run", fake_run)
    result = CliRunner().invoke(
        app,
        ["capture", "claude-code", "--host", "127.0.0.1", "--port", "5318"],
    )

    assert result.exit_code == 0
    assert invocation["host"] == "127.0.0.1"
    assert invocation["port"] == 5318
    assert invocation["access_log"] is False
    assert invocation["log_level"] == "warning"
    assert invocation["app"].title == "Maida Claude Code capture"
    assert "Listening for Claude Code OTLP" in result.stderr


def test_capture_claude_code_rejects_invalid_port(temp_data_dir):
    result = CliRunner().invoke(
        app,
        ["capture", "claude-code", "--port", "70000"],
    )
    assert result.exit_code == 2
