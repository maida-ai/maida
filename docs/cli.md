# CLI

The `maida` CLI lists runs, starts the local viewer, and exports runs to JSON. Storage is under `~/.maida/` by default (overridable with `MAIDA_DATA_DIR`). For all configuration options and precedence, see the [configuration reference](reference/config.md).

---

## `maida list`

Lists recent runs (by `started_at` descending).

**Usage:**

```bash
maida list [--limit N] [--json]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--limit`, `-n` | 20 | Maximum number of runs to list |
| `--json` | - | Output machine-readable JSON |

**Examples:**

```bash
maida list
maida list --limit 5
maida list --json
```

**Exit codes:** `0` success; `10` internal error.

**Text columns:** run_id (short), run_name, started_at, duration_ms, llm_calls, tool_calls, status.

---

## `maida view`

Starts the local viewer server and optionally opens the browser. Default bind: `127.0.0.1:8712`.

**Usage:**

```bash
maida view [RUN_ID] [--host HOST] [--port PORT] [--no-browser] [--json]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|-----------------|---------|-------------|
| `RUN_ID` | (latest) | Run to view; can be a short prefix (e.g. first 8 chars of UUID) |
| `--host`, `-H` | 127.0.0.1 | Bind host |
| `--port`, `-p` | 8712 | Bind port |
| `--no-browser` | - | Do not open the browser; only start the server |
| `--json` | - | Print run_id, url, status as JSON, then start server |

**Examples:**

```bash
maida view
maida view a1b2c3d4
maida view --port 9000 --no-browser
maida view --json
```

**Exit codes:** `0` success; `2` run not found (or no runs); `10` internal error.

With `--json`, output shape: `{"spec_version":"0.1","run_id":"...","url":"http://127.0.0.1:8712/?run_id=...","status":"serving"}`.

---

## `maida export`

Exports one run to a single JSON file (run metadata + events array).

**Usage:**

```bash
maida export RUN_ID --out FILE
```

**Arguments / options:**

| Argument/Option | Description |
|---|---|
| `RUN_ID` | Run to export; can be a short prefix (e.g. first 8 chars of UUID) |
| `--out`, `-o` | Output file path (JSON) |

**Examples:**

```bash
maida export a1b2c3d4-1234-5678-90ab-cdef12345678 --out run.json
maida export a1b2c3d4 -o ./exports/run.json
```

**Exit codes:** `0` success; `2` run not found; `10` internal error.

Output file contains: `spec_version`, `run` (run metadata), `events` (array of event objects).

---

## `maida baseline`

Captures a baseline snapshot from a completed run. The snapshot records structural metrics (event counts, tool path, token usage, duration, etc.) that `maida assert` can later compare against. See [Regression testing](regression-testing.md) for the full workflow.

**Usage:**

```bash
maida baseline RUN_ID [--out PATH]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|---|---|---|
| `RUN_ID` | *(required)* | Run ID or prefix to snapshot |
| `--out`, `-o` | `.maida/baselines/<run_name>.json` | Output path for the baseline JSON file |

**Examples:**

```bash
maida baseline a1b2c3d4
maida baseline a1b2c3d4 --out baselines/support_agent_v1.json
```

**Exit codes:** `0` success; `2` run not found; `10` internal error.

The output file is a JSON object containing `schema_version`, `source_run_id`, summary metrics, `tool_path`, `tool_call_counts`, `llm_models_used`, `event_type_sequence`, and `final_status`. Check it into version control to share the baseline with your team.

---

## `maida assert`

Asserts that a completed run meets behavioral policy checks. Returns exit code `0` when all checks pass and `1` when any check fails, making it suitable for CI gates.

**Usage:**

```bash
maida assert RUN_ID [options]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|---|---|---|
| `RUN_ID` | *(required)* | Run ID or prefix to check |
| `--baseline`, `-b` | - | Baseline JSON file to compare against |
| `--policy` | `.maida/policy.yaml` (auto-detected) | Policy YAML file with assertion thresholds |
| `--max-steps` | - | Max total events allowed |
| `--step-tolerance` | `0.5` | Fractional tolerance for step count |
| `--max-tool-calls` | - | Max tool calls allowed |
| `--tool-call-tolerance` | `0.5` | Fractional tolerance for tool calls |
| `--no-new-tools` | `false` | Fail if run uses tools not in baseline |
| `--no-loops` | `false` | Fail if any LOOP_WARNING present |
| `--no-guardrails` | `false` | Fail if any guardrail was triggered |
| `--max-cost-tokens` | - | Max total tokens allowed |
| `--cost-tolerance` | `0.5` | Fractional tolerance for token cost |
| `--max-duration-ms` | - | Max run duration in ms |
| `--duration-tolerance` | `0.5` | Fractional tolerance for duration |
| `--expect-status` | - | Expected run status (`ok` or `error`) |
| `--format`, `-f` | `text` | Output format: `text`, `json`, or `markdown` |

**Precedence:** CLI flags override the policy file, which overrides defaults. See the [Policy YAML reference](reference/policy.md) for the full override rules and threshold semantics.

**Examples:**

```bash
# Assert against a baseline with default tolerances
maida assert a1b2c3d4 --baseline .maida/baselines/my_agent.json

# Assert with standalone thresholds (no baseline)
maida assert a1b2c3d4 --max-steps 80 --max-tool-calls 30 --no-loops

# Assert using a policy file
maida assert a1b2c3d4 --baseline baseline.json --policy ci-policy.yaml

# Markdown output for GitHub step summaries
maida assert a1b2c3d4 --baseline baseline.json --format markdown
```

**Exit codes:** `0` all checks passed; `1` one or more checks failed; `2` run or baseline not found; `10` internal error.

---

## `maida diff`

Compares two runs, or a run against a baseline, showing structural differences in summary metrics, tool path, and event type distribution. Useful for understanding what changed when `maida assert` reports a failure. See [Regression testing](regression-testing.md) for the workflow.

**Usage:**

```bash
maida diff RUN_A [RUN_B] [--baseline FILE] [--format FORMAT]
```

Exactly one of `RUN_B` or `--baseline` must be provided.

**Arguments / options:**

| Argument/Option | Description |
|---|---|
| `RUN_A` | First run ID or prefix |
| `RUN_B` | Second run ID or prefix (mutually exclusive with `--baseline`) |
| `--baseline`, `-b` | Baseline JSON file to compare against (mutually exclusive with `RUN_B`) |
| `--format`, `-f` | Output format: `text` (default) |

**Examples:**

```bash
# Compare two runs
maida diff a1b2c3d4 e5f6a7b8

# Compare a run against a baseline
maida diff a1b2c3d4 --baseline .maida/baselines/my_agent.json
```

**Exit codes:** `0` success; `2` run or baseline not found; `10` internal error.

**Text output sections:**

- **Summary** — metric-by-metric comparison with percentage change (e.g. `tool_calls: 10 -> 14 (+40%)`)
- **Tool path changes** — new (`+`) and removed (`-`) tools
- **Event type distribution** — per-event-type counts with percentage change
