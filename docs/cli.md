# CLI reference

The `maida` CLI captures, inspects, compares, and gates agent runs. Commands that accept a trace ID default to the latest run when the ID is omitted; the selected run is announced on stderr so stdout remains machine-readable.

## Start and configure

| Command | Use it to |
|---|---|
| [`maida demo`](cli/demo.md) | Run the deterministic first-run or regression story |
| [`maida init`](cli/init.md) | Scaffold policy and optional GitHub Actions configuration |
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
