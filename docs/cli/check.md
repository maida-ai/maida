# `maida check`

**Unreleased extension:** `--agent claude-code|codex|chatgpt-work` selects an attached provider. Plain `check` selects the most recently started task across attached providers. Codex and local-only Work tasks are runtime turns that can be checked while the session remains open; Claude retains session-based completion. A newer unfinished, interrupted, corrupt, or unimportable task produces recovery guidance without stale fallback. See [Codex and local-only Work](../codex-work.md) for activation, coverage, immutable snapshots, and the unverified desktop acceptance journey. The released v0.6.1 behavior described below is Claude-specific.

Check the latest Claude Code task captured in this initialized repository, then open the same task using the printed viewer command. This command is available from v0.6.1.

```bash
maida check
```

When Maida is installed in your project environment, use `uv run maida check`. The report prints the trace ID and `uv run maida view TRACE_ID` in that case; otherwise it prints `maida view TRACE_ID`.

The built-in checks require successful completion, no observed loop warnings and no recorded guardrail events. No policy or baseline is needed, and existing policy files do not affect these first-run checks. The report covers observed tool activity and session lifecycle, not answer correctness or complete model-call, token or latency coverage.

The newest session must have finished and contain useful evidence. Missing, incomplete or broken capture produces a concrete recovery action; the command never substitutes SDK runs or an older completed task for unfinished new work. Run `maida init` if capture is absent or detached. Saved Claude tasks remain readable by explicit trace ID after detach.

`--format json` and `--format markdown` produce machine-readable or shareable reports; the trace ID and viewer command go to stderr so redirected report output stays clean. Human-readable text is the default and includes both on stdout.

This command does not change `maida assert`, bare `maida view` or other SDK/Python read defaults. Use `maida assert TRACE_ID --baseline PATH` for an explicit captured task's baseline gate.

Exit codes: `0` the three checks passed; `1` an observed check failed; `2` setup, capture or format needs repair; `10` an unexpected internal failure. A failing report still prints the selected task and its viewer command.
