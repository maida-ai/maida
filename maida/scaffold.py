"""Project scaffolding for ``maida init``: starter policy and CI workflow."""

from pathlib import Path

POLICY_RELPATH = Path(".maida") / "policy.yaml"
WORKFLOW_RELPATH = Path(".github") / "workflows" / "maida.yml"
CHECKOUT_ACTION_REF = "actions/checkout@v7"
MAIDA_ASSERT_ACTION_REF = "maida-ai/maida-assert@V4"

POLICY_TEMPLATE = """\
# Maida policy - enforced by `maida assert` locally and in CI.
# Reference: https://github.com/maida-ai/maida/blob/main/docs/reference/policy.md
#
# Starter behavior is intentionally forgiving:
# - With a baseline, numeric checks allow modest growth.
# - Without a baseline, these tolerance checks do nothing.
# - Strict checks are commented out; uncomment them when you want CI to fail.
assert:
  # Allowed growth vs baseline (0.5 = +50%).
  step_tolerance: 0.5
  tool_call_tolerance: 0.5
  cost_tolerance: 0.5
  duration_tolerance: 0.5

  # Strict checks (uncomment to opt in):
  # Fail on repeated loop patterns.
  # no_loops: true
  # Fail if a guardrail stopped the run.
  # no_guardrails: true
  # Fail on tools not present in the baseline.
  # no_new_tools: true
  # Fail unless the run ended with status "ok".
  # expect_status: ok

  # Hard caps, independent of any baseline (uncomment to enable):
  # max_steps: 80
  # max_tool_calls: 40
  # max_cost_tokens: 20000
  # max_duration_ms: 60000

  # Ignored checks (skip these even when thresholds are set):
  # ignored_checks:
  #   - step_count
  #   - cost_tokens
"""

WORKFLOW_TEMPLATE = f"""\
name: Agent Regression Check
on: [pull_request]

permissions:
  contents: read          # checkout only needs read access
  pull-requests: write    # maida-assert posts a sticky PR comment

jobs:
  agent-check:
    runs-on: ubuntu-latest
    steps:
      - name: Check out repository
        uses: {CHECKOUT_ACTION_REF}

      - name: Run Maida regression gate
        uses: {MAIDA_ASSERT_ACTION_REF}
        with:
          # Replace this with the script that runs your traced agent.
          # It must use @trace or traced_run so Maida records a run.
          agent-script: my_agent.py
          policy: .maida/policy.yaml
          # After committing a baseline, uncomment and point this at it:
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
