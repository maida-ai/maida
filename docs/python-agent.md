# Protect a Python tool-calling agent

First run the offline gate story from [Getting started](getting-started.md). Then use one existing offline test or fixture for your real agent. The standalone `uv tool install` command installs the CLI in isolation; it does not make `import maida` available in your project.

## Add Maida to the project

Use the project's package manager and install the library into the interpreter that runs the agent:

```bash
uv add "maida-ai==0.5.3"
```

Wrap one complete invocation with `@trace`, and record real tool boundaries or use a [supported integration](integrations.md). Preserve the agent's return value, errors, and tool behavior. Do not record invented events to get a green check.

The [practical Python tutorial](https://github.com/maida-ai/maida-tutorials/blob/main/guides/python-agent.md) supplies a small offline task with known-good and regressed behavior. All runnable examples now live in [maida-tutorials/examples](https://github.com/maida-ai/maida-tutorials/tree/main/examples).

## Capture and review

Use an isolated data directory and run your known-good fixture. Replace `agent.py` with the project's traced entrypoint:

```bash
export MAIDA_DATA_DIR="$(mktemp -d)"
uv run python agent.py
uv run maida extract --window "$MAIDA_DATA_DIR/runs" --out maida-draft
```

Inspect the generated policy and baseline. Candidates remain inactive until you review which behaviors are intentional. Copy the approved pair to `.maida/policy.yaml` and `.maida/baselines/agent.json`, preserving the review reason in Git. Start with a few invariant checks; a single observation does not justify a population-level reliability claim.

## Prove pass, fail, and repair

From the project's Git workspace, run the actual entrypoint against those accepted files:

```bash
uv run maida run agent.py --trials 1 \
  --baseline .maida/baselines/agent.json \
  --policy .maida/policy.yaml --format markdown
```

One trial is appropriate for the initial invariant-only rehearsal. If you retain statistical metrics, preserve their stated thresholds and supply a feasible budget instead. Read the report verdict: exit `0` includes INCONCLUSIVE.

First expect PASS. In a disposable copy, introduce a known regression covered by the reviewed policy and expect FAIL; repair it and expect PASS without changing the baseline. Investigate missing traces as an instrumentation problem. Only then [add the Action](https://github.com/maida-ai/maida-assert) with the same entrypoint and checked-in files.
