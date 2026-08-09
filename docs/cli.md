# CLI

The `maida` CLI runs the bundled demo, scaffolds a project, validates and imports existing traces, lists runs, starts the local viewer, exports runs to JSON, updates baselines intentionally, and gates individual runs or completed windows against baselines. Storage is under `~/.maida/` by default (overridable with `MAIDA_DATA_DIR`). For all configuration options and precedence, see the [configuration reference](reference/config.md).

Commands that take a run ID (`assert`, `baseline`, `accept`, `export`, `diff`) default to the **latest run** when the ID is omitted. The selected run is announced on stderr so stdout stays machine-readable.

---

## `maida validate-trace`

Validates an externally emitted native Maida trace without installing or
modifying it.

```bash
maida validate-trace PATH [--json]
```

`PATH` is either a directory containing `meta.json` and `spans.jsonl`, or that
directory's `meta.json`. Text mode prints a concise success result to stdout or
actionable diagnostics to stderr. `--json` keeps stdout machine-readable for
both success and failure, with `valid`, trace metadata, span count, and
sanitized diagnostics.

**Exit codes:** `0` valid; `1` invalid trace content; `2` missing, unreadable,
or unsupported input path; `10` unexpected validator failure.

See [Emit Maida traces without an SDK](reference/trace-emitter.md) for the
required fields, JSON Schemas, enrichment rules, and subthread topology.

---

## `maida capture claude-code`

Starts a local OTLP HTTP/protobuf receiver for Claude Code logs and beta traces.

```bash
maida capture claude-code [--host 127.0.0.1] [--port 4318]
```

The receiver provides `/healthz`, `/v1/logs`, and `/v1/traces`, validates and
redacts batches before writing, and stores source captures under
`~/.maida/captures/claude-code/`. Progress is written to stderr. See
[Capture Claude Code telemetry](claude-code.md) for exporter configuration and
the local storage contract.

**Exit codes:** `0` after normal shutdown; `10` receiver startup/runtime error.

---

## `maida capture claude-hook`

Reads exactly one supported Claude Code command-hook payload from stdin and
appends it to the hashed session capture without returning a hook decision.

```bash
maida capture claude-hook
```

