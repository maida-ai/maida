"""Zero-token burn-in harness for the repeated statistical gate."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from maida.assertions import AssertionPolicy
from maida.config import load_config
from maida.runner import run_trials
from maida.statistics import GateVerdict


_SYNTHETIC_AGENT = """\
import os
import random

from maida import record_tool_call, traced_run

seed = int(os.environ["MAIDA_BURN_IN_GATE_SEED"])
trial = int(os.environ["MAIDA_TRIAL_INDEX"])
pass_probability = float(os.environ["MAIDA_BURN_IN_PASS_PROBABILITY"])
healthy = random.Random(f"{seed}:{trial}").random() < pass_probability

with traced_run(name="maida-burn-in-agent"):
    if not healthy:
        record_tool_call("synthetic-regression", args={}, result="injected")
"""


@dataclass(frozen=True)
class BurnInReport:
    """Stability rates from repeated unchanged-repository gate decisions."""

    gates: int
    trials_per_gate: int
    seed: int
    pass_probability: float
    verdicts: tuple[GateVerdict, ...]
    elapsed_seconds: float

    @property
    def false_fail_count(self) -> int:
        return self.verdicts.count(GateVerdict.FAIL)

    @property
    def inconclusive_count(self) -> int:
        return self.verdicts.count(GateVerdict.INCONCLUSIVE)

    @property
    def false_fail_rate(self) -> float:
        return self.false_fail_count / self.gates

    @property
    def inconclusive_rate(self) -> float:
        return self.inconclusive_count / self.gates

    @property
    def model_calls(self) -> int:
        return 0

    @property
    def acceptance_met(self) -> bool:
        return self.false_fail_rate < 0.02 and self.inconclusive_rate < 0.15

    def to_dict(self) -> dict[str, object]:
        return {
            "gates": self.gates,
            "trials_per_gate": self.trials_per_gate,
            "seed": self.seed,
            "pass_probability": self.pass_probability,
            "verdicts": [verdict.value for verdict in self.verdicts],
            "false_fail_count": self.false_fail_count,
            "false_fail_rate": self.false_fail_rate,
            "inconclusive_count": self.inconclusive_count,
            "inconclusive_rate": self.inconclusive_rate,
            "model_calls": self.model_calls,
            "elapsed_seconds": self.elapsed_seconds,
            "acceptance_met": self.acceptance_met,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def to_text(self) -> str:
        return "\n".join(
            [
                f"Gates: {self.gates} ({self.trials_per_gate} trials each)",
                f"False-fail rate: {self.false_fail_rate:.1%} "
                f"({self.false_fail_count}/{self.gates}; required <2%)",
                f"Inconclusive rate: {self.inconclusive_rate:.1%} "
                f"({self.inconclusive_count}/{self.gates}; required <15%)",
                "Model calls: 0",
                f"Elapsed: {self.elapsed_seconds:.2f}s",
                f"RESULT: {'PASS' if self.acceptance_met else 'FAIL'}",
            ]
        )


def summarize_verdicts(
    verdicts: Iterable[GateVerdict],
    *,
    trials_per_gate: int,
    seed: int,
    pass_probability: float,
    elapsed_seconds: float = 0.0,
) -> BurnInReport:
    """Build a burn-in report from already-computed gate verdicts."""
    values = tuple(verdicts)
    if not values:
        raise ValueError("at least one gate verdict is required")
    if trials_per_gate < 1:
        raise ValueError("trials_per_gate must be at least 1")
    if not 0.0 <= pass_probability <= 1.0:
        raise ValueError("pass_probability must be between 0 and 1")
    return BurnInReport(
        gates=len(values),
        trials_per_gate=trials_per_gate,
        seed=seed,
        pass_probability=pass_probability,
        verdicts=values,
        elapsed_seconds=elapsed_seconds,
    )


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def run_burn_in(
    *,
    gates: int = 50,
    trials_per_gate: int = 3,
    seed: int = 137,
    pass_probability: float = 0.99,
    max_wall_time_seconds: float = 600.0,
    workspace_parent: Path | None = None,
) -> BurnInReport:
    """Run the full gate repeatedly against one unchanged synthetic agent repo."""
    if gates < 1:
        raise ValueError("gates must be at least 1")
    if max_wall_time_seconds <= 0:
        raise ValueError("max_wall_time_seconds must be positive")

    started = time.monotonic()
    with tempfile.TemporaryDirectory(
        prefix="maida-burn-in-", dir=workspace_parent
    ) as temp:
        temp_root = Path(temp)
        project = temp_root / "fixed-agent-repo"
        project.mkdir()
        agent_script = project / "agent.py"
        agent_script.write_text(_SYNTHETIC_AGENT, encoding="utf-8")
        subprocess.run(
            ["git", "init", "--quiet"], cwd=project, check=True, capture_output=True
        )
        subprocess.run(
            ["git", "add", "agent.py"], cwd=project, check=True, capture_output=True
        )

        previous_data = os.environ.get("MAIDA_DATA_DIR")
        previous_seed = os.environ.get("MAIDA_BURN_IN_GATE_SEED")
        previous_probability = os.environ.get("MAIDA_BURN_IN_PASS_PROBABILITY")
        os.environ["MAIDA_DATA_DIR"] = str(temp_root / "data")
        os.environ["MAIDA_BURN_IN_PASS_PROBABILITY"] = str(pass_probability)
        verdicts: list[GateVerdict] = []
        try:
            config = load_config(project_root=project)
            policy = AssertionPolicy(
                trials=trials_per_gate,
                max_tool_calls=0,
                confidence_level=0.95,
                pass_rate_threshold=0.90,
            )
            for gate_index in range(gates):
                elapsed = time.monotonic() - started
                if elapsed >= max_wall_time_seconds:
                    raise TimeoutError(
                        f"burn-in exceeded {max_wall_time_seconds:g}s wall-time cap"
                    )
                os.environ["MAIDA_BURN_IN_GATE_SEED"] = str(seed + gate_index * 10_000)
                report = run_trials(
                    agent_script,
                    trials=trials_per_gate,
                    policy=policy,
                    config=config,
                    project_root=project,
                    confidence_level=policy.confidence_level,
                    pass_rate_threshold=policy.pass_rate_threshold,
                )
                verdicts.append(report.verdict)
        finally:
            _restore_env("MAIDA_DATA_DIR", previous_data)
            _restore_env("MAIDA_BURN_IN_GATE_SEED", previous_seed)
            _restore_env("MAIDA_BURN_IN_PASS_PROBABILITY", previous_probability)

    return summarize_verdicts(
        verdicts,
        trials_per_gate=trials_per_gate,
        seed=seed,
        pass_probability=pass_probability,
        elapsed_seconds=time.monotonic() - started,
    )
