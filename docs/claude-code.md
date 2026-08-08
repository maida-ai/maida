# Capture Claude Code telemetry

Maida can receive Claude Code's OpenTelemetry events and beta traces without
patching agent code. The receiver binds to loopback by default and writes only
to local Maida storage.

Start the receiver:

```bash
maida capture claude-code
```

In a second terminal, configure Claude Code to export logs and traces over
OTLP HTTP/protobuf:

```bash
export CLAUDE_CODE_ENABLE_TELEMETRY=1
export CLAUDE_CODE_ENHANCED_TELEMETRY_BETA=1
export OTEL_LOGS_EXPORTER=otlp
export OTEL_TRACES_EXPORTER=otlp
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
export OTEL_LOG_USER_PROMPTS=0
export OTEL_LOG_ASSISTANT_RESPONSES=0
export OTEL_LOG_TOOL_CONTENT=0
claude -p "Inspect the project and report its test command."
```

The receiver exposes `GET /healthz`, `POST /v1/logs`, and
`POST /v1/traces`. It requires protobuf requests from the `claude-code`
service, validates a complete export batch before writing, and rejects
malformed known signals. Unknown newer signals are retained so an additive
Claude Code update does not silently discard source evidence.

Captures are stored under:

```text
~/.maida/captures/claude-code/<hashed-session-id>/<segment>/
├── manifest.json
├── logs.jsonl
└── spans.jsonl
```

Session directory names never contain the raw Claude session ID. Existing
Maida redaction and field-size limits apply recursively before persistence;
numeric duration and token counters remain available for regression policy.
Exact exporter retries are deduplicated, while the receiver rejects a retry
that reuses a source identity with different content.

## Import a captured session

Stop the receiver after Claude exits, then import the session into the normal
Maida run store:

```bash
maida import claude-code --session-id "$CLAUDE_SESSION_ID"
```

The command selects the latest immutable segment by default. Pass
`--segment 0001` to select one explicitly or `--json` for a machine-readable
summary. Selection notices go to stderr, leaving JSON stdout clean.

Import creates a synthetic session root with interaction spans beneath it,
maps Claude model and tool activity onto Maida's existing `LLM_CALL` and
`TOOL_CALL` semantics, and keeps source IDs, commands, file paths, subagent
topology, Claude version, and mapping version in sanitized `maida.meta`.
Trace spans supply topology when present; logs enrich those spans and provide
a complete fallback when trace export is unavailable. Unknown source records
remain ordinary structural spans rather than creating Claude-specific Maida
event types.

The normalized trace ID and span IDs are deterministic. Re-importing identical
source data is a no-op. If the source bytes change after import, Maida refuses
to overwrite the installed run. The trace schema remains at its current
version; normal `maida baseline`, `maida assert`, `maida diff`, and `maida view`
commands work without a Claude-specific downstream policy path.

Use `--host` and `--port` to change the bind address. Keep the receiver on a
trusted interface: it intentionally has no authentication because its default
use is a local process or an isolated CI job.

See Claude Code's official [monitoring
reference](https://code.claude.com/docs/en/monitoring-usage) for exporter
variables and the beta trace hierarchy.
