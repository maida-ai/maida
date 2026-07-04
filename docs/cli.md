# CLI

The `maida` CLI runs the bundled demo, scaffolds a project, lists runs, starts the local viewer, exports runs to JSON, and gates runs against baselines. Storage is under `~/.maida/` by default (overridable with `MAIDA_DATA_DIR`). For all configuration options and precedence, see the [configuration reference](reference/config.md).

Commands that take a run ID (`assert`, `baseline`, `export`, `diff`) default to the **latest run** when the ID is omitted. The selected run is announced on stderr so stdout stays machine-readable.

---

## `maida demo`

Runs a bundled simulated customer-support agent and records a trace. No network, no API keys; all LLM/tool data is canned and nothing leaves your machine.

**Usage:**

```bash
maida demo [--regression]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--regression` | Full story: baseline a known-good run, run a "refactored" agent that loops, calls a new tool, and burns more tokens, then show the failing gate report and a PR-comment preview. Writes the baseline to `.maida/baselines/demo-support-agent.json`. |

**Examples:**

```bash
maida demo               # one traced run; inspect it with `maida view`
maida demo --regression  # watch the gate catch a bad refactor
```

**Exit codes:** `0` success (including when the demo gate intentionally fails); `10` internal error.

---

## `maida init`

Scaffolds Maida configuration in the current directory. Never overwrites existing files unless `--force` is given; safe to re-run.

**Usage:**

