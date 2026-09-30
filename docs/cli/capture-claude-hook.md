# `maida capture claude-hook`

Reads exactly one supported Claude Code command-hook payload from stdin and
appends it to the hashed session capture without returning a hook decision.

```bash
maida capture claude-hook
```

The handler supports `SessionStart`, `PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `PermissionDenied`, and `SessionEnd`. Successful handling
writes nothing to stdout or stderr. The command is passive: it never returns
allow, deny, retry, or context fields and never uses Claude's blocking exit
code 2. `SessionEnd` closes and imports the segment automatically; abrupt
segments remain available to `maida import claude-code`.

When the repository pointer has `enabled: false`, the handler skips capture and exits `0`. Set `MAIDA_DEBUG=1` in Claude Code's environment to see a diagnostic on stderr naming the repository and reconnect command. The diagnostic never includes the hook payload. It is also emitted at debug level through the `maida.cli` logger.

**Exit codes:** `0` captured or intentionally disabled; `10` invalid payload, conflicting delivery, automatic-import failure, or internal error.

See [Command-hook fallback](../claude-code.md#command-hook-fallback) for the compact
project settings configuration, lifecycle segmentation, and privacy contract.
