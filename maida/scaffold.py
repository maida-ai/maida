"""Project scaffolding for ``maida init``: starter policy and CI workflow."""

from pathlib import Path

POLICY_RELPATH = Path(".maida") / "policy.yaml"
WORKFLOW_RELPATH = Path(".github") / "workflows" / "maida.yml"
CHECKOUT_ACTION_REF = "actions/checkout@v7"
MAIDA_ASSERT_ACTION_REF = "maida-ai/maida-assert@v5"
MAIDA_ACCEPT_ACTION_REF = "maida-ai/maida-assert/accept-command@v5"

POLICY_TEMPLATE = """\
# Maida policy v2 - enforced locally and by maida-assert main.
# `confidence` is one-sided coverage (0.95 uses z = 1.645).
# Measured tolerances compare against the immutable checked-in baseline sample.
version: 2
trials: 3
fail_fast: true
metrics:
  stop_condition_reached:
    kind: invariant
    require: true

  forbidden_tools:
    kind: invariant
    none_of: [admin_delete]

  step_count:
    kind: measured
    direction: upper
    tolerance: {relative: 0.5}

  cost_tokens:
    kind: measured
    direction: upper
    tolerance: {relative: 0.25}

  task_pass_rate:
    kind: statistical
    direction: lower
    threshold: 0.90
    confidence: 0.95
    success_predicate: all_invariants_passed
    # n_min is 25; the default three-trial scaffold reports without blocking.
    mode: report_only
"""

WORKFLOW_TEMPLATE = f"""\
name: Agent Regression Check
on:
  pull_request:
  issue_comment:
    types: [created]
  repository_dispatch:
    types: [maida_baseline_updated]

# Each job declares only the permissions needed for its event path.
permissions: {{}}

env:
  # Replace this with the script that runs your traced agent.
  # It must use @trace or traced_run so Maida records a run.
  MAIDA_AGENT_SCRIPT: my_agent.py
  MAIDA_POLICY: .maida/policy.yaml
  # After committing a baseline, point this at it to enable `/maida accept`:
  MAIDA_BASELINE: ''

jobs:
  agent-check:
    if: >-
      github.event_name == 'pull_request' ||
      github.event_name == 'repository_dispatch'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
      checks: write
      # A repository_dispatch run belongs to the default branch, so publish the
      # gate result explicitly against the accepted PR-head SHA.
      statuses: write
    steps:
      - name: Check out repository
        uses: {CHECKOUT_ACTION_REF}
        with:
          ref: ${{{{ github.event_name == 'repository_dispatch' && github.event.client_payload.sha || github.sha }}}}

      - name: Run Maida regression gate
        id: gate
        uses: {MAIDA_ASSERT_ACTION_REF}
        with:
          agent-script: ${{{{ env.MAIDA_AGENT_SCRIPT }}}}
          policy: ${{{{ env.MAIDA_POLICY }}}}
          baseline: ${{{{ env.MAIDA_BASELINE }}}}
          accept-command-enabled: ${{{{ env.MAIDA_BASELINE != '' }}}}

      - name: Publish dispatched gate status
        if: always() && github.event_name == 'repository_dispatch'
        env:
          GH_TOKEN: ${{{{ github.token }}}}
          TARGET_SHA: ${{{{ github.event.client_payload.sha }}}}
          GATE_OUTCOME: ${{{{ steps.gate.outcome }}}}
        shell: bash
        run: |
          if [ "$GATE_OUTCOME" = "success" ]; then
            state=success
            description="Maida behavioral regression gate passed"
          else
            state=failure
            description="Maida behavioral regression gate failed"
          fi
          gh api --method POST \\
            "repos/${{GITHUB_REPOSITORY}}/statuses/${{TARGET_SHA}}" \\
            -f state="$state" \\
            -f context="Maida / agent-check" \\
            -f description="$description" \\
            -f target_url="${{GITHUB_SERVER_URL}}/${{GITHUB_REPOSITORY}}/actions/runs/${{GITHUB_RUN_ID}}"

  accept-command:
    if: >-
      github.event_name == 'issue_comment' &&
      github.event.issue.pull_request &&
      startsWith(github.event.comment.body, '/maida accept')
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - name: Accept intentional baseline change
        uses: {MAIDA_ACCEPT_ACTION_REF}
        with:
          agent-script: ${{{{ env.MAIDA_AGENT_SCRIPT }}}}
          policy: ${{{{ env.MAIDA_POLICY }}}}
          baseline: ${{{{ env.MAIDA_BASELINE }}}}
          github-token: ${{{{ github.token }}}}
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
