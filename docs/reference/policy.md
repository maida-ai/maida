# Policy YAML reference

A **policy file** (`.maida/policy.yaml`) lets teams check assertion thresholds into version control so that `maida assert` applies consistent checks without long CLI flags.

---

## Resolution order

`maida assert` resolves the policy in this order:

1. **`--policy PATH`** flag on the CLI (explicit path)
2. **`.maida/policy.yaml`** in the current working directory (auto-detected)
3. **Empty policy** (all checks disabled)

CLI flags (`--max-steps`, `--no-loops`, etc.) are then merged on top. CLI values always win over the file — see [Override rules](#override-rules) below.

---

## File structure

The file is standard YAML. The policy loader reads a single top-level `assert:` mapping; all other top-level keys are ignored. Unknown keys inside `assert:` are also ignored.

```yaml
# .maida/policy.yaml
assert:
  # ... assertion fields go here ...
```

Requires **PyYAML** (`pip install pyyaml` or included in `maida[yaml]`). If PyYAML is not installed, `maida assert --policy ...` raises a clear `RuntimeError`.

---

## Assertion fields

All fields are optional. A check is **disabled** unless at least one relevant value is set (via baseline, policy file, or CLI flag).

### Numeric thresholds

| YAML key | Type | Default | CLI flag | Description |
|---|---|---|---|---|
| `max_steps` | `int` or `null` | `null` | `--max-steps` | Hard cap on total event count |
| `step_tolerance` | `float` | `0.5` | `--step-tolerance` | Fractional tolerance for step count when comparing against a baseline |
| `max_tool_calls` | `int` or `null` | `null` | `--max-tool-calls` | Hard cap on tool call count |
| `tool_call_tolerance` | `float` | `0.5` | `--tool-call-tolerance` | Fractional tolerance for tool calls |
| `max_cost_tokens` | `int` or `null` | `null` | `--max-cost-tokens` | Hard cap on total token count |
| `cost_tolerance` | `float` | `0.5` | `--cost-tolerance` | Fractional tolerance for token cost |
| `max_duration_ms` | `int` or `null` | `null` | `--max-duration-ms` | Hard cap on run duration in milliseconds |
| `duration_tolerance` | `float` | `0.5` | `--duration-tolerance` | Fractional tolerance for duration |

### Boolean checks

| YAML key | Type | Default | CLI flag | Description |
|---|---|---|---|---|
| `no_new_tools` | `bool` | `false` | `--no-new-tools` | Fail if the run uses tools not present in the baseline |
| `no_loops` | `bool` | `false` | `--no-loops` | Fail if any `LOOP_WARNING` event was emitted |
| `no_guardrails` | `bool` | `false` | `--no-guardrails` | Fail if any guardrail event was triggered |

### Status check

| YAML key | Type | Default | CLI flag | Description |
|---|---|---|---|---|
| `expect_status` | `string` or `null` | `null` | `--expect-status` | Expected run status: `"ok"` or `"error"` |

---

## How thresholds work

Tolerances are **fractional**, not percentage: `0.5` means 50%, `0.2` means 20%.

For each numeric metric (steps, tool calls, tokens, duration), the check depends on what is available:

| Baseline provided? | `max_*` set? | Effective limit | Meaning |
|---|---|---|---|
| Yes | Yes | `min(baseline * (1 + tolerance), max_*)` | Baseline-relative with a hard cap |
| Yes | No | `baseline * (1 + tolerance)` | Baseline-relative only |
| No | Yes | `max_*` | Hard cap only |
| No | No | *(check disabled)* | Nothing to compare against |

The check **passes** when `actual <= limit`.

When a baseline value is zero and a matching `max_*` cap is set, Maida uses
the cap as the effective limit. Without a cap, a zero baseline allows no growth
for that metric.

**Example:** A baseline recorded 40 tool calls. With `tool_call_tolerance: 0.25` and `max_tool_calls: 60`:

- Baseline limit: `40 * 1.25 = 50`
- Hard cap: `60`
- Effective limit: `min(50, 60) = 50`
- A run with 48 tool calls passes; a run with 52 fails.

---

## Override rules

When `maida assert` loads a policy file and also receives CLI flags, `merge_policy` applies these rules:

- A CLI value of `None` (flag not provided) keeps the file value.
- A CLI boolean value of `False` (flag not provided) keeps the file value. Only an explicit `--no-loops` (which sends `True`) overrides.
- Any other non-`None` CLI value replaces the file value.

This means you can set baseline thresholds in the committed policy file and tighten or loosen individual checks on a per-invocation basis:

```bash
# File sets no_loops: true, step_tolerance: 0.3
# CLI overrides max_steps for this specific run
maida assert abc123 --max-steps 100
```

---

## Full examples

### Strict CI policy

```yaml
# .maida/policy.yaml — checked into the repo
assert:
  max_steps: 80
  step_tolerance: 0.2
  max_tool_calls: 30
  tool_call_tolerance: 0.2
  max_cost_tokens: 10000
  cost_tolerance: 0.1
  max_duration_ms: 30000
  duration_tolerance: 0.2
  no_new_tools: true
  no_loops: true
  no_guardrails: true
  expect_status: ok
```

### Lenient local policy

```yaml
# .maida/policy.yaml — for local iteration
assert:
  step_tolerance: 0.5
  tool_call_tolerance: 0.5
  no_loops: true
  expect_status: ok
```

---

## Related docs

- [Regression testing](../regression-testing.md) — end-to-end workflow
- [CLI: `maida assert`](../cli.md#maida-assert) — command reference
- [Configuration](config.md) — env vars, YAML config, guardrails
