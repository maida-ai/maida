# Policy v3 two-tier acceptance

> **Status: Non-normative, non-indexed design proposal.** This document does
> not change current Maida behavior. It proposes a future breaking `version: 3`
> policy and acceptance model; no schema, loader, evaluator, refresh job, or
> accept-flow behavior described here is implemented.
>
> **Review gate:** Both founders must review and approve this proposal before
> merge. That review is still outstanding.

## Status and scope

Learning agents are expected to change, while their role boundaries must not
silently change with them. Policy v3 separates those concerns into a
human-owned `envelope` and a plastic `baseline`:

- The envelope states what the agent is permitted and required to do. It owns
  allowed tools, data surfaces, approval requirements, hard ceilings,
  invariants, and cumulative refresh bounds. Every edit is human-accepted.
- The baseline describes observed behavior inside that envelope. It owns
  distributions, order, and phrasing evidence that can adapt after a passing
  evaluation. Baseline auto-refresh is disabled by default.

The goal is to let a general self-improvement workflow propose and promote
in-envelope learning without weakening a human-owned boundary. Authorship and
approval remain separate: an agent can propose either tier, but it cannot grant
itself more authority.

This proposal covers:

- the envelope/baseline policy split and safe defaults;
- acceptance rules for human- and agent-authored changes;
- bounded automatic replacement of baseline samples;
- provenance for manual and automatic acceptance; and
- consistent PASS, FAIL, and INCONCLUSIVE behavior for future pre-merge,
  drift, and canary consumers.

It does not implement policy v3, partner branch automation, merging, deployment,
memory-write interception, or attribution of writes outside Maida artifacts.
Those remain outside this design window. The proposal is deliberately absent
from `docs/index.md` until the breaking format is implemented and released.

## Proposed policy v3 shape

The following YAML is illustrative, not an accepted schema:

```yaml
version: 3

envelope:
  allowed_tools:
    - read_catalog
    - draft_reply
  data_surfaces:
    read:
      - catalog.public
      - ticket.customer_visible
    write:
      - ticket.draft
    forbidden:
      - billing.credentials
  approval_requirements:
    - action: submit_reply
      requires: human
  ceilings:
    step_count: 40
    tool_call_count: 20
    cost_tokens: 10000
    latency_ms: 30000
  invariants:
    no_loops: true
    no_guardrails: true
    terminal_states: [ok]
  baseline_refresh:
    enabled: false
    max_updates: 3
    max_age: P30D
    anchor_tolerances:
      step_count: {absolute: 4}
      tool_order: {edit_distance: 1}
      phrasing: {distance: 0.10}

baseline:
  sample:
    artifact_hash: sha256:current-sample
    trials: 25
  observed:
    step_count: {min: 10, median: 12, max: 14}
    tool_order:
      - [read_catalog, draft_reply]
    phrasing:
      representation: normalized-v1
      distribution_hash: sha256:current-phrasing-distribution
  human_anchor:
    artifact_hash: sha256:last-human-approved-sample
    accepted_at: 2026-08-01T12:00:00Z
```

### Envelope: authority and safety

The envelope is policy-as-code for the agent's role. It is not learned from
traces and automatic refresh can never edit it. Its responsibilities are:

- **Allowed tools:** tool identities or deliberately defined tool classes the
  agent may call. A newly observed tool is an envelope expansion, not baseline
  variance.
- **Data surfaces:** named read, write, and forbidden domains. A new store,
  namespace, or permission is an envelope expansion even if an existing tool
  can technically reach it.
- **Approval requirements:** actions or effects that require a named approval
  class, such as human authorization before an external write.
- **Hard ceilings:** absolute step, tool-call, token, latency, or other resource
  limits. A learned distribution may be narrower, but never wider than these
  ceilings without human acceptance.
- **Invariants:** conditions such as no loops, no guardrail activation,
  required stop states, and role-specific obligations. They remain exact.
- **Cumulative refresh bounds:** the maximum distance, count, and age allowed
  for automatic baseline movement away from the last human-approved anchor.

An omitted permission is not implicitly granted. Policy loading must fail
closed on unknown or malformed envelope fields. Migration from v2 must produce
an explicit v3 proposal for human review; it must not infer a permissive
envelope from observations.

### Baseline: plastic evidence

The baseline is a complete, replaceable sample of behavior already proven to
fit the envelope. It can represent observed numeric distributions, structural
or tool ordering, and a versioned phrasing representation. It does not own
permissions, approval rules, hard ceilings, or invariants.

The baseline is comparative evidence, not an authorization source. A baseline
sample containing a forbidden tool or an envelope violation is invalid even if
its statistical shape looks normal. Report-only improvements or regressions
remain visible, but they cannot override a blocking envelope check.

