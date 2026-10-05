# `maida capture codex-hook`

**Unreleased.** A passive stdin receiver for native Codex hooks. Use [automatic setup](../codex.md) rather than managing a receiver or importing sessions manually.

```bash
maida capture codex-hook
```

The receiver reads one bounded JSON object and writes sanitized repository/session/turn evidence synchronously. Supported events are `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `Interrupt` and `SessionEnd`. It does not configure or depend on permission, subagent or compaction hooks. Root `Stop` and complete observed tool pairs establish completion; interruption or session exit alone cannot create success. A normal exit preserves an already completed turn.

Lifecycle identity is hashed. `UserPromptSubmit.prompt`, `Stop.last_assistant_message`, transcript contents and transcript paths are never persisted, including when redaction is disabled. Sanitized tool inputs and results remain behavioral evidence. Available agent identity distinguishes tool pairs but does not claim complete subagent topology. No allow, deny, retry or approval decision is returned to Codex.

Successful, duplicate or disabled deliveries emit no output. Conflicting deliveries require recovery. `maida check` validates and materializes immutable snapshots later, keeping hooks short. Detach disables recording from already-open native sessions; saved evidence remains available.

Exit codes: `0` captured, deduplicated or disabled; `10` invalid input, conflicting evidence or internal failure. See [coverage and recovery](../codex.md) and the [official hook contract](https://learn.chatgpt.com/docs/hooks).
