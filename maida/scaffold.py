"""Project scaffolding for ``maida init``: starter policy and CI workflow."""

from pathlib import Path

POLICY_RELPATH = Path(".maida") / "policy.yaml"
WORKFLOW_RELPATH = Path(".github") / "workflows" / "maida.yml"
CHECKOUT_ACTION_REF = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
MAIDA_ASSERT_ACTION_REF = "maida-ai/maida-assert@main"
MAIDA_ACCEPT_ACTION_REF = "maida-ai/maida-assert/accept-command@main"

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
permissions: {{}}
env:
  # Replace this with the script that runs your traced agent.
  MAIDA_AGENT_SCRIPT: my_agent.py
  MAIDA_POLICY: .maida/policy.yaml
  # After committing a baseline, point this at it to enable `/maida accept`:
  MAIDA_BASELINE: ''
concurrency:
  group: maida-${{{{ github.event.pull_request.number || github.event.issue.number || github.event.client_payload.pr_number }}}}
  cancel-in-progress: false
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
      statuses: write
    steps:
      - name: Verify PR identity
        id: pr
        uses: maida-ai/maida-assert/pr-context@main
      - name: Check out repository
        uses: {CHECKOUT_ACTION_REF} # v7
        with:
          ref: ${{{{ steps.pr.outputs.head-sha }}}}
          fetch-depth: 0
          persist-credentials: false
      - name: Run Maida regression gate
        id: gate
        uses: {MAIDA_ASSERT_ACTION_REF}
        with:
          agent-script: ${{{{ env.MAIDA_AGENT_SCRIPT }}}}
          policy: ${{{{ env.MAIDA_POLICY }}}}
          baseline: ${{{{ env.MAIDA_BASELINE }}}}
          accept-command-enabled: ${{{{ env.MAIDA_BASELINE != '' }}}}
          configuration-acceptance: ${{{{ vars.MAIDA_CONFIGURATION_ACCEPTANCE }}}}
      - name: Publish required gate status
        if: always() && steps.pr.outcome == 'success'
        uses: maida-ai/maida-assert/publish-status@main
        with:
          head-sha: ${{{{ steps.pr.outputs.head-sha }}}}
          base-sha: ${{{{ steps.pr.outputs.base-sha }}}}
          verdict: ${{{{ steps.gate.outputs.verdict }}}}
          conclusion: ${{{{ steps.gate.outputs.conclusion }}}}
          publication: ${{{{ steps.gate.outputs.publication }}}}

  authorize:
    if: >-
      github.event_name == 'issue_comment' &&
      github.event.issue.pull_request &&
      startsWith(github.event.comment.body, '/maida accept')
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pull-requests: write
    outputs:
      authorized: ${{{{ steps.command.outputs.authorized }}}}
      context: ${{{{ steps.command.outputs.context }}}}
      head-sha: ${{{{ steps.command.outputs.head-sha }}}}
    steps:
      - id: command
        uses: {MAIDA_ACCEPT_ACTION_REF}
        with:
          stage: authorize
          baseline: ${{{{ env.MAIDA_BASELINE }}}}
          policy: ${{{{ env.MAIDA_POLICY }}}}
          github-token: ${{{{ github.token }}}}

  capture:
    needs: authorize
    if: needs.authorize.outputs.authorized == 'true'
    runs-on: ubuntu-latest
    timeout-minutes: 10
    permissions:
      contents: read
    steps:
      - uses: {CHECKOUT_ACTION_REF} # v7
        with:
          ref: ${{{{ needs.authorize.outputs.head-sha }}}}
          persist-credentials: false
      - uses: maida-ai/maida-assert/capture-acceptance@main
        with:
          context: ${{{{ needs.authorize.outputs.context }}}}
          agent-script: ${{{{ env.MAIDA_AGENT_SCRIPT }}}}
          artifact-directory: ${{{{ runner.temp }}}}/maida-acceptance
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: maida-accept-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}
          path: ${{{{ runner.temp }}}}/maida-acceptance/acceptance.json
          if-no-files-found: error
          retention-days: 1

  write:
    needs: [authorize, capture]
    if: always() && needs.authorize.outputs.authorized == 'true'
    runs-on: ubuntu-latest
    permissions:
      contents: write
      pull-requests: write
    steps:
      - uses: actions/download-artifact@d3f86a106a0bac45b974a628896c90dbdf5c8093 # v4.3.0
        if: needs.capture.result == 'success'
        with:
          name: maida-accept-${{{{ github.run_id }}}}-${{{{ github.run_attempt }}}}
          path: ${{{{ runner.temp }}}}/maida-acceptance
      - uses: maida-ai/maida-assert/write-back@main
        if: always()
        with:
          context: ${{{{ needs.authorize.outputs.context }}}}
          artifact-directory: ${{{{ runner.temp }}}}/maida-acceptance
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
