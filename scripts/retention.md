# Repository retention measurement

A repository is **retained** when a Maida check ran on at least one pull request
in **each** of weeks 3 and 4 after installation. This measures repeated use,
not successful verdicts, branch protection, or behavioral correctness.

The active workflow is a **private sheet populated by voluntary check-ins**.
Automated usage collection remains disabled. Public PyPI and npm download
snapshots are separate context: they cannot establish repository setup or
retention. See [weekly public downloads](weekly_downloads.md).

## Time and counting rules

- `installed_at_utc` is the first installation of the Maida PR check in the
  repository, confirmed by its operator. Installing the Python package alone
  does not establish this date. Record an ISO 8601 UTC timestamp.
- Weeks are seven-day intervals relative to installation, not calendar weeks.
  Week 3 is `[installed_at + 14 days, installed_at + 21 days)`; week 4 is
  `[installed_at + 21 days, installed_at + 28 days)`. The opening instant counts;
  the closing instant belongs to the next week.
- A check counts when Maida executed against a PR candidate. PASS, FAIL, and
  INCONCLUSIVE all count. A skipped job, setup failure before Maida ran, package
  download, local demo, push-only run, or scheduled run does not count.
- Each repository contributes at most one retained count. Reruns and multiple
  PRs do not increase it. Reinstallation does not reset its original clock;
  repository renames keep the same private reference. Do not count the same
  repository separately for multiple reporters or evidence sources.
- A repository becomes eligible only after the end of week 4. Incomplete
  observation windows remain pending even if qualifying runs already occurred.

## Tracking sheet

Copy [retention-template.csv](retention-template.csv) into a private spreadsheet.
The committed template intentionally contains only headers. Keep populated
copies, repository mappings, check-in records, and evidence outside this public
repository. Use an opaque `repository_ref`, not an organization or customer name.

Record one row per repository:

| Column | Meaning |
| --- | --- |
| `repository_ref` | Stable, private deduplication key |
| `installed_at_utc` | Confirmed initial check installation timestamp |
| `week3_status`, `week4_status` | `yes`, `no`, or `unknown` |
| `week3_run_at_utc`, `week4_run_at_utc` | Timestamp of one qualifying execution, when known |
| `week3_evidence_ref`, `week4_evidence_ref` | Reference to private evidence supporting that week's status |
| `source` | `check_in` for the active voluntary workflow |
| `reviewed_at_utc` | Timestamp of the review that validated this row |
| `reviewer_ref` | Private reference to the reviewer |
| `notes` | Minimal explanation of missing or conflicting evidence |

Set `yes` only with evidence of a PR execution within the relevant interval.
A check-in may explicitly confirm execution in that interval when an exact
timestamp is unavailable; leave the timestamp blank and preserve the dated
confirmation in the private evidence record. Set `no` only when a complete week
is explicitly confirmed to have had no qualifying executions. Silence, absent
pings, withdrawn consent, and conflicting evidence mean `unknown`, not `no`.

Ask permission to record the confirmation privately, explain its purpose, and
allow the operator to decline. Request no traces, prompts, tool payloads, access
tokens, or private repository access. Do not automatically send reminders or
collect additional identifying data to fill missing answers.

A suggested check-in, sent manually by the review owner:

> May we record your optional confirmation privately to understand continued use?
> When was the Maida PR check first installed? During each of these two intervals
> [insert install-relative week 3 and week 4 dates], did it run on a PR? Yes, no,
> or unsure is enough. No traces or payloads needed, and you can decline.

Aggregate command pings and package download totals cannot establish retention.
Any future automated source requires a separate explicit opt-in and adequate
evidence of identity, PR execution, and timing. No collection is enabled by this
document or template.

## Weekly calculation

At a fixed UTC review cutoff, freeze a private snapshot and use these rules:

1. Deduplicate repositories and validate timestamps and evidence. Rows with
   unknown install dates or identity conflicts are unresolved; disclose their
   count separately and resolve them before making a threshold decision.
2. Let `E` be repositories with known installation dates whose week 4 has ended.
   Pending repositories do not enter `E`.
3. Let `R` be eligible repositories with `yes` in both weeks. Let `N` be eligible
   repositories with `no` in either week. Let `U = E - R - N` be unresolved
   eligible repositories.
4. The headline number is **`R` confirmed retained repositories** as of the
   cutoff. Always accompany it with `E`, `N`, `U`, the pending count, and any
   excluded unresolved records. Never present this opt-in sample as all installs.
5. If a decision uses a retention rate, its observed lower bound is `R / E`
   and its possible upper bound is `(R + U) / E`. With `E = 0`, the rate is
   unavailable, not zero. These are missing-evidence bounds, not statistical
   confidence intervals.

For a privately configured minimum rate `t`, the condition “retention is below
`t`” is established only when `(R + U) / E < t`. If `R / E >= t`, the observed
sample meets it. Otherwise evidence is insufficient. Record the intended cohort,
minimum eligible sample, cutoff, threshold, comparison operator, and decision
owner before applying a criterion. Do not invent a threshold or treat a small,
self-selected sample as population evidence. This template does not define
business targets or authorize a launch decision.

## Weekly review procedure

The review owner should add this item to the existing private weekly agenda:

> Refresh the retention sheet at the agreed UTC cutoff. Review confirmed retained
> repositories (`R`) with eligibility and unknown counts; reconcile duplicate or
> missing evidence; record the applicable threshold result and next action.

The private sheet itself may serve as the agenda. Keep separate tabs for the
review procedure, voluntary check-ins, public downloads, and dated review history.
Record the owner privately; the public template contains no partner identities or
private sheet URL. Import registry snapshots as dated values, keeping each
registry's count, completeness status, and source. Never add PyPI and npm together
as unique users or convert missing observations into zeros.

Record the owner, recurring day/time, private sheet location, agenda location,
and next review date in that agenda. At each review, save the snapshot and record
the cutoff, `R/E/N/U`, pending and excluded counts, evidence corrections, decision,
and next action. Append a review record rather than overwriting historical totals.
Late evidence can correct a prior measurement but must preserve its original
value and reason for correction.

This is a procedure ready for adoption, not an installed calendar event or an
assertion that reviews already happen. Operational setup is complete only when
the owner has linked the sheet in the recurring agenda and recorded the next
review. Actual review records remain private.