### Defaults and enablement

The safe default is `envelope.baseline_refresh.enabled: false`. With refresh
disabled, a baseline change waits for human acceptance through the accept flow.
No author identity, including a human author identity, turns refresh on.

If `enabled: true`, `max_updates`, `max_age`, and `anchor_tolerances` are all
required and must be explicit. A loader must reject zero, negative, missing, or
unbounded values rather than manufacture permissive defaults. Each tolerance
names a baseline property and a cumulative distance rule supported by the
corresponding evaluator. The envelope itself remains human-owned after refresh
is enabled.

## Acceptance matrix

Authorship is provenance, not authority. Every envelope change requires human
acceptance, regardless of whether its author is a human or an agent. This
includes restrictions and hardening as well as expansions, so the human-owned
artifact cannot be silently rewritten. In particular, any envelope expansion
always requires a human approver.

Agents may author changes, but can never approve an envelope change. An agent
also cannot impersonate an automatic acceptor or a human approver.

| Proposed change | Author | Evaluation | Required action |
| --- | --- | --- | --- |
| No artifact change | Human or agent | PASS | Promotion may continue; no acceptance record is created. |
| Baseline-only + PASS | Human or agent | Refresh enabled, cadence valid, and all anchor bounds pass | The complete baseline sample may be automatically accepted with automation provenance. |
| Baseline-only + PASS | Human or agent | Refresh disabled or a cadence/anchor bound is exhausted | Hold baseline replacement for human acceptance; do not silently widen a bound. |
| Baseline-only | Human or agent | FAIL | FAIL holds promotion and refresh for review. |
| Baseline-only | Human or agent | INCONCLUSIVE | INCONCLUSIVE defers promotion and refresh while a new complete evidence sample is collected. |
| Any envelope restriction or expansion | Human | Any | A different human approver reviews and accepts the envelope; then evaluation reruns. |
| Any envelope restriction or expansion | Agent | Any | A human approver reviews and accepts the envelope; the agent cannot approve it. |

For an envelope expansion, a baseline PASS is insufficient because PASS only
means the candidate satisfies the currently accepted envelope. Human
acceptance produces a new envelope revision; evaluation must rerun against
that accepted revision before promotion. Requiring a different human approver
from the author is the conservative default for human-authored envelope edits;
the eventual approval-policy design may make that separation configurable but
must never remove human approval.

## Cumulative human-anchor rule

Let `A` be the last human-approved baseline sample, `B0` the current baseline,
and `B1` the candidate replacement. Automatic refresh checks `B1` against `A`,
never merely against the rolling baseline `B0`. After another automatic update,
the next candidate is still compared with the same `A`.

Refresh is eligible only when all of these statements are true:

1. The candidate evaluation is PASS and every observation satisfies the current
   envelope.
2. Refresh is explicitly enabled.
3. The number of automatic updates since `A` is less than `max_updates`.
4. The age of `A` is no greater than `max_age` at decision time.
5. Every cumulative distance from `A` is within its named
   `anchor_tolerances` limit.
6. The candidate is a complete, fixed-size sample produced by the configured
   evidence collection rule.

For example, suppose the anchor median step count is 10 and its absolute
tolerance is 4. Rolling samples with medians 12 and 14 can be eligible, but a
sample at 16 is held: its cumulative distance from 10 is 6 even though its
distance from the rolling value 14 is only 2. Small accepted changes therefore
cannot slow-walk beyond the human-approved tolerance.

Crossing any count, age, or distance bound holds the candidate for human
review; it does not stretch the limit. Human acceptance can establish the
candidate as the next anchor, resetting update count and age only after the
reviewer sees the cumulative diff from the previous human anchor. An envelope
boundary is independently absolute, so no series of baseline refreshes can
grant a new tool, data surface, approval bypass, ceiling, or invariant waiver.

Each promotion replaces the full baseline sample atomically. It does not append
or accumulate traces into the active sample. Evidence collection may rerun to
produce a new complete candidate sample, but old and new trials are not pooled.
This keeps the sample size and evidence rule reviewable and prevents stale
history from dominating current behavior.

## Provenance contract

Every proposed artifact records authorship separately from acceptance. The
future format must retain enough information to reconstruct both the human
anchor chain and automatic replacements:

- **Author identity:** kind (`human`, `agent`, or `automation`), stable identity,
  and, for an agent, its declared agent/revision identity. This says who wrote
  the proposal, not who authorized it.
- **Acceptance mode:** `human` or `automatic`, plus decision timestamp, verdict,
  and reason.
