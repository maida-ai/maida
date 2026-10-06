"""CLI gating behavior."""

import json
from hashlib import sha256
import pytest
from typer.testing import CliRunner
from maida import record_tool_call, traced_run
from maida.cli import app
from maida.config import load_config
from maida.events import EventType
from maida.schema_versions import BASELINE_SCHEMA_VERSION
from tests.support.runs import get_latest_run_id
from tests.support.cli import _make_run, _write_run_with_malformed_span, _write_trace_run


runner = CliRunner()


def test_baseline_creates_file(empty_data_dir):
    config = load_config()
    with traced_run(name="baseline_test"):
        record_tool_call("search", args={}, result=None)
    run_id = get_latest_run_id(config)

    out = empty_data_dir / "bl.json"
    result = runner.invoke(app, ["baseline", run_id, "--out", str(out)])
    assert result.exit_code == 0
    assert out.is_file()
    data = json.loads(out.read_text())
    assert data["source_run_id"] == run_id
    assert "summary" in data
    assert data["summary"]["tool_calls"] == 1


@pytest.mark.parametrize("from_report", [False, True])
def test_baseline_overwrite_requires_force(empty_data_dir, from_report):
    config = load_config()
    with traced_run(name="overwrite_test"):
        record_tool_call("search", args={}, result=None)
    run_id = get_latest_run_id(config)
    args = [run_id]
    if from_report:
        from maida.schema_versions import REPORT_SCHEMA_VERSION

        report = empty_data_dir / "report.json"
        report.write_text(
            json.dumps(
                {
                    "report_version": REPORT_SCHEMA_VERSION,
                    "metadata": {},
                    "trials": [
                        {
                            "trace_id": run_id,
                            "metric_values": {"step_count": 1},
                            "structural_signature": {},
                            "invariant_outcomes": {},
                        }
                    ],
                }
            )
        )
        args = ["--from-report", str(report)]
    out = empty_data_dir / "bl.json"
    original = b'{"reviewed": true}\n'
    out.write_bytes(original)

    result = runner.invoke(app, ["baseline", *args, "--out", str(out)])

    assert result.exit_code == 2
    assert "--force" in result.stderr
    assert "already exists" in result.stderr
    assert result.stdout == ""
    assert out.read_bytes() == original

    result = runner.invoke(app, ["baseline", *args, "--out", str(out), "--force"])

    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text())["source_run_id"] == run_id


def test_baseline_missing_run_exit_two(empty_data_dir):
    result = runner.invoke(app, ["baseline", "missing_run", "--out", str(empty_data_dir / "bl.json")])
    assert result.exit_code == 2


def test_accept_updates_baseline_with_metadata_and_diff(empty_data_dir):
    config = load_config()
    baseline_run = _make_run(
        config,
        name="agent",
        events=[(EventType.TOOL_CALL, "search", {})],
    )
    baseline_path = empty_data_dir / "baseline.json"
    result = runner.invoke(app, ["baseline", baseline_run, "--out", str(baseline_path)])
    assert result.exit_code == 0
    previous_hash = sha256(baseline_path.read_bytes()).hexdigest()

    current_run = _make_run(
        config,
        name="agent",
        events=[
            (EventType.TOOL_CALL, "search", {}),
            (EventType.TOOL_CALL, "new_tool", {}),
        ],
    )

    result = runner.invoke(
        app,
        [
            "accept",
            current_run,
            "--baseline",
            str(baseline_path),
            "--message",
            "expected tool expansion",
        ],
    )

    assert result.exit_code == 0
    assert "Baseline updated:" in result.output
    assert "new_tool" in result.output
    data = json.loads(baseline_path.read_text())
    assert data["source_run_id"] == current_run
    assert data["summary"]["tool_calls"] == 2
    assert data["tool_call_sequence"] == ["search", "new_tool"]
    assert data["acceptance"]["reason"] == "expected tool expansion"
    assert data["acceptance"]["source_run_id"] == current_run
    assert data["acceptance"]["maida_version"]
    assert data["acceptance"]["previous_baseline"] == {
        "path": str(baseline_path),
        "source_run_id": baseline_run,
        "created_at": data["acceptance"]["previous_baseline"]["created_at"],
        "schema_version": BASELINE_SCHEMA_VERSION,
        "sha256": previous_hash,
    }


