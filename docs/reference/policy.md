# Policy v2 and gate decisions

Maida policy v2 makes the source of every acceptance criterion explicit. The
gate is a pure function of the candidate code, policy, immutable baseline,
and environment. It never accumulates baseline observations across CI runs.

```yaml
version: 2
trials: 3
fail_fast: true
metrics:
  stop_condition_reached: {kind: invariant, require: true}
  forbidden_tools: {kind: invariant, none_of: [admin_delete]}
  step_count:
    kind: measured
    direction: upper
    tolerance: {relative: 0.5}
  cost_tokens:
    kind: measured
    direction: upper
    tolerance: {relative: 0.25}
  task_pass_rate:
    kind: statistical
    direction: lower
    threshold: 0.90
    confidence: 0.95
    success_predicate: all_invariants_passed
    mode: report_only
```

Policy is hand-authored input and fails closed. Unknown fields are errors.
The version uses `major[.minor]`: `version: 2` normalizes to `(2, 0)`;
`version: 2.1` enables the plan metrics below; `version: 2.0.0` is not valid.

## Metric kinds

| Kind | Acceptance criterion | Sufficiency |
| --- | --- | --- |
| `invariant` | Author-declared semantic contract | No sample requirement |
| `measured` | Author-declared numeric tolerance or limit | Valid at N=1 |
| `distributional` | One-sided prediction bound inferred from a baseline sample | Checked when the baseline is bound |
| `statistical` | Policy threshold θ and candidate Bernoulli outcomes | Checked when policy loads |

The band is either declared or purchased. A measured tolerance is cheap and
works with one candidate observation. A distributional bound requires enough
baseline trials to earn its requested coverage.

### Invariant

An invariant is exact. Any observed violation fails, including at N=1. Across
multiple trials, trials are a counterexample search rather than a rate sample:
one violation is enough. Reports show `violated in 1/25 trials` so intermittent
contracts are visible. Reclassify an intermittent property instead of weakening
the invariant rule.

Supported invariants are `stop_condition_reached`, `forbidden_tools`,
`required_tools`, `no_loops`, `no_guardrails`, `plan_effectful_modules`, and
`plan_grants`.

### Measured

A measured metric compares the candidate aggregate with a user tolerance or
hard limit. The default aggregate is `median`. Every report includes
min/median/max. `aggregate: max` and `aggregate: p90` are explicit upper-tail
opt-ins.

A one-in-25 tail event is not a measured question. Express “the rate of
`step_count > X` increased” as a Bernoulli statistical metric.

Relative and absolute tolerances are additive around the baseline aggregate.
When a hard limit is also present, the stricter boundary applies.

### Pre-execution plan rules

Policy 2.1 adds the deliberately small vocabulary used to judge a generated
plan before it executes:

```yaml
version: 2.1
metrics:
  plan_depth: {kind: measured, direction: upper, limit: 4}
  plan_fanout: {kind: measured, direction: upper, limit: 2}
  plan_budget_cost_usd: {kind: measured, direction: upper, limit: 1.5}
  plan_budget_model_tokens: {kind: measured, direction: upper, limit: 2000}
  plan_budget_tool_calls: {kind: measured, direction: upper, limit: 10}
  plan_budget_wall_time_ms: {kind: measured, direction: upper, limit: 10000}
  plan_effectful_modules:
    kind: invariant
    allowed: [demo.deliver]
    none_of: [untrusted.shell]
  plan_grants:
    kind: invariant
    allowed: [records.context.read, messages.deliver]
    approval_required_for: [messages.deliver]
```

`allowed` requires the observed set to be a subset of the declared values, so
an explicit `allowed: []` permits no values. `none_of` forbids any overlap;
`all_of` requires named values to be present. An empty `none_of`, `all_of`, or
`approval_required_for` clause is rejected when it would make the whole rule a
no-op. For `plan_grants`, `approval_required_for` requires the resolved artifact
to carry an approval requirement for each named effect that the plan actually
requests; unrelated policy-listed effects do not reject a harmless plan. Plan
metrics require complete pre-execution evidence from a plan backend for every
trial; a trace-only or partially evidenced gate fails closed rather than
pretending to evaluate them.

### Distributional

A distributional metric infers a one-sided order-statistic prediction bound
from the immutable baseline trial vector. For exchangeable observations, a new
draw exceeds the maximum of `n` baseline observations with probability
`1/(n+1)`. Therefore:

```text
n_min = ceil(1 / (1 - coverage)) - 1
```

This is 19 baseline trials for 95% coverage and 9 for 90%. Ties are
conservative. `direction: both` is rejected; declare a measured two-sided
tolerance if both sides are harmful.

At candidate N=1 the gate applies the prediction bound directly. At N>1 it
counts harmful exceedances and applies a one-sided Wilson bound to the
exceedance rate. Applying any-trial failure here would produce
`1 - coverage^N` false failures (72% at 95% coverage and N=25), so it is
intentionally not used.

