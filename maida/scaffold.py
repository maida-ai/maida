"""Project scaffolding for ``maida init``: starter policy and CI workflow."""

from pathlib import Path

POLICY_RELPATH = Path(".maida") / "policy.yaml"
WORKFLOW_RELPATH = Path(".github") / "workflows" / "maida.yml"

POLICY_TEMPLATE = """\
# Maida policy — enforced by `maida assert` locally and in CI.
# Reference: https://github.com/maida-ai/maida/blob/main/docs/reference/policy.md
assert:
  # Fail if the run loops (repeated tool/LLM call patterns).
  no_loops: true
  # Fail if a development guardrail (max calls, duration, ...) fired.
  no_guardrails: true
  # Fail when the run uses tools the baseline has never seen.
  # (Only enforced when a baseline is provided.)
  no_new_tools: true
  # The run must finish with this status.
  expect_status: ok
  # Allowed growth vs the baseline (fractional: 0.5 = +50%).
  step_tolerance: 0.5
  tool_call_tolerance: 0.5
  cost_tolerance: 0.5
  duration_tolerance: 0.5
  # Hard caps, independent of any baseline (uncomment to enable):
  # max_steps: 80
  # max_tool_calls: 40
  # max_cost_tokens: 20000
  # max_duration_ms: 60000
"""

WORKFLOW_TEMPLATE = """\
name: Agent Regression Check
on: [pull_request]

permissions:
  contents: read
  pull-requests: write  # lets the action post the regression report

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: maida-ai/maida-assert@v2
        with:
          # TODO: point at the script that runs your traced agent
          # (it must use @trace or traced_run so a run is recorded).
          agent-script: my_agent.py
          policy: .maida/policy.yaml
          # Once you commit a baseline (maida baseline --out ...), enable:
          # baseline: .maida/baselines/my_agent.json
"""


def write_scaffold(path: Path, content: str, force: bool = False) -> bool:
    """Write *content* to *path*, creating parents.

    Returns True when the file was written, False when it already existed
    and *force* was not set.
    """
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