```bash
maida init [--github] [--force]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--github` | Also write `.github/workflows/maida.yml` using the pinned [`maida-ai/maida-assert@V4`](https://github.com/maida-ai/maida-assert/releases/tag/V4) action |
| `--force` | Overwrite existing files |

**Files written:**

- `.maida/policy.yaml` — commented starter policy with 50% baseline tolerances; strict checks such as `no_loops`, `no_guardrails`, `no_new_tools`, and `expect_status: ok` are shown as opt-ins
- `.github/workflows/maida.yml` (with `--github`) — PR check running your traced agent and posting the regression report as a sticky comment; pins `actions/checkout@v7` and `maida-ai/maida-assert@V4`

**Exit codes:** `0` success; `10` internal error.

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

**Text columns:** trace_id (short; displayed in the compatibility `run_id` column), run_name, started_at, duration_ms, llm_calls, tool_calls, status.

---

## `maida view`

Starts the local viewer server and optionally opens the browser. Default bind: `127.0.0.1:8712`.

**Usage:**

```bash
maida view [TRACE_ID] [--host HOST] [--port PORT] [--no-browser] [--json]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|-----------------|---------|-------------|
| `TRACE_ID` | (latest) | Run to view; can be a full 32-hex-character OTel trace ID or a prefix |
| `--host`, `-H` | 127.0.0.1 | Bind host |
| `--port`, `-p` | 8712 | Bind port |
| `--no-browser` | - | Do not open the browser; only start the server |
| `--json` | - | Print the selected trace ID in the `run_id` compatibility field, url, and status as JSON, then start server |

**Examples:**

```bash
maida view
maida view a1b2c3d4
maida view --port 9000 --no-browser
maida view --json
```

**Exit codes:** `0` success; `2` run not found (or no runs); `10` internal error.

With `--json`, output shape: `{"spec_version":"0.2","run_id":"...","url":"http://127.0.0.1:8712/?run_id=...","status":"serving"}`.

---

## `maida export`

Exports one run to a single JSON file (run metadata + events array).

**Usage:**

```bash
maida export [TRACE_ID] --out FILE
```

**Arguments / options:**

| Argument/Option | Description |
|---|---|
| `TRACE_ID` | Run to export; can be a full 32-hex-character OTel trace ID or a prefix. Defaults to the latest run when omitted |
| `--out`, `-o` | Output file path (JSON) |

**Examples:**

```bash
maida export --out run-export.json   # latest run
maida export a1b2c3d4 -o ./exports/run-export.json
```

**Exit codes:** `0` success; `2` run not found; `10` internal error.

Output file contains: `spec_version`, `run` (run metadata), `events` (array of event objects).

---

## `maida baseline`

Captures a baseline snapshot from a completed run. The snapshot records structural metrics (event counts, tool path, token usage, duration, etc.) that `maida assert` can later compare against. See [Regression testing](regression-testing.md) for the full workflow.

**Usage:**

```bash
maida baseline [TRACE_ID] [--out PATH]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|---|---|---|
| `TRACE_ID` | *(latest run)* | OTel trace ID or prefix to snapshot |
| `--out`, `-o` | `.maida/baselines/<run_name>.json` | Output path for the baseline JSON file |

**Examples:**

```bash
maida baseline                       # snapshot the latest run
maida baseline a1b2c3d4 --out baselines/support_agent_v1.json
```

**Exit codes:** `0` success; `2` run not found; `10` internal error.

The output file is a JSON object containing `schema_version`, `source_run_id`, summary metrics, `tool_path`, ordered `tool_call_sequence`, `tool_call_counts`, `llm_models_used`, `event_type_sequence`, and `final_status`. Check it into version control to share the baseline with your team.

---

## `maida assert`

Asserts that a completed run meets behavioral policy checks. Returns exit code `0` when all checks pass and `1` when any check fails, making it suitable for CI gates.

**Usage:**

```bash
maida assert [TRACE_ID] [options]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|---|---|---|
| `TRACE_ID` | *(latest run)* | OTel trace ID or prefix to check |
| `--baseline`, `-b` | - | Baseline JSON file to compare against |
| `--policy` | `.maida/policy.yaml` (auto-detected) | Policy YAML file with assertion thresholds |
| `--max-steps` | - | Max total events allowed |
| `--min-steps` | - | Min total events required |
| `--step-tolerance` | `0.5` | Fractional tolerance for step count |
| `--max-tool-calls` | - | Max tool calls allowed |
| `--min-tool-calls` | - | Min tool calls required |
| `--tool-call-tolerance` | `0.5` | Fractional tolerance for tool calls |
| `--no-new-tools` | `false` | Fail if run uses tools not in baseline |
| `--no-loops` | `false` | Fail if any LOOP_WARNING present |
| `--no-guardrails` | `false` | Fail if any guardrail was triggered |
| `--max-cost-tokens` | - | Max total tokens allowed |
| `--min-cost-tokens` | - | Min total tokens required |
| `--cost-tolerance` | `0.5` | Fractional tolerance for token cost |
| `--max-duration-ms` | - | Max run duration in ms |
| `--min-duration-ms` | - | Min run duration in ms |
| `--duration-tolerance` | `0.5` | Fractional tolerance for duration |
| `--expect-status` | - | Expected run status (`ok` or `error`) |
| `--format`, `-f` | `text` | Output format: `text`, `json`, or `markdown` |

**Precedence:** CLI flags override the policy file, which overrides defaults. See the [Policy YAML reference](reference/policy.md) for the full override rules and threshold semantics.

**Examples:**

```bash
# Assert the latest run against a baseline with default tolerances
maida assert --baseline .maida/baselines/my_agent.json

# Assert a specific run with standalone thresholds (no baseline)
maida assert a1b2c3d4 --max-steps 80 --max-tool-calls 30 --no-loops

# Assert using a policy file
maida assert --baseline baseline.json --policy ci-policy.yaml

# Markdown output for GitHub PR comments / step summaries
maida assert --baseline baseline.json --format markdown
```

**Exit codes:** `0` all checks passed; `1` one or more checks failed; `2` run or baseline not found; `10` internal error.

Each assertion result includes a stable `reason_code`; JSON output also includes a top-level `reason_codes` array for failed checks. Markdown output starts with a pass/fail verdict, shows **Top behavior changes** when a baseline diff is available, groups failed checks by reason code, and includes concise next steps plus a local-repro snippet. The text report appends the structural diff on failure.

---

## `maida diff`

Compares two runs, or a run against a baseline, showing structural differences in summary metrics, tool path, and event type distribution. Useful for understanding what changed when `maida assert` reports a failure. See [Regression testing](regression-testing.md) for the workflow.

**Usage:**

```bash
maida diff [TRACE_A] [TRACE_B] [--baseline FILE] [--format FORMAT]
```

Exactly one of `TRACE_B` or `--baseline` must be provided.

**Arguments / options:**

| Argument/Option | Description |
|---|---|
| `TRACE_A` | First OTel trace ID or prefix. Defaults to the latest run when omitted |
| `TRACE_B` | Second OTel trace ID or prefix (mutually exclusive with `--baseline`) |
| `--baseline`, `-b` | Baseline JSON file to compare against (mutually exclusive with `TRACE_B`) |
| `--format`, `-f` | Output format: `text` (default) |

**Examples:**

```bash
# Compare two runs
maida diff a1b2c3d4 e5f6a7b8

# Compare the latest run against a baseline
maida diff --baseline .maida/baselines/my_agent.json
```

**Exit codes:** `0` success; `2` run or baseline not found; `10` internal error.

**Text output sections:**

- **Summary** — metric-by-metric comparison with percentage change (e.g. `step_count: 38 -> 42 (+11%)`)
- **Tool path** — compact baseline/current tool-call sequences, with long paths truncated in the middle
- **Tool call changes** — new (`+`), removed (`-`), repeated (`~`), and reordered (`!`) tool calls
- **Event type distribution** — per-event-type counts with percentage change