def test_accept_records_github_provenance_and_verdict(empty_data_dir, monkeypatch):
    config = load_config()
    baseline_run = _make_run(
        config,
        name="agent",
        events=[(EventType.TOOL_CALL, "search", {})],
    )
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", baseline_run, "--out", str(baseline_path)])
    current_run = _make_run(
        config,
        name="agent",
        events=[
            (EventType.TOOL_CALL, "search", {}),
            (EventType.TOOL_CALL, "new_tool", {}),
        ],
    )
    monkeypatch.setenv("GITHUB_ACTOR", "reviewer-login")
    monkeypatch.setenv("GITHUB_REPOSITORY", "maida-ai/example-agent")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example")
    monkeypatch.setenv("MAIDA_PR_NUMBER", "42")
    monkeypatch.setenv("MAIDA_EXPECTED_HEAD_SHA", "a" * 40)

    result = runner.invoke(
        app,
        [
            "accept",
            current_run,
            "--baseline",
            str(baseline_path),
            "--reason",
            "expected tool expansion",
        ],
    )

    assert result.exit_code == 0
    acceptance = json.loads(baseline_path.read_text())["acceptance"]
    assert acceptance["accepted_by"] == "reviewer-login"
    assert acceptance["source"] == {
        "repository": "maida-ai/example-agent",
        "pull_request": {
            "number": 42,
            "url": "https://github.example/maida-ai/example-agent/pull/42",
        },
        "commit_sha": "a" * 40,
    }
    assert acceptance["verdict"] == {
        "outcome": "accepted",
        "summary": ("Accepted run status ok: 2 events, 2 tool calls, 0 errors, 0 loop warnings."),
    }


def test_accept_rejects_invalid_pr_provenance_without_rewriting_baseline(empty_data_dir, monkeypatch):
    config = load_config()
    baseline_run = _make_run(config, name="agent", events=[(EventType.TOOL_CALL, "search", {})])
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", baseline_run, "--out", str(baseline_path)])
    before = baseline_path.read_bytes()
    current_run = _make_run(config, name="agent", events=[(EventType.TOOL_CALL, "lookup", {})])
    monkeypatch.setenv("MAIDA_PR_NUMBER", "not-a-number")

    result = runner.invoke(
        app,
        [
            "accept",
            current_run,
            "--baseline",
            str(baseline_path),
            "--reason",
            "expected rename",
        ],
    )

    assert result.exit_code == 10
    assert "MAIDA_PR_NUMBER must be a positive integer" in result.stderr
    assert baseline_path.read_bytes() == before


def test_accept_records_local_provenance_without_pr_source(empty_data_dir, monkeypatch):
    config = load_config()
    baseline_run = _make_run(config, name="agent", events=[(EventType.TOOL_CALL, "search", {})])
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", baseline_run, "--out", str(baseline_path)])
    current_run = _make_run(config, name="agent", events=[(EventType.TOOL_CALL, "lookup", {})])
    monkeypatch.setenv("MAIDA_ACCEPTED_BY", "local-reviewer")
    for name in (
        "GITHUB_ACTOR",
        "GITHUB_REPOSITORY",
        "MAIDA_PR_NUMBER",
        "MAIDA_EXPECTED_HEAD_SHA",
    ):
        monkeypatch.delenv(name, raising=False)

    result = runner.invoke(
        app,
        [
            "accept",
            current_run,
            "--baseline",
            str(baseline_path),
            "--reason",
            "expected local rename",
        ],
    )

    assert result.exit_code == 0
    acceptance = json.loads(baseline_path.read_text())["acceptance"]
    assert acceptance["accepted_by"] == "local-reviewer"
    assert acceptance["source"] == {
        "repository": None,
        "pull_request": None,
        "commit_sha": None,
    }