The handler supports `SessionStart`, `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `PermissionDenied`, and `SessionEnd`. Successful handling
writes nothing to stdout or stderr. The command is passive: it never returns
allow, deny, retry, or context fields and never uses Claude's blocking exit
code 2. `SessionEnd` closes and imports the segment automatically; abrupt
segments remain available to `maida import claude-code`.

**Exit codes:** `0` captured; `10` invalid payload, conflicting delivery,
automatic-import failure, or internal error.

See [Command-hook fallback](claude-code.md#command-hook-fallback) for the compact
project settings configuration, lifecycle segmentation, and privacy contract.

---

## `maida import claude-code`

Normalizes one local Claude Code capture into the current Maida trace schema
and atomically installs it in the run store.

```bash
maida import claude-code --session-id SESSION_ID [--segment latest] [--json]
```

`--session-id` is hashed before path lookup. `--segment` selects an immutable
capture segment and defaults to the latest. Identical re-imports are no-ops;
changed source data never overwrites an existing deterministic run. Latest
segment notices use stderr so `--json` keeps stdout machine-readable.

**Exit codes:** `0` imported or already present; `2` missing/invalid capture;
`10` normalization or storage failure.

See [Capture Claude Code telemetry](claude-code.md) for receiver setup,
normalization rules, and storage layout.

---

## `maida scenario run`

Runs pinned headless Claude Code prompts in isolated fixture workspaces and
evaluates their captures against checked-in baselines.

```bash
maida scenario run [MANIFEST] [--scenario ID] [--format text|json|markdown]
```

`MANIFEST` defaults to `.maida/scenarios.yaml`. The runner validates the whole
manifest and local environment before invoking Claude. Each selected scenario
gets a fresh temporary workspace containing only its declared Git-tracked
fixture files, a unique non-persisted Claude session, an ephemeral loopback
OTLP receiver, its own native USD cap, turn cap, and process-group timeout.
The exact project settings and strict MCP configuration are passed explicitly;
permissions use `dontAsk` and dangerous permission bypass is never enabled.

Reports contain no raw Claude stdout or stderr. Per-scenario status is `pass`,
`assertion_failed`, or `agent_failed`.

**Exit codes:** `0` all pass; `1` one or more assertion failures; `2` invalid
manifest, selection, config, executable, or version preflight; `10` any agent,
capture-import, or runtime failure. Agent failure takes precedence over
assertion failure when multiple scenarios run.

See [Run isolated scenarios](claude-code.md#run-isolated-scenarios) for the
manifest schema and a complete example.

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
| `--github` | Also write `.github/workflows/maida.yml` using the current `maida-ai/maida-assert@main` gate and the `maida-ai/maida-assert/accept-command@main` handler |
| `--force` | Overwrite existing files |

**Files written:**

- `.maida/policy.yaml` — strict v2 starter with invariant contracts, directional measured tolerances, and a three-trial report-only pass-rate metric
- `.github/workflows/maida.yml` (with `--github`) — PR check running your traced agent and posting the regression report as a sticky comment; also handles authorized `/maida accept [optional reason]` comments and rechecks an accepted PR-head commit; pins `actions/checkout@v7` and currently tracks `maida-ai/maida-assert@main`, with the command handler at `maida-ai/maida-assert/accept-command@main`

Edit the generated `MAIDA_AGENT_SCRIPT` value for your entrypoint. After
committing a baseline, set `MAIDA_BASELINE` to its tracked path; leaving it blank
keeps the accept command inactive with a polite configuration response. Baseline
write-back supports same-repository PR branches only and requires the commenter
to have repository write access.

**Exit codes:** `0` success; `10` internal error.

---

## `maida import langfuse`

Imports existing Langfuse traces through the read-only v2 observations API and
stores validated Maida runs locally. One Langfuse trace becomes one Maida run.

**Usage:**

```bash
maida import langfuse --trace-id TRACE_ID [--base-url URL] [--json]
maida import langfuse --from TIME --to TIME [FILTERS] [--json]
```

**Selection options:**

| Option | Description |
|---|---|
| `--trace-id` | Import one complete Langfuse trace; mutually exclusive with range options |
| `--from` | Inclusive, timezone-aware start of range discovery |
| `--to` | Exclusive, timezone-aware end of range discovery |
| `--trace-name` | Restrict range discovery to one recurring trace name |
| `--session-id` | Restrict range discovery to one Langfuse session |
| `--environment` | Restrict discovery to an environment; repeat for multiple values |
| `--base-url` | Override `LANGFUSE_BASE_URL` for cloud region or self-hosting |
| `--json` | Print a machine-readable import summary |

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` before running the command.
`LANGFUSE_TIMEOUT` optionally controls the request timeout. The command performs
only `GET /api/public/v2/observations` requests and writes only to local Maida
storage.

**Examples:**

```bash
maida import langfuse --trace-id 7f0d4a2c...
maida import langfuse --from 2026-08-01T00:00:00Z --to 2026-08-02T00:00:00Z --trace-name support-agent
```

**Exit codes:** `0` imported or already present; `2` invalid selection, no
matches, or only incomplete traces; `10` API, normalization, or storage failure.

See [Importing Langfuse traces](langfuse.md) for the mapping, pagination,
redaction, idempotence, and self-hosted ClickHouse reference.

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

With `--json`, output shape: `{"spec_version":"0.2.0","run_id":"...","url":"http://127.0.0.1:8712/?run_id=...","status":"serving"}`.

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

---

## `maida accept`

Updates an existing baseline from a completed run when a behavior change is intentional. Use it after inspecting `maida diff` and `maida view`; do not accept a regression just to make CI pass.

**Usage:**

```bash
maida accept [TRACE_ID] --baseline PATH --reason TEXT
```

**Arguments / options:**

| Argument/Option | Default | Description |
|---|---|---|
| `TRACE_ID` | *(latest run)* | OTel trace ID or prefix to accept |
| `--baseline`, `-b` | *(required)* | Existing baseline JSON file to update |
| `--reason`, `--message`, `-m` | *(required)* | Human-readable reason for accepting the change |

**Examples:**

```bash
maida diff --baseline .maida/baselines/my_agent.json
maida view
maida accept --baseline .maida/baselines/my_agent.json --reason "expected retrieval tool split"
git diff .maida/baselines/my_agent.json
```

