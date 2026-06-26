# Regression testing

Maida ships three CLI commands that turn traced runs into behavioral regression gates: **baseline**, **assert**, and **diff**. Together they let you capture a known-good run, check future runs against it, and drill into what changed when something breaks.

---

## Why

Agent behavior is non-deterministic. A prompt tweak, model upgrade, or tool change can silently increase token usage, add unexpected tool calls, or introduce loops. `maida assert` gives you a one-line check - locally or in CI - that catches these regressions before they ship or become incidents.

---

## Workflow overview

```
1. Run your agent              python your_agent.py
2. Capture a baseline          maida baseline
3. Run the agent again         python your_agent.py
4. Assert against baseline     maida assert --baseline .maida/baselines/my_agent.json
5. If it fails, diff           maida diff --baseline .maida/baselines/my_agent.json
```

`baseline`, `assert`, and `diff` default to the latest run when no run ID is given; pass an ID or short prefix to target a specific run.

To see the whole workflow on canned data first, run `maida demo --regression`.

---

## Step 1: Capture a baseline

After a successful run that represents the expected behavior:

```bash
maida baseline <TRACE_ID>
```

This creates a JSON snapshot at `.maida/baselines/<run_name>.json` (or the trace ID if no name was set). Use `--out` to control the path:

```bash
maida baseline a1b2c3d4 --out baselines/support_agent_v1.json
```

**What gets captured:**

| Field | Description |
|---|---|
| `schema_version` | Baseline format version (`"0.1"`) |
| `source_run_id` | The run this baseline was created from |
| `source_run_name` | Run name (if set) |
| `summary` | Aggregate metrics: total events, LLM calls, tool calls, errors, loop warnings, duration, tokens |
| `tool_path` | Sorted list of unique tool names used |
| `tool_call_counts` | Per-tool invocation counts |
| `llm_models_used` | Models seen in LLM_CALL events |
| `event_type_sequence` | Ordered list of event types |
| `guardrail_events` | Any guardrail-triggered events |
| `final_status` | Run status (`"ok"` or `"error"`) |

Check the baseline file into version control so the team shares the same reference point.

---

## Step 2: Assert against a baseline

```bash
maida assert <TRACE_ID> --baseline .maida/baselines/my_agent.json
```

Exit codes: `0` = all checks pass, `1` = one or more checks failed, `2` = run or baseline not found, `10` = internal error.

### What gets checked

Checks are controlled by the **assertion policy** — a combination of a policy YAML file and CLI flags. By default, if a baseline is provided, every numeric metric is compared with a 50% tolerance. You can tighten or customize this with a policy file or CLI flags.

### Standalone thresholds (no baseline needed)

You can assert without a baseline by setting hard caps:

```bash
maida assert <TRACE_ID> --max-steps 80 --max-tool-calls 30 --no-loops
```

### Combining baseline and thresholds

When both a baseline and a `max_*` threshold are set, the effective limit is the **lesser** of the two:

```
limit = min(baseline_value * (1 + tolerance), max_value)
```