def test_accept_reason_alias_updates_baseline(empty_data_dir):
    config = load_config()
    baseline_run = _make_run(config, name="agent", events=[(EventType.TOOL_CALL, "search", {})])
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", baseline_run, "--out", str(baseline_path)])
    current_run = _make_run(config, name="agent", events=[(EventType.TOOL_CALL, "lookup", {})])

    result = runner.invoke(
        app,
        ["accept", current_run, "--baseline", str(baseline_path), "-m", "renamed tool"],
    )

    assert result.exit_code == 0
    data = json.loads(baseline_path.read_text())
    assert data["acceptance"]["reason"] == "renamed tool"


def test_accept_defaults_to_latest_run(empty_data_dir):
    config = load_config()
    baseline_run = _make_run(config, name="agent", events=[(EventType.TOOL_CALL, "search", {})])
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", baseline_run, "--out", str(baseline_path)])
    _make_run(config, name="older", events=[(EventType.TOOL_CALL, "older_tool", {})])
    newest = _make_run(config, name="newer", events=[(EventType.TOOL_CALL, "newest_tool", {})])

    result = runner.invoke(
        app,
        ["accept", "--baseline", str(baseline_path), "--reason", "accept latest"],
    )

    assert result.exit_code == 0
    assert "Using latest run:" in result.stderr
    data = json.loads(baseline_path.read_text())
    assert data["source_run_id"] == newest
    assert data["tool_call_sequence"] == ["newest_tool"]


def test_accept_noop_leaves_baseline_untouched(empty_data_dir):
    config = load_config()
    run_id = _make_run(config, name="agent", events=[(EventType.TOOL_CALL, "search", {})])
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", run_id, "--out", str(baseline_path)])
    before = baseline_path.read_bytes()

    result = runner.invoke(
        app,
        [
            "accept",
            run_id,
            "--baseline",
            str(baseline_path),
            "--reason",
            "same behavior",
        ],
    )

    assert result.exit_code == 0
    assert "no update written" in result.output
    assert baseline_path.read_bytes() == before


def test_accept_missing_reason_exit_two(empty_data_dir):
    config = load_config()
    run_id = _make_run(config, name="agent")
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", run_id, "--out", str(baseline_path)])

    result = runner.invoke(app, ["accept", run_id, "--baseline", str(baseline_path)])

    assert result.exit_code == 2
    assert "Acceptance reason required" in result.stderr


def test_accept_blank_reason_exit_two(empty_data_dir):
    config = load_config()
    run_id = _make_run(config, name="agent")
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", run_id, "--out", str(baseline_path)])

    result = runner.invoke(app, ["accept", run_id, "--baseline", str(baseline_path), "--reason", "   "])

    assert result.exit_code == 2
    assert "Acceptance reason required" in result.stderr


def test_accept_missing_baseline_exit_two(empty_data_dir):
    config = load_config()
    run_id = _make_run(config, name="agent")

    result = runner.invoke(
        app,
        [
            "accept",
            run_id,
            "--baseline",
            str(empty_data_dir / "missing.json"),
            "--reason",
            "expected",
        ],
    )

    assert result.exit_code == 2
    assert "Baseline not found:" in result.stderr


def test_accept_missing_run_exit_two(empty_data_dir):
    config = load_config()
    run_id = _make_run(config, name="agent")
    baseline_path = empty_data_dir / "baseline.json"
    runner.invoke(app, ["baseline", run_id, "--out", str(baseline_path)])

    result = runner.invoke(
        app,
        [
            "accept",
            "missing-run",
            "--baseline",
            str(baseline_path),
            "--reason",
            "expected",
        ],
    )

    assert result.exit_code == 2
    assert "Run not found:" in result.stderr


