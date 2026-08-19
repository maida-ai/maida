# `maida init`

Scaffolds Maida configuration in the current directory. Never overwrites existing files unless `--force` is given; safe to re-run.

**Usage:**

```bash
maida init [--github] [--force]
```

**Options:**

| Option | Description |
|--------|-------------|
| `--github` | Also write `.github/workflows/maida.yml` using the current `maida-ai/maida-assert@v5` gate and the `maida-ai/maida-assert/accept-command@v5` handler |
| `--force` | Overwrite existing files |

**Files written:**

- `.maida/policy.yaml` — strict v2 starter with invariant contracts, directional measured tolerances, and a three-trial report-only pass-rate metric
- `.github/workflows/maida.yml` (with `--github`) — PR check running your traced agent and posting the regression report as a sticky comment; also handles authorized `/maida accept [optional reason]` comments and rechecks an accepted PR-head commit; pins `actions/checkout@v7` and currently tracks `maida-ai/maida-assert@v5`, with the command handler at `maida-ai/maida-assert/accept-command@v5`

Edit the generated `MAIDA_AGENT_SCRIPT` value for your entrypoint. After
committing a baseline, set `MAIDA_BASELINE` to its tracked path; leaving it blank
keeps the accept command inactive with a polite configuration response. Baseline
write-back supports same-repository PR branches only and requires the commenter
to have repository write access.

**Exit codes:** `0` success; `10` internal error.
