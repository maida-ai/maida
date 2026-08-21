# `maida import claude-code`

Normalizes one local Claude Code capture into the current Maida trace schema
and atomically installs it in the run store.

```bash
maida import claude-code --session-id SESSION_ID [--segment latest] [--json]
```

`--session-id` is hashed before path lookup. `--segment` selects an immutable
capture segment and defaults to the latest. Identical re-imports are no-ops;
changed source data never overwrites an existing deterministic run. Latest
segment notices use stderr so `--json` keeps stdout machine-readable.

**Exit codes:** `0` imported or already present; `2` missing/invalid capture;
`10` normalization or storage failure.

See [Capture Claude Code telemetry](../claude-code.md) for receiver setup,
normalization rules, and storage layout.