def test_accept_malformed_baseline_exit_two(empty_data_dir):
    config = load_config()
    run_id = _make_run(config, name="agent")
    baseline_path = empty_data_dir / "baseline.json"
    baseline_path.write_text("{not json", encoding="utf-8")

    result = runner.invoke(
        app,
        ["accept", run_id, "--baseline", str(baseline_path), "--reason", "expected"],
    )

    assert result.exit_code == 2
    assert "Invalid baseline file:" in result.stderr


def test_accept_non_object_baseline_exit_two(empty_data_dir):
    config = load_config()
    run_id = _make_run(config, name="agent")
    baseline_path = empty_data_dir / "baseline.json"
    baseline_path.write_text("[]", encoding="utf-8")

    result = runner.invoke(
        app,
        ["accept", run_id, "--baseline", str(baseline_path), "--reason", "expected"],
    )

    assert result.exit_code == 2
    assert "Invalid baseline file:" in result.stderr


def test_accept_malformed_run_exit_two(empty_data_dir):
    bad_trace_id = "badbad11" + "a" * 24
    baseline_trace_id = "face0011" + "b" * 24
    _write_run_with_malformed_span(empty_data_dir, bad_trace_id)
    _write_trace_run(empty_data_dir, baseline_trace_id, "baseline")
    baseline_path = empty_data_dir / "baseline.json"
    result = runner.invoke(app, ["baseline", baseline_trace_id, "--out", str(baseline_path)])
    assert result.exit_code == 0

    result = runner.invoke(
        app,
        [
            "accept",
            bad_trace_id,
            "--baseline",
            str(baseline_path),
            "--reason",
            "expected",
        ],
    )

    assert result.exit_code == 2
    assert "Run validation failed" in result.stderr
    assert "spans.jsonl line 1" in result.stderr
    assert "sk-test-DO-NOT-LEAK" not in result.stderr


def test_assert_exit_zero_on_pass(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        record_tool_call("t", args={}, result=None)
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["assert", run_id, "--max-steps", "10"])
    assert result.exit_code == 0
    assert "PASSED" in result.output


def test_assert_exit_one_on_fail(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        for i in range(5):
            record_tool_call(f"t{i}", args={}, result=None)
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["assert", run_id, "--max-steps", "3"])
    assert result.exit_code == 1
    assert "FAILED" in result.output


