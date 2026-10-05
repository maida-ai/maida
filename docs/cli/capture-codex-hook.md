# `maida capture codex-hook`

**Unreleased.** A passive stdin receiver for native Codex and local-only ChatGPT Work command hooks. Use [automatic setup](../codex-work.md) rather than managing receiver processes or importing sessions manually.

```bash
maida capture codex-hook
```

The receiver reads one JSON object and persists sanitized repository/session/turn evidence synchronously. Setup binds this command to the installed Maida interpreter. It observes lifecycle, user prompt, tool, subagent, compaction, stop, and interrupt signals exposed by native hooks, keeping source identity and topology where available. Private transcript files are never parsed. No allow, deny, retry, context, or approval decision is returned to the agent.

Success and disabled capture produce no output. Identical direct and plugin deliveries are deduplicated; conflicting deliveries require repair. Root termination and complete observed tool pairs are required for completed turn selection. Child termination, interruption, and session closure never manufacture successful completion. `maida check` validates and materializes immutable snapshots later, keeping shutdown hooks short.

Detaching either shared setup route disables the receiver for both Codex and Work, including cached plugin hooks. Saved evidence is preserved. `MAIDA_DEBUG=1` enables a disabled-capture diagnostic on stderr without logging the payload.

Exit codes: `0` captured, deduplicated, or intentionally disabled; `10` invalid input, conflicting evidence, or internal capture error. See [coverage, activation, and recovery](../codex-work.md) and the [official native hook contract](https://learn.chatgpt.com/docs/hooks).
