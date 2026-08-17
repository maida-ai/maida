# Regression testing

Maida runs a traced agent in isolated workspace copies, evaluates an explicit
policy taxonomy, and emits PASS, FAIL, or provider-neutral INCONCLUSIVE.

## Recommended workflow

```bash
# 1. Scaffold and edit the v2 policy.
maida init --github

# 2. Capture a complete reviewed trial sample.
maida run my_agent.py \
  --policy .maida/policy.yaml \
  --trials 25 \
  --no-fail-fast \
  --json-out baseline-report.json
maida baseline \
  --from-report baseline-report.json \
  --out .maida/baselines/my_agent.json

# 3. Commit the policy and immutable baseline.
git add .maida/policy.yaml .maida/baselines/my_agent.json

# 4. Run the candidate gate.
maida run my_agent.py \
  --policy .maida/policy.yaml \
  --baseline .maida/baselines/my_agent.json \
  --format markdown \
  --json-out maida-report.json
```

The gate never accumulates baseline observations across CI runs. Recapture
explicitly when you want to buy more baseline evidence.

## Policy

Every metric says where its acceptance criterion comes from:

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
  task_pass_rate:
    kind: statistical
    direction: lower
    threshold: 0.90
    confidence: 0.95
    success_predicate: all_invariants_passed
    mode: report_only
```

- `invariant` is an exact semantic contract. One violation fails.
- `measured` uses a declared tolerance or hard limit and aggregates with the
  median by default.
- `distributional` infers a one-sided prediction bound from the immutable
  baseline trial vector.
- `statistical` applies a one-sided Wilson bound to candidate Bernoulli
  outcomes.

Direction controls blocking, not reporting. An upper-direction step count
falling from 12 to 5 passes, while the `-7` delta remains visible for review.

See the [policy reference](reference/policy.md) for sufficiency formulas,
aggregation, migration, and schema compatibility.

## Baseline contents

Baseline schema `0.3.1` stores:

- raw per-trial numeric and invariant outcome vectors;
- an environment fingerprint;
- structural signatures deduplicated by SHA-256 with counts;
- source trace IDs and compatibility summary fields.

`19` baseline trials earn a 95% one-sided distributional prediction bound;
`9` earn 90%. A too-small sample makes omitted mode report-only. Explicit
gating rejects when the policy is bound to that baseline.

The single-run `maida baseline [TRACE_ID]` form remains for compatibility and
emits a one-trial sample. Prefer `--from-report` for new gates.

## Reports and exit codes

Report schema `2.0.1` includes the metric kind, direction, mode, named decision
rule, stopping rule, trials used/budgeted, raw outcomes, and tier evidence.
Report consumers must ignore unknown fields within a major.

Markdown is verdict-first and always reports large improvements. Report-only
metrics show observed values without a confidence verdict. INCONCLUSIVE is
neutral and never a red check.

| Exit | Meaning |
| ---: | --- |
| 0 | PASS or INCONCLUSIVE |
| 1 | Gate FAIL |
| 2 | Missing or invalid input, policy, baseline, or run |
| 10 | Internal execution error |

## Fail-fast and calibration

`fail_fast: true` stops a fixed budget when a blocking result cannot recover,
such as an invariant violation. Reports record the shortened sample and abort
reason. Use `--no-fail-fast` for baseline capture and calibration so the full
outcome vector is available.

The issue #187 [calibration table](calibration-187.md) is a seeded offline
measurement, not an acceptance guarantee. Unreachable policy cells are shown
as load rejected.

## Reviewing changes

Use the preserved trace IDs and structural report evidence to inspect a
failure:

```bash
maida view <TRACE_ID>
maida diff <TRACE_ID> --baseline .maida/baselines/my_agent.json
```

If behavior intentionally changed, recapture a complete baseline report and
review the resulting baseline JSON diff. `maida accept` remains available for
legacy single-run baseline workflows, but it does not accumulate statistical
history.

## GitHub Actions

`maida init --github` pins `maida-ai/maida-assert@v5`. The Action
consumes report schema 2, keeps INCONCLUSIVE neutral, and posts the tier-aware
Markdown as a sticky PR comment and check summary. The action contract is
maintained in the separate `maida-assert` repository.