If the baseline sample is too small, omitted `mode` resolves to
`report_only`. Explicit `mode: gating` is rejected when the baseline is bound,
with the required recapture size.

### Statistical

A statistical metric evaluates candidate Bernoulli outcomes against policy θ.
The baseline does not contribute. `task_pass_rate` defaults to the explicit
named predicate `all_invariants_passed`: one trial succeeds when every
invariant-tier metric and the agent process succeeded on that trial.

`confidence` is the **one-sided coverage** of the Wilson bound. At 0.95,
`z = 1.645`; it is not the coverage of a two-sided interval. Direction chooses
the harmful side:

- `lower`: pass rate and stop-condition-reached rate
- `upper`: error, retry, or harmful-event rates
- `both`: only when explicitly declared

PASS and FAIL are both earned from the relevant one-sided bound. Otherwise the
metric is INCONCLUSIVE. For unanimous lower-direction outcomes:

```text
Wilson lower = n / (n + z²)
n_min = ceil(θ z² / (1 - θ))
```

At 95% confidence:

| θ | Minimum candidate trials |
| ---: | ---: |
| 0.70 | 7 |
| 0.80 | 11 |
| 0.90 | 25 |

An explicit gating metric below `n_min` is rejected while loading policy. The
error includes θ, confidence, computed `n_min`, configured trials, and both
remedies: raise trials or set `mode: report_only`. If `mode` is omitted, the
metric is report-only below the boundary and promotes to gating at the
boundary.

## Direction and reporting

`direction` is required on measured, distributional, and statistical metrics.
`upper` means only an increase can block; `lower` means only a decrease can
block; `both` must be explicit and is never inferred by v2.

Direction controls blocking, not reporting. A step count falling from 12 to 5
passes an upper-direction gate but the `-7` delta remains in the PR comment.
Large improvements can reveal skipped work and deserve review without a red
check.

## Composition and stopping

FAIL dominates. PASS requires every blocking tier to pass and no gating metric
to be inconclusive. A gating INCONCLUSIVE produces the overall neutral
INCONCLUSIVE result. `report_only` metrics never block and have no verdict.

`fail_fast: true` is the default. The fixed budget stops when a blocking
failure is irreversible, especially an invariant violation, and reports
`trials_used`, `trials_budgeted`, `stopping_rule`, and `abort_reason`.
Use `--no-fail-fast` for baseline capture and calibration.

Exit codes are stable across report versions:

| Exit | Meaning |
| ---: | --- |
| 0 | PASS or INCONCLUSIVE |
| 1 | Gate FAIL |
| 2 | Input, policy, baseline, or run not found/invalid |
| 10 | Internal execution error |

## Immutable baseline capture

Run the full candidate budget and bind it explicitly:

```bash
maida run agent.py --trials 25 --no-fail-fast --json-out report.json
maida baseline --from-report report.json --out .maida/baselines/agent.json
```

The checked-in baseline stores each per-trial numeric and invariant outcome
vector, trace structural signatures deduplicated by hash with counts, and an
environment fingerprint. When the report includes plan evidence, the additive
`plan_sample` stores its numeric vectors, set values, and canonical plan
artifacts deduplicated by artifact ID. It is never mutated or accumulated by
gate runs. Recapture explicitly when more evidence is desired.

## Version streams

The five formats are intentionally not harmonized:

| Stream | Current | Form | Compatibility |
| --- | --- | --- | --- |
| Policy | `2.1` | `major[.minor]` | Hand-authored; unknown keys and unsupported minor versions reject; 2.0 remains valid for trace-only rules |
| Trace | `0.2.0` | full semver | Generated; legacy `0.2` loads and patch drift within 0.2 is accepted |
| Baseline | `0.3.1` | full semver | Generated; 0.3 patch drift and legacy `0.2` load |
| Report | `2.0.1` | full semver | Generated external contract; consumers must ignore unknown fields within a major |
| Plan | `0.1.0` | full semver | Generated canonical artifact; exact version and content digest are validated |

Report fields are never removed or repurposed within a major. Report consumers
must ignore unknown fields. Policy loaders do the opposite because silently
ignoring a requested policy key would make the gate claim enforcement it did
not perform.

## v1 migration

An unversioned or `version: 1` policy loads through a visible deprecation
warning. Known legacy metric names infer their established directions:
step/tool/cost/latency are upper; pass rate is lower. Unknown v1 keys are named
in the warning.

N=1 users retain binary pass/fail through invariant and measured tiers. Default
N=3 users retain their verdict unless the old gate passed solely because a
statistical metric used the removed 3/3 unanimous compatibility rule. That is
the only branch-protection-visible migration change.

The former `single_trial_binary` and `small_n_unanimous` decision rules no
longer exist.
