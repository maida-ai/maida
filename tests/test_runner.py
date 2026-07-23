"""Tests for repeated, workspace-isolated agent execution."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from maida import record_tool_call, traced_run
from maida.assertions import AssertionPolicy
from maida.baseline import create_baseline
from maida.config import load_config
from maida.runner import RunExecutionError, run_trials
from maida.statistics import GateVerdict
from tests.conftest import get_latest_run_id


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _write_agent(repo: Path, body: str) -> Path:
    script = repo / "agent.py"
    script.write_text(body, encoding="utf-8")
    _git("add", "agent.py", cwd=repo)
    return script


@pytest.fixture
def agent_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "project"
    repo.mkdir()
    _git("init", "--quiet", cwd=repo)
    return repo


def test_run_trials_isolates_workspace_and_preserves_one_trace_per_trial(
    agent_repo: Path, temp_data_dir: Path
) -> None:
    _write_agent(
        agent_repo,
        """
from pathlib import Path
from maida import record_tool_call, traced_run

state = Path("trial-state.txt")
if state.exists():
    raise RuntimeError("workspace leaked from an earlier trial")
state.write_text("created", encoding="utf-8")
with traced_run(name="isolated-agent"):
    record_tool_call("work", args={}, result="ok")
""".lstrip(),
    )

    config = load_config(project_root=agent_repo)
    report = run_trials(
        agent_repo / "agent.py",
        trials=3,
        policy=AssertionPolicy(max_tool_calls=1),
        config=config,
        project_root=agent_repo,
    )

    assert report.trials_requested == 3
    assert len(report.trials) == 3
    assert len({trial.trace_id for trial in report.trials}) == 3
    assert all(trial.process_exit_code == 0 for trial in report.trials)
    assert all(trial.assertion_report.passed for trial in report.trials)
    assert report.verdict is GateVerdict.PASS
    assert {result.check_name for result in report.aggregate_results} == {
        "agent_process",
        "tool_calls",
    }
    assert not (agent_repo / "trial-state.txt").exists()
    for trial in report.trials:
        assert (temp_data_dir / "runs" / trial.trace_id / "meta.json").is_file()


def test_run_trials_copies_nonignored_untracked_files(
    agent_repo: Path, temp_data_dir: Path
) -> None:
    (agent_repo / "scenario.txt").write_text("expected", encoding="utf-8")
    _write_agent(
        agent_repo,
        """
from pathlib import Path
from maida import record_tool_call, traced_run

value = Path("scenario.txt").read_text(encoding="utf-8")
with traced_run(name="untracked-input"):
    record_tool_call("read", args={}, result=value)
""".lstrip(),
    )

    report = run_trials(
        agent_repo / "agent.py",
        trials=1,
        policy=AssertionPolicy(),
        config=load_config(project_root=agent_repo),
        project_root=agent_repo,
    )

    assert report.trials[0].process_exit_code == 0


def test_run_trials_rejects_agent_that_does_not_produce_exactly_one_trace(
    agent_repo: Path, temp_data_dir: Path
) -> None:
    _write_agent(agent_repo, "print('no trace')\n")

    with pytest.raises(RunExecutionError, match="exactly one trace"):
        run_trials(
            agent_repo / "agent.py",
            trials=1,
            policy=AssertionPolicy(),
            config=load_config(project_root=agent_repo),
            project_root=agent_repo,
        )


def test_run_trials_rejects_non_positive_trial_count(
    agent_repo: Path, temp_data_dir: Path
) -> None:
    _write_agent(agent_repo, "print('unused')\n")

    with pytest.raises(ValueError, match="at least 1"):
        run_trials(
            agent_repo / "agent.py",
            trials=0,
            policy=AssertionPolicy(),
            config=load_config(project_root=agent_repo),
            project_root=agent_repo,
        )


def test_trial_report_json_records_metadata_and_check_outcomes(
    agent_repo: Path, temp_data_dir: Path
) -> None:
    _write_agent(
        agent_repo,
        """
from maida import traced_run

with traced_run(name="json-agent"):
    pass
""".lstrip(),
    )
    report = run_trials(
        agent_repo / "agent.py",
        trials=1,
        policy=AssertionPolicy(max_steps=10),
        config=load_config(project_root=agent_repo),
        project_root=agent_repo,
    )

    payload = json.loads(report.to_json())
    assert payload["report_version"] == "1"
    assert payload["metadata"]["trials_requested"] == 1
    assert payload["passed"] is True
    assert payload["verdict"] == "pass"
    assert payload["trials"][0]["trial"] == 1
    assert payload["trials"][0]["run_name"] == "json-agent"
    assert payload["trials"][0]["process_exit_code"] == 0
    assert payload["trials"][0]["checks"][0]["check_name"] == "step_count"


def test_trial_report_markdown_is_verdict_first_with_intervals_and_traces(
    agent_repo: Path, temp_data_dir: Path
) -> None:
    _write_agent(
        agent_repo,
        "from maida import traced_run\nwith traced_run(name='markdown-agent'):\n    pass\n",
    )
    report = run_trials(
        agent_repo / "agent.py",
        trials=1,
        policy=AssertionPolicy(max_steps=10),
        config=load_config(project_root=agent_repo),
        project_root=agent_repo,
    )

    markdown = report.to_markdown()
    assert markdown.startswith("## ✅ Maida statistical gate: pass")
    assert "Wilson interval" in markdown
    assert "`step_count`" in markdown
    assert f"`{report.trials[0].trace_id[:8]}`" in markdown


def test_trial_report_records_each_baseline_diff(
    agent_repo: Path, temp_data_dir: Path
) -> None:
    config = load_config(project_root=agent_repo)
    with traced_run(name="baseline"):
        record_tool_call("old-tool", args={}, result="ok")
    baseline = create_baseline(get_latest_run_id(config), config)
    _write_agent(
        agent_repo,
        """
from maida import record_tool_call, traced_run
with traced_run(name="candidate"):
    record_tool_call("new-tool", args={}, result="ok")
""".lstrip(),
    )

    report = run_trials(
        agent_repo / "agent.py",
        trials=1,
        policy=AssertionPolicy(no_new_tools=True),
        baseline=baseline,
        config=config,
        project_root=agent_repo,
    )

    assert report.trials[0].baseline_diff is not None
    assert report.trials[0].baseline_diff["new_tools"] == ["new-tool"]


def test_statistical_report_schema_pins_three_verdict_contract() -> None:
    schema = json.loads(
        (
            Path(__file__).parents[1]
            / "schemas"
            / "statistical-gate-report.schema.json"
        ).read_text(encoding="utf-8")
    )

    assert schema["properties"]["report_version"]["const"] == "1"
    assert schema["properties"]["verdict"]["enum"] == [
        "pass",
        "fail",
        "inconclusive",
    ]
    assert schema["properties"]["passed"]["type"] == ["boolean", "null"]
