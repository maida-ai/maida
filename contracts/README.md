# Cross-repository contracts

This directory is the authoritative, implementation-checked contract source
for Maida sibling repositories. `current-main.json` records the current engine,
trace, baseline, policy, report, plan, CLI, installation, and Action channel.
`conformance/` contains Python-owned behavioral vectors for mirrors such as
`maida-ts`.

`engine_ref` is the exact released Maida git tag consumers should use when
exercising this contract. Update it when the contract's required engine release
changes; do not derive it from a development build's package version.

Consumer repositories -- `maida-assert`, `maida-ts`, `maida-ai.github.io`,
`maida-tutorials`, and `maida-workflows` -- vendor exact snapshots under
`tests/contracts/` because those copies are test inputs, not independently
owned public contracts. Update the Python source first, copy the affected
snapshot, and run:

```bash
uv run python scripts/check_cross_repo_sync.py --workspace ..
```

The scheduled/manual cross-repository workflow performs the same byte-level
comparison against every sibling `main` branch.
