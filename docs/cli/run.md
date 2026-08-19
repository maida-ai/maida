# `maida run`

Runs a traced Python agent repeatedly in fresh copies of the current tracked and nonignored workspace, evaluates every resulting trace with the selected policy, and aggregates the outcomes into PASS, FAIL, or INCONCLUSIVE.

```bash
maida run AGENT_SCRIPT [options]
```

| Argument/Option | Default | Description |
|---|---|---|
| `AGENT_SCRIPT` | required | Traced Python script inside the current Git workspace |
| `--trials` | policy value (`3`) | Number of isolated subprocess trials |
| `--confidence-level` | policy value (`0.95`) | One-sided Wilson coverage (`z = 1.645` at 0.95) |
| `--pass-rate-threshold` | policy value (`0.90`) | Required pass rate |
| `--baseline`, `-b` | - | Baseline JSON applied to every trial |
| `--policy` | `.maida/policy.yaml` | Assertion and statistical gate settings |
| `--format`, `-f` | `text` | `text`, `json`, or verdict-first `markdown` |
| `--fail-fast` / `--no-fail-fast` | policy value (`true`) | Stop on an irreversible blocking failure, or force the full fixed-N sample |
| `--json-out` | - | Atomically write report schema `2.0.1` to a sidecar |

```bash
maida run my_agent.py --baseline .maida/baselines/my_agent.json \
  --policy .maida/policy.yaml --format markdown --json-out maida-report.json
```

Each trial must create exactly one completed trace. Exit `1` is reserved for FAIL; PASS and the provider-neutral INCONCLUSIVE verdict exit `0`, so CI consumers must read the JSON `verdict` rather than infer uncertainty from the process status. Missing inputs exit `2` and internal execution failures exit `10`.
