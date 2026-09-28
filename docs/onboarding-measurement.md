# Measure setup and maintenance effort locally

**Next release:** `maida onboarding` is available in the development checkout. It is an explicit local journal, separate from the optional usage-count receiver. It sends nothing over the network and stores no task text, prompt, tool payload, repository path or person name.

Start before trying the walkthrough:

```bash
maida onboarding start --assistance none --task-kind coding-agent
```

Use `founder` or `other` if someone is helping. Use `--task-kind coding-agent`, `python-agent` or `other` to record a bounded task category; omitting it records an unknown category. Every attempt records the installed engine version. Omitting the assistance option records assistance as `unknown`; an unknown attempt never counts as unassisted. Start a fresh attempt when retrying after a blocker, using the same checkout directory to retain the denominator. Starting over keeps an unfinished previous attempt as abandoned.

Record actual human effort separately from elapsed time:

```bash
maida onboarding record --phase setup --minutes 6 --actor user
maida onboarding record --phase investigation --minutes 4 --actor founder
maida onboarding record --phase maintenance --minutes 12 --actor founder
```

Phases are `setup`, `trial`, `investigation` and `maintenance`. Actors are `user`, `founder` and `other`. Record fresh baseline trial effort as well as candidate trials; do not hide recurring rebaseline or upgrade work in the original installation time. Founder help before first activation changes its classification to assisted; later maintenance remains visible without rewriting the original activation classification.

Record only milestones you actually completed with your own repository task:

```bash
maida onboarding record --milestone captured
maida onboarding record --milestone baseline-reviewed
maida onboarding record --milestone gate-pass
maida onboarding record --milestone regression-caught
maida onboarding record --milestone repair-pass
```

Activation requires all five stages in order: capture your own task, review its baseline/policy, pass the gate, deliberately introduce and catch a regression, and repair it back to PASS. The command rejects missing prerequisite stages; a caught regression alone is incomplete activation. Demo completion, a green shell exit code and an INCONCLUSIVE report do not meet that definition. This journal is self-reported evidence; it cannot verify someone understood the report or independently prove a CI boundary. `installed` is an optional progress milestone. Record `ci-verified` separately only after testing the actual protected pull request boundary; local activation does not count as CI verification.

Keep failed attempts in the denominator:

```bash
maida onboarding record --outcome blocked --phase setup --minutes 15 --actor user
maida onboarding report
maida onboarding report --json
```

`--outcome abandoned` records a stopped attempt. An ended attempt needs a new `start` before further work. Reports include engine versions and bounded task-category counts, activation count/rate over all attempts, unassisted/assisted/unknown counts, blocked/abandoned/in-progress counts, elapsed seconds to first activation, explicit CI-verification counts, and effort totals by actor and phase. Zero attempts produce a null activation rate, not a success.

The journal lives under `<data_dir>/onboarding/<checkout-hash>.json`, normally in local `~/.maida` storage, with private file permissions. It is outside the repository by default and is never auto-uploaded. Changing the checkout location creates a separate local series. To compare walkthrough versions, run separate documented participant attempts, retain unsuccessful attempts, and share only the aggregate output you choose to share. Automated workflow tests validate the mechanics; claims about unassisted user activation require real participant attempts.
