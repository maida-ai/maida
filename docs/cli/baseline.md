# `maida baseline`

Captures a baseline snapshot from a completed run. The snapshot records structural metrics (event counts, tool path, token usage, duration, etc.) that `maida assert` can later compare against. See [Regression testing](../regression-testing.md) for the full workflow.

**Usage:**

```bash
maida baseline [TRACE_ID] [--out PATH]
maida baseline --from-report REPORT.json [--out PATH]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|---|---|---|
| `TRACE_ID` | *(latest run)* | OTel trace ID or prefix for legacy single-run capture |
| `--from-report` | - | Build an immutable multi-trial sample from report v2; mutually exclusive with `TRACE_ID` |
| `--out`, `-o` | `.maida/baselines/<run_name>.json` | Output path for the baseline JSON file |

**Examples:**

```bash
maida run my_agent.py --trials 25 --no-fail-fast --json-out report.json
maida baseline --from-report report.json --out .maida/baselines/my_agent.json
maida baseline a1b2c3d4 --out baselines/legacy-single-run.json
```

**Exit codes:** `0` success; `2` run not found; `10` internal error.

Report-based capture stores the raw per-trial numeric and invariant vectors, an environment fingerprint, and structural signatures deduplicated with counts. The sample is immutable and never accumulates across gate runs. Check it into version control as part of the reviewed diff.
