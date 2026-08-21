# `maida demo`

Runs a bundled simulated customer-support agent and records a trace. No network, no API keys; all LLM/tool data is canned and nothing leaves your machine.

**Usage:**

```bash
maida demo [--regression] [--plan] [--policy PATH]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--regression` | Full story: baseline a known-good run, run a "refactored" agent that loops, calls a new tool, and burns more tokens, then show the failing gate report and a PR-comment preview. Writes the baseline to `.maida/baselines/demo-support-agent.json`. |
| `--plan` | Generate a runtime plan and refuse it before execution. Requires the optional `maida-workflows` backend; without it the command exits `2` with the install instruction. |
| `--policy PATH` | Core policy 2.1 file used by `--plan`. Defaults to `.maida/policy.yaml` when that file exists, otherwise the bundled refusal policy. `--policy` takes precedence over both. |

**Examples:**

```bash
maida demo               # one traced run; inspect it with `maida view`
maida demo --regression  # watch the gate catch a bad refactor
maida demo --plan        # refuse a generated plan before any child runs
```

**Exit codes:** `0` success (including when the demo gate intentionally fails); `10` internal error.
