# Measure the local onboarding funnel

**Product activation is the first valid `maida check` report on the user's own repository task.** PASS and FAIL both count: each provides meaningful evidence about the observed task. Setup errors, unreadable evidence and incomplete capture do not count. Producing a report does not establish that the user understood it.

`maida onboarding` is an explicit local journal. Start measurement yourself; normal product commands never create an attempt. The journal never uploads anything, makes no network calls, and stores no task text, prompt, tool payload, repository path or person name. It is separate from optional usage counts.

## Start before setup

```bash
maida onboarding start --assistance none --task-kind coding-agent
maida init
# Complete one real coding-agent task in this repository and exit the session.
maida check
maida onboarding report
```

The primary elapsed-time metric is `time_to_first_report_seconds`: time from attempt start to the first valid report. For a usability study measuring the full **GitHub homepage → report** journey, start an external stopwatch at the homepage. The CLI cannot start its journal before Maida is installed. Record that external interval separately; do not present the CLI interval as the full journey.

Use `--assistance founder` or `other` if help is already being provided. Omitting it records `unknown`, which never counts as unassisted. Task kinds are the bounded categories `coding-agent`, `python-agent` and `other`; omitting the option records an unknown category. Each attempt records the installed engine version.

## Follow the ordered funnel

| Stage | Evidence | Recording |
|---|---|---|
| `setup-ready` | First-run `maida init` successfully completed capture setup for this repository. Declining the preview does not count. | Automatic during an active attempt. |
| `own-task-captured` | A real coding-agent task completed with a complete captured lifecycle and tool activity, and was successfully imported for this repository. | Automatic for complete repository-scoped captures/imports. |
| `first-report` | `maida check` rendered a valid report on that own task, whether PASS or FAIL. **Product activation / first value.** | Automatic after rendering. |
| `gate-configured` | The user reviewed and activated a baseline/policy with `maida init --reviewed`. Drafting candidates alone does not count. | Automatic after successful reviewed activation. |
| `local-gate-verified` | A reviewed local gate first passed, caught a safe intentional regression, and passed again after repair. | Explicit regression/repair evidence, described below. |
| `pr-gate-verified` | The actual protected PR merge boundary was tested successfully. | Explicit only; generating a workflow never counts. |

Automatic recording is quiet and applies only after `maida onboarding start`, to the latest attempt that has not been blocked or abandoned. Repeating commands keeps the first milestone timestamp and does not duplicate it. Successful commands without an explicitly started attempt create no measurement journal. If measurement storage is invalid or unwritable, product commands continue quietly; use the explicit onboarding report to inspect the journal error. Start before the task to measure capture timing accurately. If capture predates the attempt, a successful check records the first observation of its complete evidence at check time, without backdating it.

`demo-seen` is an optional independent milestone:

```bash
maida onboarding record --milestone demo-seen
```

A demo is never required for setup, capture, activation or gate verification. The journal also accepts explicit funnel milestone records for facilitated studies; record only events actually observed. A first report requires own-task capture evidence. Later stages do not manufacture timestamps for earlier stages that were not recorded.

## Verify the local gate, then the PR boundary

After reviewed init activates the contract, repeat the task and confirm its comparative PASS. Deliberately introduce a safe regression, confirm that the same gate catches it, then repair it back to PASS without replacing the baseline:

```bash
maida onboarding record --milestone gate-pass
maida onboarding record --milestone regression-caught
maida onboarding record --milestone repair-pass
```

These ordered, explicit records establish `local-gate-verified` at the first repair PASS. Recording `local-gate-verified` directly still requires the regression/repair sequence. An arbitrary FAIL followed by PASS from `maida check` does not establish it. Local regression verification is a stronger downstream milestone than first-report activation.

Only after testing the actual protected PR boundary, including its required checks and acceptance behavior, record:

```bash
maida onboarding record --milestone pr-gate-verified
```

PR verification is stronger again. This self-reported record is not independent proof of branch protection, and workflow generation does not imply enforcement. See the [Action's repository protection requirements](https://github.com/maida-ai/maida-assert#add-the-merge-boundary).

## Keep assistance and human effort separate from elapsed time

```bash
maida onboarding record --phase setup --minutes 6 --actor user
maida onboarding record --phase investigation --minutes 4 --actor founder
maida onboarding record --phase maintenance --minutes 12 --actor founder
```

Phases are `setup`, `trial`, `investigation` and `maintenance`; actors are `user`, `founder` and `other`. Record fresh baseline trial effort as well as candidate trials, recurring rebaselining and upgrades. Human effort is separate from elapsed attempt time.

Assistance at activation is frozen at the first `first-report`. Founder or other help recorded before that report marks activation assisted. Help received only afterward remains in effort totals and later-stage assistance, and does not retroactively change an unassisted activation. Record help when it occurs: the journal cannot reconstruct earlier help from a maintenance entry added later. Unknown assistance remains unknown unless help has been recorded.

## Include drop-off and unsuccessful attempts

```bash
maida onboarding record --outcome blocked --phase setup --minutes 15 --actor user
maida onboarding report
maida onboarding report --json
```

Use `--outcome abandoned` for a stopped attempt. Ended attempts require a new `start`; starting over preserves an unfinished previous attempt as abandoned. All attempts, including abandoned and blocked users, remain in the denominator.

Text and JSON reports show all six stages in order: attempts reaching each stage, percentage of all attempts, conversion from the previous stage, elapsed seconds from attempt start to its first occurrence, and unassisted/assisted/unknown counts at that stage. Stage counts reflect recorded evidence. Conversion is the fraction of attempts with the previous stage recorded that subsequently reached this stage; it never assumes unrecorded stages. The first stage uses all attempts as its denominator. A zero denominator produces null in JSON and `n/a` in text.

`time_to_first_report_seconds` is prominent in both formats. Reports also retain total attempts, activation count/rate, blocked/abandoned/in-progress counts, effort by actor and phase, engine versions, task kinds, local and PR verification counts, and optional demo count. Text reports explicitly show attempts that completed setup but never produced a report. JSON retains `time_to_activation_seconds` as an alias for first-report times and `ci_verified` as an alias for explicit PR verification.

## Storage and older journal formats

Measurement lives under `<data_dir>/onboarding/<checkout-hash>.json`, normally in `~/.maida/onboarding/`, outside the repository, with private file permissions where supported by the platform. It uses the storage parent rather than the per-project capture-receipt directory. Attempts are scoped to the repository root so subdirectory commands share measurement. Changing checkout location starts a separate series.

The current funnel uses `journal_version: 2` only. A missing journal is created as v2 by an explicit `maida onboarding start`. Existing v1 journals and other unsupported formats are never migrated, overwritten, deleted or reinterpreted. Explicit onboarding commands fail with the journal path and recovery instructions; normal product commands quietly skip measurement and leave the older file unchanged. This applies to both completed and unfinished v1 attempts. Invalid v2 journals are also preserved and rejected.

For an older journal, the error explains:

```text
This onboarding journal was created by an older or unsupported measurement format
and cannot be used with the current funnel.

Existing data was left unchanged:
<local journal path>

Move or archive that file, then run:
  maida onboarding start
```

Move or archive the named file yourself if you want to retain its evidence, then explicitly start a new v2 attempt before measuring setup, capture and first value. Historical attempts never contribute invented first-report timestamps. Old subdirectory journals are left untouched; the current journal location is scoped to the repository root.

To compare walkthroughs, retain unsuccessful attempts and document which study conditions apply. Share only aggregate output you choose to share. Nothing is uploaded automatically. Automated tests validate measurement mechanics; claims about unassisted activation or comprehension require real participant observations.
