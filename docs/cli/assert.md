# `maida assert`

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
| `--ignore-check` | - | Skip a named check. Repeatable. |
| `--format`, `-f` | `text` | Output format: `text`, `json`, or `markdown` |

**Precedence:** CLI flags override the policy file, which overrides defaults. See the [Policy YAML reference](../reference/policy.md) for the full override rules and threshold semantics.

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
