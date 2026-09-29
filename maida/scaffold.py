"""Project scaffolding for ``maida init``: starter policy and CI workflow."""

import json
import tomllib
from pathlib import Path

POLICY_RELPATH = Path(".maida") / "policy.yaml"
WORKFLOW_RELPATH = Path(".github") / "workflows" / "maida.yml"
CHECKOUT_ACTION_REF = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
MAIDA_ACTION_REVISION = "v0.6.0"
MAIDA_ASSERT_ACTION_REF = f"maida-ai/maida-assert@{MAIDA_ACTION_REVISION}"
MAIDA_ACCEPT_ACTION_REF = f"maida-ai/maida-assert/accept-command@{MAIDA_ACTION_REVISION}"

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
  # The existing traced entrypoint selected at initialization.
  MAIDA_AGENT_SCRIPT: __MAIDA_AGENT_SCRIPT__
  MAIDA_POLICY: .maida/policy.yaml
  # Reviewed baseline selected at initialization.
  MAIDA_BASELINE: __MAIDA_BASELINE__
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
        uses: maida-ai/maida-assert/pr-context@{MAIDA_ACTION_REVISION}
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
        uses: maida-ai/maida-assert/publish-status@{MAIDA_ACTION_REVISION}
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
      - uses: maida-ai/maida-assert/capture-acceptance@{MAIDA_ACTION_REVISION}
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
      - uses: maida-ai/maida-assert/write-back@{MAIDA_ACTION_REVISION}
        if: always()
        with:
          context: ${{{{ needs.authorize.outputs.context }}}}
          artifact-directory: ${{{{ runner.temp }}}}/maida-acceptance
          github-token: ${{{{ github.token }}}}
"""


def render_workflow(agent_script: str, baseline: str) -> str:
    """Render validated paths and detected project dependencies in both run jobs."""
    rendered = WORKFLOW_TEMPLATE.replace("__MAIDA_AGENT_SCRIPT__", json.dumps(agent_script)).replace(
        "__MAIDA_BASELINE__", json.dumps(baseline)
    )
    setup = dependency_setup()
    if setup:
        rendered = rendered.replace(
            "      - name: Run Maida regression gate", setup + "      - name: Run Maida regression gate"
        )
        rendered = rendered.replace(
            f"      - uses: maida-ai/maida-assert/capture-acceptance@{MAIDA_ACTION_REVISION}",
            setup + f"      - uses: maida-ai/maida-assert/capture-acceptance@{MAIDA_ACTION_REVISION}",
        )
    return rendered


def dependency_setup() -> str:
    """Use the consumer's declared Python dependencies, without fabricating any."""
    commands = []
    pyproject = Path("pyproject.toml")
    if pyproject.is_file():
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        if Path("uv.lock").is_file():
            commands.extend(
                [
                    'uv export --locked --no-dev --no-emit-project --output-file "$RUNNER_TEMP/maida-project-requirements.txt"',
                    'uv pip install --python "$(command -v python)" -r "$RUNNER_TEMP/maida-project-requirements.txt"',
                ]
            )
            if project.get("build-system"):
                commands.append('uv pip install --python "$(command -v python)" --no-deps .')
        elif project.get("project"):
            commands.append('uv pip install --python "$(command -v python)" .')
    if not commands and Path("requirements.txt").is_file():
        commands.append('uv pip install --python "$(command -v python)" -r requirements.txt')
    if not commands:
        return ""
    setup = """      - name: Set up project Python
        uses: actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97 # v7.0.0
        with:
          python-version: '3.12'
      - name: Set up project uv
        uses: astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4 # v10.1.0
        with:
          version: '0.12.17'
          enable-cache: 'false'
      - name: Install project dependencies
        shell: bash
        run: |
"""
    return setup + "".join(f"          {command}\n" for command in commands)


def write_scaffold(path: Path, content: str, force: bool = False) -> bool:
    """Compatibility helper for consumers rendering a single scaffold artifact."""
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True
