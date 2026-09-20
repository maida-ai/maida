# `maida init`

Scaffolds Maida configuration in the current directory. Never overwrites existing files unless `--force` is given; safe to re-run.

**Usage:**

```bash
maida init [--github] [--force]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--github` | Also write `.github/workflows/maida.yml` using the coordinated `maida-ai/maida-assert@main` gate and `maida-ai/maida-assert/accept-command@main` handler |
| `--force` | Overwrite existing files |

**Files written:**

- `.maida/policy.yaml` -- strict v2 starter with invariant contracts, directional measured tolerances, and a three-trial report-only pass-rate metric
- `.github/workflows/maida.yml` (with `--github`) -- verifies the PR head before checkout, runs the gate and publishes its required status; separate authorization, read-only capture and data-only write jobs handle `/maida accept [optional reason]`; uses a commit-pinned checkout action and tracks the coordinated `maida-ai/maida-assert@main` actions until you pin a reviewed Action commit

Edit the generated `MAIDA_AGENT_SCRIPT` value for your entrypoint. After
committing a baseline, set `MAIDA_BASELINE` to its tracked path; leaving it blank
keeps the accept command inactive with a polite configuration response. Baseline
write-back supports same-repository PR branches only and requires the commenter
to have repository write access.

**Exit codes:** `0` success; `10` internal error.
