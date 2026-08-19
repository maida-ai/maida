# `maida diff`

Compares two stored runs, a stored run against a baseline, or a locally
captured Claude Code session against a baseline. Stored-run mode is an
inspection command and exits successfully after producing a diff. Capture mode
is a local policy gate: it normalizes and installs the selected capture, runs
the same assertions and structural comparison as `maida assert`, and renders
the same report used for PR comments. See [Regression
testing](../regression-testing.md) for the workflow.

**Usage:**

```bash
maida diff [TRACE_A] [TRACE_B] [--baseline FILE]
maida diff --capture SESSION_ID --baseline FILE [--policy FILE] [--format FORMAT]
```

In stored-run mode, exactly one of `TRACE_B` or `--baseline` must be provided.
Positional trace IDs cannot be combined with `--capture`.

**Arguments / options:**

| Argument/Option | Description |
|---|---|
| `TRACE_A` | First OTel trace ID or prefix. Defaults to the latest run when omitted |
| `TRACE_B` | Second OTel trace ID or prefix (mutually exclusive with `--baseline`) |
| `--baseline`, `-b` | Baseline JSON file to compare against (mutually exclusive with `TRACE_B`) |
| `--capture` | Raw Claude Code session ID to normalize, install, and gate |
| `--policy` | Policy YAML for capture mode. Defaults to `.maida/policy.yaml` when present |
| `--format`, `-f` | Capture output format: `text` (default), `json`, or `markdown` |

**Examples:**

```bash
# Compare two runs
maida diff a1b2c3d4 e5f6a7b8

# Compare the latest run against a baseline
maida diff --baseline .maida/baselines/my_agent.json

# Gate a locally captured Claude Code session before pushing
maida diff --capture "$CLAUDE_SESSION_ID" \
  --baseline .maida/baselines/my_agent.json \
  --policy .maida/policy.yaml \
  --format json
```

**Stored-run exit codes:** `0` successful inspection; `2` run or baseline not
found; `10` internal error.

**Capture-mode exit codes:** `0` policy pass; `1` policy regression; `2`
missing/invalid arguments, capture, baseline, or policy; `10` capture import,
evaluation, or other runtime failure. Capture selection and import notices go
to stderr; stdout contains only the requested report format.

**Text output sections:**

- **Summary** — metric-by-metric comparison with percentage change (e.g. `step_count: 38 -> 42 (+11%)`)
- **Tool path** — compact baseline/current tool-call sequences, with long paths truncated in the middle
- **Tool call changes** — new (`+`), removed (`-`), repeated (`~`), and reordered (`!`) tool calls
- **Event type distribution** — per-event-type counts with percentage change

## Reusable stored-run evaluator

Automation that already has a normalized trace can use the same evaluator
without invoking Typer:

```python
from maida.evaluation import evaluate_stored_run_against_baseline

evaluation = evaluate_stored_run_against_baseline(
    trace_id,
    loaded_baseline,
    policy,
    config,
)
report = evaluation.report
structural_diff = evaluation.diff
markdown = evaluation.render("markdown", baseline_path="baseline.json")
```

The evaluator accepts in-memory baseline and policy objects. File loading,
capture import, and progress notices remain caller concerns, which keeps it
suitable for scenario aggregation and other non-CLI workflows.
