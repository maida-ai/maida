# CLI reference

The `maida` CLI captures, inspects, compares, and gates agent runs. Commands that accept a trace ID default to the latest run when the ID is omitted; the selected run is announced on stderr so stdout remains machine-readable.

## Run selection after capture setup

`maida init` attaches capture without changing ordinary SDK/Python commands. `list`, `view`, `export`, `baseline`, `accept`, `diff`, starter `--from-run latest` and baseline-gated `assert` retain their configured run selection, including after detach. A baseline's source ID does not redirect candidate selection.

`maida check` automatically selects the newest captured Claude task in the current repository while capture is enabled. It requires finished, useful evidence, uses three first-run checks independently of policy, and never falls back to unrelated SDK runs. `maida assert` retains ordinary selection regardless of its assertion flags. Explicit Claude trace IDs and prefixes resolve automatically in this repository, including after detach; no storage option is needed. Bare `maida view` retains ordinary selection; use the exact viewer command printed by `check` to open that captured task.

```bash
maida list
maida assert --baseline .maida/baselines/python-agent.json
maida check
maida view RUN_ID                # inspect the task identified in its report
```

## Exit codes

Exit codes are a stable contract; CI can branch on them.

| Code | Meaning |
|---|---|
| `0` | Success. For gate commands: PASS or INCONCLUSIVE. |
| `1` | The gate failed: behavior regressed beyond policy. |
| `2` | Not found, or an invalid selection or argument. |
| `10` | Internal error. |

Progress messages and run-selection notices go to stderr, so stdout stays
machine-readable when you redirect JSON or Markdown output.

## Start and configure

| Command | Use it to |
|---|---|
| [`maida demo`](cli/demo.md) | Run the deterministic first-run or regression story |
| [`maida init`](cli/init.md) | Set up local capture, then draft and review an observed contract |
| [`maida check`](cli/check.md) | Check your latest captured task and print its viewer command |
| [`maida detach`](cli/detach.md) | Remove repository capture hooks after preview and confirmation |
| [`maida onboarding`](onboarding-measurement.md) | Measure the local setup-to-first-report funnel and human effort |
| [`maida view`](cli/view.md) | Open the local execution timeline |
| [`maida list`](cli/list.md) | List recent local runs |

## Capture and import

| Command | Use it to |
|---|---|
| [`maida capture claude-code`](cli/capture-claude-code.md) | Receive local Claude Code OTLP telemetry |
| [`maida capture claude-hook`](cli/capture-claude-hook.md) | Append one passive Claude Code hook event |
| [`maida import claude-code`](cli/import-claude-code.md) | Normalize a captured Claude Code session |
| [`maida import langfuse`](cli/import-langfuse.md) | Import completed traces through Langfuse's read-only API |
| [`maida validate-trace`](cli/validate-trace.md) | Validate an externally emitted native trace |
| [`maida export`](cli/export.md) | Write a portable JSON envelope for one run |

## Baseline and gate

| Command | Use it to |
|---|---|
| [`maida run`](cli/run.md) | Execute isolated trials and apply a policy-v2 gate |
| [`maida baseline`](cli/baseline.md) | Capture an immutable reviewed baseline |
| [`maida accept`](cli/accept.md) | Intentionally update a baseline with provenance |
| [`maida drift`](cli/drift.md) | Evaluate a completed trace window against a baseline |
| [`maida scenario run`](cli/scenario-run.md) | Gate isolated Claude Code scenarios |
| [`maida assert`](cli/assert.md) | Evaluate one completed trace through the legacy interface |
| [`maida diff`](cli/diff.md) | Inspect structural changes between runs or against a baseline |
| [`maida extract`](cli/extract.md) | Derive review-required policy and baseline drafts |

```{toctree}
:hidden:
:maxdepth: 1

cli/demo
cli/init
cli/check
cli/detach
cli/view
cli/list
cli/capture-claude-code
cli/capture-claude-hook
cli/import-claude-code
cli/import-langfuse
cli/validate-trace
cli/export
cli/run
cli/baseline
cli/accept
cli/drift
cli/scenario-run
cli/assert
cli/diff
cli/extract
```