- **Automatic acceptance identity:** a stable Maida automation identity and
  evaluator version. Automation is the acceptor only for an eligible
  baseline-only PASS.
- **Approver identity:** the human reviewer for any envelope change or manual
  baseline acceptance. It must never be copied from the agent author field.
- **Source revision:** repository and immutable revision for the evaluated
  proposal when available.
- **Source report:** report version, report identifier, and report hash for the
  PASS, FAIL, or INCONCLUSIVE evidence used in the decision.
- **Artifact chain:** previous artifact hash and current artifact hash, covering
  the complete canonical baseline or envelope representation.
- **Anchor link:** human-anchor hash, anchor acceptance time, automatic update
  index, and the measured cumulative distances used for each anchor tolerance.

An automatic record could therefore say that agent `support-learner@rev-18`
authored the candidate, `maida:auto-refresh@evaluator-v1` accepted it after a
PASS, and `sha256:A` remained the human anchor. A manual envelope record instead
names the human approver. Recording an agent as author never makes the agent an
approver.

Persistence must be atomic and compare-and-swap against the recorded previous
hash. A stale concurrent decision, persistence failure, missing provenance
field, or hash mismatch leaves the accepted artifacts unchanged.

## Canary and drift verdicts

A future pre-merge, scheduled-drift, or canary integration applies the same
decision ordering:

1. Evaluate the candidate against the human-owned envelope.
2. Evaluate baseline-relative behavior and produce one gate verdict.
3. For a baseline refresh candidate, evaluate cadence and cumulative anchor
   bounds.
4. Only then may an external promotion controller act.

Verdicts have these effects:

- **PASS:** promotion is eligible. A baseline-only proposal may auto-refresh
  only when refresh is enabled and all cadence and anchor rules pass. PASS never
  accepts an envelope change.
- **FAIL:** promotion and every refresh are held for review. A human may accept
  a baseline change only after confirming it remains inside the envelope. An
  envelope violation instead requires an explicit envelope proposal and human
  acceptance before evaluation reruns.
- **INCONCLUSIVE:** promotion and refresh are deferred, and the integration
  collects enough fresh evidence to build a new complete candidate sample. It
  does not partially update the active baseline while waiting.

FAIL dominates mixed checks. A gating INCONCLUSIVE remains neutral but cannot
be treated as PASS for refresh. Report-only metrics remain evidence and never
authorize promotion or refresh. Maida supplies the verdict and provenance; a
partner's branch, merge, deployment, or canary controller remains external.

## Self-improvement flow

The partner-independent model is: self-modification is a PR authored by the
agent, so gate it instead of disabling it.

1. The agent authors a code, prompt, or baseline proposal. Provenance identifies
   the agent and source revision.
2. Maida evaluates the resulting complete sample against the accepted envelope,
   current baseline, and last human anchor.
3. An in-envelope PASS can continue through the external promotion flow. If
   baseline auto-refresh is enabled and bounded, Maida may replace the complete
   baseline sample and record automatic acceptance.
4. A new tool, data surface, approval bypass, higher hard ceiling, or relaxed
   invariant is an envelope expansion. Promotion waits for human acceptance,
   regardless of the agent's PASS against baseline-relative checks.
5. FAIL holds the proposal; INCONCLUSIVE gathers a fresh complete sample. Neither
   changes accepted artifacts.

This expresses learning and self-improvement without partner-specific policy
rules. The same matrix applies to a human-authored refactor, an agent-authored
prompt update, or an externally orchestrated canary result.

## Follow-up implementation issues

These are required implementation seams, not work included in this proposal:

1. **Policy loading and migration:** define and publish policy v3 JSON/YAML
   schemas, fail-closed validation, normalization, v2-to-v3 proposal generation,
   and explicit human review of migrated envelope defaults.
2. **Two-tier evaluation:** separate envelope checks from baseline-relative
   checks, define distance algorithms for distributions/order/phrasing, and add
   deterministic verdict and boundary coverage.
3. **Baseline refresh and provenance:** implement complete-sample replacement,
   cumulative anchor comparisons, count/age enforcement, canonical hashing,
   compare-and-swap persistence, and automatic acceptance records.
4. **Accept-flow UX:** show author versus approver, cumulative anchor diffs,
   envelope expansions, and an explicit human-only envelope acceptance action
   in local and PR-comment surfaces.
5. **Drift and canary integration:** carry v3 envelope, anchor, verdict, and
   provenance semantics through read-only scheduled windows and external canary
   controllers without introducing partner-specific merge automation.

Before any implementation issue begins, both founders must resolve review
questions about envelope defaults, separation of author and approver, and the
initial distance representations. Before this document merges, both founders
must record approval of the proposal itself.
