# Regression testing

Maida runs a traced agent in isolated workspace copies, evaluates an explicit policy taxonomy, and emits PASS, FAIL, or provider-neutral INCONCLUSIVE. Start with the [released coding-agent walkthrough](getting-started.md). This reference explains the Python trial runner after you have protected one task.

## Recommended workflow

For the installed release, follow the [Python agent walkthrough](python-agent.md). Start with a reviewed invariant contract and one task; add sampling only when the metric requires it.

The **next-release** [init workflow](cli/init.md) derives a small inactive starter from your own observations:

```bash
uv run python my_agent.py
maida init --from-run latest
```

Confirm the displayed run belongs to the task you are protecting. Read `.maida/starter/policy.yaml`, remove rules outside your contract, then activate the reviewed draft:

```bash
maida init --reviewed --reason "This task must finish without loops"
maida run my_agent.py --baseline .maida/baselines/agent.json --policy .maida/policy.yaml
```

Introduce a small intentional regression, confirm FAIL, repair it, and confirm PASS before adding CI. A reviewed invariant evaluates observed executions; it does not establish an underlying population pass rate.

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

Calibration must use deterministic, full-budget runs with `--no-fail-fast`.
Treat the resulting sample as evidence for selecting policy thresholds, not as
an acceptance guarantee; unreachable policy configurations fail while loading.

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

After reviewing the development starter, `maida init --github --agent-script my_agent.py` generates a workflow pinned to the reviewed Action commit, using the real entrypoint and baseline paths. See [init](cli/init.md) for supported dependency setup and the Python 3.12 acceptance-runner boundary. The Action consumes report schema 2, keeps INCONCLUSIVE blocking, and posts the Markdown report as a sticky PR comment and check summary. The action contract is maintained in the separate `maida-assert` repository. The [released Python walkthrough](python-agent.md) remains the route for installed releases; remote branch protection must be verified in the consumer repository before claiming merge enforcement.