See the [Policy YAML reference](reference/policy.md#how-thresholds-work) for the full decision table.

---

## Step 3: Use a policy file

Instead of passing many CLI flags, commit a `.maida/policy.yaml` file:

```yaml
assert:
  max_steps: 80
  step_tolerance: 0.2
  max_tool_calls: 30
  no_loops: true
  no_new_tools: true
  expect_status: ok
```

`maida assert` auto-detects `.maida/policy.yaml` in the current directory. To use a different path:

```bash
maida assert <TRACE_ID> --baseline baseline.json --policy ci-policy.yaml
```

**Precedence:** CLI flags > policy file > defaults. See the [full policy reference](reference/policy.md) for all fields, threshold semantics, and override rules.

---

## Output formats

Use `--format` (`-f`) to choose the output format.

### Text (default)

```bash
maida assert <TRACE_ID> --baseline baseline.json
```

```
  ✓ step_count [no_regression]: 42 steps (baseline: 38, tolerance: 50%)
  ✓ tool_calls [no_regression]: 12 tool calls (baseline: 10, tolerance: 50%)
  ✗ no_loops [loop_detected]: 2 loop warning(s) detected
  ✓ expect_status [no_regression]: status is 'ok'

RESULT: FAILED (1 of 4 checks failed)
```

### JSON

```bash
maida assert <TRACE_ID> --baseline baseline.json --format json
```

```json
{
  "run_id": "a1b2c3d4-...",
  "baseline_run_id": "e5f6a7b8-...",
  "passed": false,
  "reason_codes": ["loop_detected"],
  "results": [
    {
      "check_name": "step_count",
      "passed": true,
      "reason_code": "no_regression",
      "message": "42 steps (baseline: 38, tolerance: 50%)",
      "expected": "57",
      "actual": "42"
    },
    {
      "check_name": "no_loops",
      "passed": false,
      "reason_code": "loop_detected",
      "message": "2 loop warning(s) detected",
      "expected": null,
      "actual": "2"
    }
  ]
}
```

### Markdown

Designed for GitHub PR comments and step summaries:

```bash
maida assert --baseline baseline.json --format markdown
```

```markdown
## ❌ Maida gate: agent behavior regressed

**1 of 4 checks failed** · run `a1b2c3d4` vs baseline `e5f6a7b8`

### Failed checks by reason

#### `loop_detected`

| Check | Expected | Actual | Details |
|---|---|---|---|
| ❌ `no_loops` | — | 2 | 2 loop warning(s) detected |

<details>
<summary>✅ 3 passing checks</summary>
...
</details>

### What changed vs baseline

| Metric | Baseline | Current | Change |
|---|---|---|---|
| tool_calls | 10 | 14 | +40% |
| loop_warnings | 0 | 2 | NEW |

**Tool changes:**
- ➕ `web_search` — new tool, not in baseline
```

The report leads with the verdict, groups failed checks by stable reason code, collapses passing checks, and — when a baseline is provided — embeds the structural diff and a copy-pasteable local-repro snippet.

---

## Step 4: Drill into failures with diff

When `maida assert` fails, use `maida diff` to see exactly what changed.

### Diff against a baseline

```bash
maida diff <TRACE_ID> --baseline .maida/baselines/my_agent.json
```

### Diff two runs directly

```bash
maida diff <RUN_A> <RUN_B>
```

### Sample output

```
Run comparison: a1b2c3d4 vs e5f6a7b8

Summary:
  total_events: 38 -> 42 (+11%)
  tool_calls: 10 -> 14 (+40%)
  loop_warnings: 0 -> 2 (NEW)

Tool path changes:
  + web_search (new)

Event type distribution:
  LLM_CALL: 8 -> 8
  TOOL_CALL: 10 -> 14 (+40%)
  LOOP_WARNING: 0 -> 2 (NEW)
```

The diff shows summary-level metric changes, new or removed tools, and shifts in the event type distribution.

---

## GitHub Actions example

The easiest path is the [maida-assert action](https://github.com/maida-ai/maida-assert) — scaffold it with `maida init --github`. It runs your traced agent, asserts the run, and posts the regression report as a sticky PR comment.

To wire it up by hand instead, run your agent in CI, then assert against the checked-in baseline (`maida assert` picks up the latest run automatically):

```yaml
- name: Run agent
  run: python my_agent.py

- name: Assert agent behavior
  run: |
    maida assert \
      --baseline .maida/baselines/my_agent.json \
      --format markdown >> "$GITHUB_STEP_SUMMARY"
```

If the assertion fails, the step exits with code 1 and the markdown report appears in the GitHub Actions step summary.

---

## Related docs

- [Policy YAML reference](reference/policy.md) — all assertion fields, threshold semantics, CLI-to-YAML mapping
- [CLI reference](cli.md) — `baseline`, `assert`, `diff` command details
- [Guardrails](guardrails.md) — runtime limits (separate from post-hoc assertions)