def test_assert_exit_two_missing_baseline(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        pass
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(
        app,
        ["assert", run_id, "--baseline", str(empty_data_dir / "nope.json")],
    )
    assert result.exit_code == 2


def test_assert_exit_two_missing_run(empty_data_dir):
    result = runner.invoke(app, ["assert", "missing-run", "--max-steps", "10"])

    assert result.exit_code == 2
    assert "Run not found:" in result.stderr


def test_assert_exit_ten_on_internal_error(monkeypatch, empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        record_tool_call("t", args={}, result=None)
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)

    def raise_internal_error(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("maida._cli.gating.run_assertions", raise_internal_error)

    result = runner.invoke(app, ["assert", run_id, "--max-steps", "10"])

    assert result.exit_code == 10
    assert "error: boom" in result.stderr


def test_assert_markdown_uses_report_formatter(monkeypatch, empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        record_tool_call("t", args={}, result=None)
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)
    seen = {}

    def fake_format_report_markdown(report, *, diff=None, baseline_path=None):
        seen["run_id"] = report.run_id
        seen["diff"] = diff
        seen["baseline_path"] = baseline_path
        return "CLI markdown report"

    monkeypatch.setattr("maida._cli.gating.format_report_markdown", fake_format_report_markdown)

    result = runner.invoke(app, ["assert", run_id, "--max-steps", "10", "--format", "markdown"])

    assert result.exit_code == 0
    assert result.stdout == "CLI markdown report\n"
    assert seen == {"run_id": run_id, "diff": None, "baseline_path": None}


def test_assert_json_format(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        pass
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["assert", run_id, "--max-steps", "10", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "Usage ping: disabled" in result.stderr
    assert data["passed"] is True


def test_assert_markdown_format(empty_data_dir):
    config = load_config()
    with traced_run(name="assert_test"):
        pass
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["assert", run_id, "--max-steps", "10", "--format", "markdown"])
    assert result.exit_code == 0
    assert "Maida verdict" in result.output


def test_assert_with_baseline(empty_data_dir):
    config = load_config()
    with traced_run(name="baseline_run"):
        for _ in range(5):
            record_tool_call("t", args={}, result=None)
    from tests.support.runs import get_latest_run_id

    bl_run = get_latest_run_id(config)

    bl_path = empty_data_dir / "bl.json"
    runner.invoke(app, ["baseline", bl_run, "--out", str(bl_path)])

    with traced_run(name="check_run"):
        for _ in range(5):
            record_tool_call("t", args={}, result=None)
    check_run = get_latest_run_id(config)

    result = runner.invoke(
        app,
        [
            "assert",
            check_run,
            "--baseline",
            str(bl_path),
            "--max-steps",
            "100",
            "--duration-tolerance",
            "100",
        ],
    )
    assert result.exit_code == 0


def test_assert_no_loops_flag(empty_data_dir):
    config = load_config()
    with traced_run(name="loop_test"):
        record_tool_call("t", args={}, result=None)
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)

    result = runner.invoke(app, ["assert", run_id, "--no-loops"])
    assert result.exit_code == 0


def test_assert_ignore_check_flag(empty_data_dir):
    config = load_config()
    events = [(EventType.TOOL_CALL, f"t{i}", {}) for i in range(100)]
    _make_run(config, name="check", events=events)
    from tests.support.runs import get_latest_run_id

    run_id = get_latest_run_id(config)

    # Run would fail with max_steps=1, but --ignore-check skips step_count
    result = runner.invoke(
        app,
        [
            "assert",
            run_id,
            "--max-steps",
            "1",
            "--ignore-check",
            "step_count",
            "--format",
            "json",
        ],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    step = next(r for r in data["results"] if r["check_name"] == "step_count")
    assert step["ignored"] is True


def test_assert_defaults_to_latest_run(empty_data_dir):
    config = load_config()
    _make_run(config, name="older", events=[(EventType.TOOL_CALL, "t", {})])
    newest = _make_run(config, name="newer", events=[(EventType.TOOL_CALL, "t", {})])

    result = runner.invoke(app, ["assert", "--max-steps", "10", "--format", "json"])
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["run_id"] == newest
    assert "Using latest run:" in result.stderr


def test_assert_no_runs_exit_two(empty_data_dir):
    result = runner.invoke(app, ["assert", "--max-steps", "10"])
    assert result.exit_code == 2
    assert "No runs found" in result.stderr


def test_assert_json_stdout_stays_clean_when_run_id_omitted(empty_data_dir):
    config = load_config()
    _make_run(config, name="run", events=[(EventType.TOOL_CALL, "t", {})])

    result = runner.invoke(app, ["assert", "--max-steps", "10", "--format", "json"])
    assert result.exit_code == 0
    json.loads(result.stdout)  # stdout must be pure JSON


def test_baseline_defaults_to_latest_run(empty_data_dir):
    config = load_config()
    _make_run(config, name="older", events=[(EventType.TOOL_CALL, "t", {})])
    newest = _make_run(config, name="newer", events=[(EventType.TOOL_CALL, "t", {})])

    out = empty_data_dir / "bl.json"
    result = runner.invoke(app, ["baseline", "--out", str(out)])
    assert result.exit_code == 0
    bl = json.loads(out.read_text())
    assert bl["source_run_id"] == newest


def test_baseline_no_runs_exit_two(empty_data_dir):
    out = empty_data_dir / "bl.json"
    result = runner.invoke(app, ["baseline", "--out", str(out)])
    assert result.exit_code == 2
    assert "No runs found" in result.stderr
