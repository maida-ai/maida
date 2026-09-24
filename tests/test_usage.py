import json

import pytest
from fastapi.testclient import TestClient

from maida import usage


def test_disabled_does_not_open_network(monkeypatch):
    monkeypatch.delenv("MAIDA_USAGE_OPT_IN", raising=False)
    monkeypatch.setattr(usage, "build_opener", lambda *a: pytest.fail("network"))
    assert usage.report_usage("pass") == "Usage ping: disabled"


@pytest.mark.parametrize("verdict", ["pass", "fail", "inconclusive"])
def test_opt_in_minimal_payload(monkeypatch, verdict):
    monkeypatch.setenv("MAIDA_USAGE_OPT_IN", "1")
    monkeypatch.setenv("MAIDA_USAGE_ENDPOINT", "https://collector.example/usage")
    monkeypatch.setenv("MAIDA_USAGE_REPO_ID", "a" * 64)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    captured = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            return b""

    class Sender:
        def open(self, request, timeout):
            assert timeout == 1
            captured.append(json.loads(request.data))
            return Response()

    monkeypatch.setattr(usage, "build_opener", lambda *a: Sender())
    assert usage.report_usage(verdict) == "Usage ping: opted in; sent"
    payload = captured[0]
    assert set(payload) == {
        "schema_version",
        "version",
        "repo_id",
        "date",
        "pr",
        "verdict_counts",
    }
    assert payload["verdict_counts"][verdict] == 1
    assert sum(payload["verdict_counts"].values()) == 1
    assert payload["pr"] is True


def test_bad_configuration_is_nonfatal(monkeypatch):
    monkeypatch.setenv("MAIDA_USAGE_OPT_IN", "1")
    monkeypatch.setenv("MAIDA_USAGE_ENDPOINT", "http://secret.example")
    monkeypatch.setenv("MAIDA_USAGE_REPO_ID", "private/repo")
    assert usage.report_usage("fail") == "Usage ping: opted in; unavailable"


def test_receiver_disabled_and_append_only(tmp_path):
    log = tmp_path / "usage.jsonl"
    payload = usage.make_payload("pass", "a" * 64, False)
    disabled = TestClient(usage.create_receiver(log))
    assert disabled.post("/usage", json=payload).status_code == 503
    assert not log.exists()
    client = TestClient(usage.create_receiver(log, enabled=True))
    for _ in range(2):
        assert client.post("/usage", json=payload).status_code == 204
    assert len(log.read_text().splitlines()) == 2
    assert client.get("/usage").status_code == 405
    assert client.delete("/usage").status_code == 405
    assert client.post("/usage", json={**payload, "prompt": "secret"}).status_code == 422
    assert len(log.read_text().splitlines()) == 2


@pytest.mark.parametrize("raw", [b"{", b"null", b"x" * 2049, b"[" * 1000 + b"]" * 1000])
def test_receiver_rejects_bad_body(tmp_path, raw):
    log = tmp_path / "usage.jsonl"
    client = TestClient(usage.create_receiver(log, enabled=True))
    assert client.post("/usage", content=raw).status_code in {413, 422}
    assert not log.exists()


def test_network_failure_is_nonfatal(monkeypatch):
    monkeypatch.setenv("MAIDA_USAGE_OPT_IN", "1")
    monkeypatch.setenv("MAIDA_USAGE_ENDPOINT", "https://collector.example/usage")
    monkeypatch.setenv("MAIDA_USAGE_REPO_ID", "a" * 64)

    def broken(*args):
        raise OSError("sensitive connection details")

    monkeypatch.setattr(usage, "build_opener", broken)
    assert usage.report_usage("inconclusive") == "Usage ping: opted in; unavailable"


@pytest.mark.parametrize("max_steps,exit_code", [(10, 0), (0, 1)])
def test_gate_state_on_stderr_and_json_clean(temp_data_dir, monkeypatch, max_steps, exit_code):
    from maida import traced_run, record_tool_call
    from maida.cli import app
    from typer.testing import CliRunner

    monkeypatch.delenv("MAIDA_USAGE_OPT_IN", raising=False)
    with traced_run(name="usage-test"):
        record_tool_call("test", args={}, result=None)
    result = CliRunner().invoke(app, ["assert", "--max-steps", str(max_steps), "--format", "json"])
    assert result.exit_code == exit_code
    assert json.loads(result.stdout)["passed"] is (exit_code == 0)
    assert "Usage ping: disabled" in result.stderr


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", True),
        ("repo_id", "private/repo"),
        ("version", ""),
        ("date", "2026-W01-1"),
        ("date", "2026-02-30"),
        ("pr", 1),
        ("verdict_counts", {"pass": True, "fail": 0, "inconclusive": 0}),
        ("verdict_counts", {"pass": 1, "fail": 1, "inconclusive": 0}),
    ],
)
def test_receiver_schema_rejects_invalid_values(tmp_path, field, value):
    payload = usage.make_payload("pass", "a" * 64, False)
    payload[field] = value
    log = tmp_path / "usage.jsonl"
    client = TestClient(usage.create_receiver(log, enabled=True))
    assert client.post("/usage", json=payload).status_code == 422
    assert not log.exists()


def test_published_schema_matches_payloads():
    from pathlib import Path
    from jsonschema import Draft202012Validator, FormatChecker

    schema = json.loads((Path(__file__).parents[1] / "schemas/usage-ping.v1.schema.json").read_text())
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    for verdict in ("pass", "fail", "inconclusive"):
        payload = usage.make_payload(verdict, "a" * 64, False)
        validator.validate(payload)
        assert usage._validate_payload(payload)


def test_redirects_are_rejected():
    assert usage._NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.example") is None
