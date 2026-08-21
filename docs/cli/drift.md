# `maida drift`

Evaluates a completed native Maida trace window against one agent baseline
without executing the agent or changing the source traces.

```bash
maida drift --window RUNS_DIR --baseline BASELINE [options]
```

| Argument/Option | Default | Description |
|---|---|---|
| `--window` | required | Native Maida `runs/` directory containing completed traces |
| `--baseline`, `-b` | required | Baseline JSON for one agent; directories are not yet accepted |
| `--policy` | `.maida/policy.yaml` | Tier-aware gate policy |
| `--agent` | baseline `source_run_name` | Explicit agent selector for legacy baselines |
| `--format`, `-f` | `text` | `text`, `json`, or verdict-first `markdown` |
| `--json-out` | - | Atomically write report schema `2.0.1` to a sidecar |

```bash
maida drift --window /srv/agents/orders/runs \
  --baseline .maida/baselines/orders-agent.json \
  --policy .maida/policy.yaml \
  --format markdown --json-out orders-agent-drift.json
```

The baseline selects matching `run_name` values from a mixed-agent window. Run
the command once per baseline. Exit `0` means PASS or neutral INCONCLUSIVE,
`1` means FAIL, `2` means invalid input, and `10` means an internal error.

See [Scheduled behavioral regression checks](../scheduled-checks.md) for sample
validation, scheduler guidance, canary promotion, and planned input adapters.
