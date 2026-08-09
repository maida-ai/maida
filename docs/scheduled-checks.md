# Scheduled behavioral regression checks

`maida drift` applies the same policy and verdict rules as the pre-merge gate
to a completed window of production traces. It is a local batch command: the
traces, baseline, policy, and report remain inside your infrastructure.

## Run a scheduled check

Point `--window` at a native Maida `runs/` directory whose direct children are
completed trace directories containing `meta.json` and `spans.jsonl`:

```bash
maida drift \
  --window /srv/agents/orders/runs \
  --baseline .maida/baselines/orders-agent.json \
  --policy .maida/policy.yaml \
  --format markdown \
  --json-out /srv/reports/orders-agent-drift.json
```

The command reads the window without copying or changing its traces. It
validates the sample before evaluation and rejects empty, incomplete, corrupt,
or unsupported traces rather than silently dropping evidence.

The baseline's `source_run_name` selects the agent. A mixed directory can hold
several agents, but this release evaluates one baseline per invocation. For a
legacy baseline without `source_run_name`, pass `--agent` explicitly. Schedule
one command per agent when a job owns multiple baselines.

## Verdicts and scheduler behavior

Drift checks preserve the gate's current metric semantics:

- invariant checks fail on a violation;
- measured checks use their configured limit or baseline tolerance;
- distributional checks use the baseline sample's prediction bound;
- statistical checks use the configured one-sided Wilson verdict.

Markdown starts with PASS, FAIL, or INCONCLUSIVE and includes the same metric
evidence and baseline-change language as the gate report. JSON uses report
schema `2.0.0`, adds `report_kind: drift`, and records every source trace ID and
run status.

| Exit | Meaning |
| ---: | --- |
| `0` | PASS or neutral INCONCLUSIVE |
| `1` | confirmed FAIL |
| `2` | missing, invalid, incomplete, or ambiguous input |
| `10` | internal execution error |

Schedulers should retain the JSON report when they need to distinguish PASS
from INCONCLUSIVE, because both are non-failing process outcomes.

## Canary promotion

Capture and review the pre-change window as the canary baseline. Route the
change to one instance, collect a complete post-change window, and evaluate it
with `maida drift`. Complete promotion only when the canary report is PASS;
treat INCONCLUSIVE as a request for more evidence rather than a failure.

## Input compatibility

This release accepts native Maida run directories. External emitters that
follow the native trace contract can write their completed traces directly
beneath `runs/`; the same validation and drift analysis apply without an
emitter-specific adapter. `maida export` JSON window inputs remain a future
source format that can be added without changing the per-agent verdict body.

Baseline paths are files today. Directory fanout is intentionally reserved for
a later release; automation should invoke once per baseline and should not
invent a baseline-directory manifest in the meantime.
