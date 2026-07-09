# missing-terminal-state

Deterministic active-run fixture. `meta.json` keeps `status: "running"` with
no terminal timestamp, and `spans.jsonl` contains a child span but no completed
root span or synthetic terminal state.
