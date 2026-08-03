"""Measure one fixed-N offline harness row without making model calls."""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import tempfile
import time
from pathlib import Path

from maida.assertions import AssertionPolicy
from maida.baseline import extract_run_metrics
from maida.config import load_config
from maida.runner import run_trials
from maida.storage import load_run_for_analysis


_AGENT = """\
from maida import record_llm_call, traced_run

with traced_run(name="issue-187-cost-harness"):
    record_llm_call(
        "recorded-offline-model",
        prompt="synthetic",
        response="synthetic",
        usage={"total_tokens": 42},
    )
"""


def main() -> None:
    repetitions = 5
    trials = 3
    with tempfile.TemporaryDirectory(prefix="maida-187-cost-") as temp:
        root = Path(temp)
        project = root / "project"
        project.mkdir()
        (project / "agent.py").write_text(_AGENT, encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet"],
            cwd=project,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "add", "agent.py"],
            cwd=project,
            check=True,
            capture_output=True,
        )
        previous = os.environ.get("MAIDA_DATA_DIR")
        os.environ["MAIDA_DATA_DIR"] = str(root / "data")
        wall_times: list[float] = []
        calls: list[int] = []
        tokens: list[int] = []
        try:
            config = load_config(project_root=project)
            for _ in range(repetitions):
                started = time.perf_counter()
                report = run_trials(
                    project / "agent.py",
                    trials=trials,
                    policy=AssertionPolicy(trials=trials, fail_fast=False),
                    config=config,
                    project_root=project,
                )
                wall_times.append(time.perf_counter() - started)
                gate_calls = gate_tokens = 0
                for trial in report.trials:
                    _, meta, events = load_run_for_analysis(trial.trace_id, config)
                    summary = extract_run_metrics(meta, events)["summary"]
                    gate_calls += int(summary["llm_calls"])
                    gate_tokens += int(summary["total_tokens"])
                calls.append(gate_calls)
                tokens.append(gate_tokens)
        finally:
            if previous is None:
                os.environ.pop("MAIDA_DATA_DIR", None)
            else:
                os.environ["MAIDA_DATA_DIR"] = previous

    payload = {
        "description": "recorded offline harness; no network model call",
        "trials": trials,
        "gate_repetitions": repetitions,
        "model_calls_per_trial": 1,
        "tokens_per_trial": 42,
        "median_model_calls_per_gate": statistics.median(calls),
        "worst_model_calls_per_gate": max(calls),
        "median_tokens_per_gate": statistics.median(tokens),
        "worst_tokens_per_gate": max(tokens),
        "median_wall_seconds_per_gate": statistics.median(wall_times),
        "worst_wall_seconds_per_gate": max(wall_times),
    }
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
