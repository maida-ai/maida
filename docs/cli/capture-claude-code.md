# `maida capture claude-code`

Starts a local OTLP HTTP/protobuf receiver for Claude Code logs and beta traces.

```bash
maida capture claude-code [--host 127.0.0.1] [--port 4318]
```

The receiver provides `/healthz`, `/v1/logs`, and `/v1/traces`, validates and
redacts batches before writing, and stores source captures under
`~/.maida/captures/claude-code/`. Progress is written to stderr. See
[Capture Claude Code telemetry](../claude-code.md) for exporter configuration and
the local storage contract.

**Exit codes:** `0` after normal shutdown; `10` receiver startup/runtime error.
