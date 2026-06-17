# missing-terminal-state

Proves the active-run/provisional trace case. `meta.json` has
`status: "running"` and the span log contains a child span but no completed
root span, so no synthetic `RUN_END` is projected.