**Exit codes:** `0` baseline updated or already matched; `2` run or baseline not found, invalid baseline, invalid run, or missing reason; `10` internal error.

When the accepted run changes baseline behavior, the baseline JSON is rewritten
with the same structural fields produced by `maida baseline` plus an
`acceptance` provenance object. It records `accepted_by`, `accepted_at`, the
reason, Maida version, source run ID, source repository/PR/commit, an
accepted-run verdict summary, and the previous baseline source run ID and
SHA-256. Subsequent Markdown assertion reports render this block under
**Baseline provenance**.

In GitHub write-back jobs, Maida reads `GITHUB_ACTOR`, `GITHUB_REPOSITORY`,
`GITHUB_SERVER_URL`, `MAIDA_PR_NUMBER`, and `MAIDA_EXPECTED_HEAD_SHA` so the
artifact identifies the approving user and exact PR revision. A local accept
records the local OS user and leaves unavailable PR/commit fields empty. If the
selected run already matches the baseline structurally, Maida prints
`no update written` and leaves the file untouched.

---

## `maida run`

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
| `--json-out` | - | Atomically write report schema `2.0.0` to a sidecar |

```bash
maida run my_agent.py --baseline .maida/baselines/my_agent.json \
  --policy .maida/policy.yaml --format markdown --json-out maida-report.json
```

Each trial must create exactly one completed trace. Exit `1` is reserved for FAIL; PASS and the provider-neutral INCONCLUSIVE verdict exit `0`, so CI consumers must read the JSON `verdict` rather than infer uncertainty from the process status. Missing inputs exit `2` and internal execution failures exit `10`.

## `maida extract`

Derives inactive, review-required gate drafts from a completed native trace
window without changing the source window or active `.maida` storage.

```bash
maida extract --window RUNS_DIR --out DRAFT_DIR [options]
```

| Argument/Option | Default | Description |
|---|---|---|
| `--window` | required | Native Maida `runs/` directory containing completed traces |
| `--out` | required | New directory for the atomic draft output |
| `--workflow` | every nonempty `run_name` | Exact workflow group; repeat for multiple groups |
| `--json` | `false` | Print `draft.json` content to stdout; notices remain on stderr |

The output contains `draft.json` plus one commented policy-v2 and immutable
baseline pair per workflow. It remains inactive until human review and an
intentional copy into the repository's gate paths. Exit `0` means extraction and
self-consistency verification succeeded, `2` means invalid input or selection,
and `10` means extraction or persistence failed without installing partial
output.

See [Extract reviewable gate drafts](extraction.md) for grouping, privacy,
artifact layout, policy candidates, and review guidance.

## `maida drift`

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
| `--json-out` | - | Atomically write report schema `2.0.0` to a sidecar |

```bash
maida drift --window /srv/agents/orders/runs \
  --baseline .maida/baselines/orders-agent.json \
  --policy .maida/policy.yaml \
  --format markdown --json-out orders-agent-drift.json
```

The baseline selects matching `run_name` values from a mixed-agent window. Run
the command once per baseline. Exit `0` means PASS or neutral INCONCLUSIVE,
`1` means FAIL, `2` means invalid input, and `10` means an internal error.

See [Scheduled behavioral regression checks](scheduled-checks.md) for sample
validation, scheduler guidance, canary promotion, and planned input adapters.

## `maida assert`

Evaluates one already-completed trace through the legacy single-run assertion interface. New tier-aware and statistical gates should use `maida run`; `maida assert` remains for v1 compatibility and direct trace inspection.

**Usage:**

```bash
maida assert [TRACE_ID] [options]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|---|---|---|
| `TRACE_ID` | *(latest run)* | OTel trace ID or prefix to check |
| `--baseline`, `-b` | - | Baseline JSON file to compare against |
| `--policy` | `.maida/policy.yaml` (auto-detected) | Policy file; v1 loads with a deprecation warning |
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

Compares two stored runs, a stored run against a baseline, or a locally
captured Claude Code session against a baseline. Stored-run mode is an
inspection command and exits successfully after producing a diff. Capture mode
is a local policy gate: it normalizes and installs the selected capture, runs
the same assertions and structural comparison as `maida assert`, and renders
the same report used for PR comments. See [Regression
testing](regression-testing.md) for the workflow.

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

### Reusable stored-run evaluator

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
