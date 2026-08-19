# Capture Claude Code telemetry

Maida can receive Claude Code's OpenTelemetry events and beta traces without
patching agent code. The receiver binds to loopback by default and writes only
to local Maida storage.

## Core capture-to-gate workflow

The OTel receiver is the default capture path. The passive command hook is a
fallback when exporter access or tool detail is unavailable. Both transports
write the same source-capture contract and converge at one framework-agnostic
normalizer:

```text
Claude Code ── OTLP logs/traces ─┐
                                ├─ capture segment ─ import ─ Maida trace ─ gate
Claude Code ── command hooks ────┘                                  └─ scenario run
```

After import, baseline creation, assertion policy, structural diff, report
formatting, and trace viewing are transport-independent. `maida scenario run`
owns the reproducible headless orchestration and uses the OTel path internally;
it evaluates through the same stored-run service as `maida diff --capture`.

Choose one entry point:

- `maida capture claude-code` for normal local and CI telemetry capture.
- `maida capture claude-hook` for the observer-only lifecycle fallback.
- `maida scenario run` for pinned, isolated prompt fixtures and automatic
  capture/import/evaluation.

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
version; normal `maida baseline`, `maida diff`, and `maida view` commands work
without a Claude-specific downstream path. The legacy `maida assert` interface
can still inspect one imported completed trace during migration.

## Gate a capture locally

Use capture-backed diff mode to import the latest segment and run the same
behavioral gate that produces Maida's PR comment:

```bash
maida diff --capture "$CLAUDE_SESSION_ID" \
  --baseline .maida/baselines/my_agent.json \
  --policy .maida/policy.yaml \
  --format markdown
```

The command exits `0` when policy checks pass, `1` for a behavioral regression,
`2` for invalid input or capture data, and `10` for an import/runtime failure.
The selected segment and idempotent import result are reported on stderr;
stdout contains only the requested text, JSON, or Markdown assertion report.

## Run isolated scenarios

`maida scenario run` turns checked-in Claude Code prompts into reproducible
local or CI gates. It defaults to `.maida/scenarios.yaml`:

```yaml
version: 1
claude:
  executable: claude
  version: 2.1.220
  model: claude-haiku-4-5-20251001
  settings: .claude/settings.json
  mcp_config: .mcp.json
  timeout_seconds: 60
  max_budget_usd: 0.10
  max_turns: 2
  allowed_tools: [Read, Write]
scenarios:
  - id: edit-config
    fixture:
      root: tests/fixtures/scenarios/edit-config
      files:
        - input.txt
        - .claude/settings.json
        - .mcp.json
    prompt: Update input.txt exactly as requested by its first line.
    baseline: .maida/baselines/edit-config.json
    policy: .maida/policy.yaml
```

All paths are relative to the project root. Fixture entries, baselines, and
policies must be Git-tracked regular files. `settings` and `mcp_config` are
paths inside every fixture and must appear in its `files` list. Settings must
not install hooks, override the runner environment, or enable permission
bypass. The MCP file must contain an `mcpServers` object; use an empty object
when a scenario needs no MCP servers:

```json
{"mcpServers": {}}
```

Before execution, Maida verifies the exact Claude Code semantic version, full
model ID, config contents, unique scenario IDs, baselines, policies, and
tracked paths. For each scenario it:

1. Copies only the declared fixture files into a fresh temporary workspace.
2. Starts an ephemeral receiver on `127.0.0.1` and disables prompt/tool-content
   telemetry logging.
3. Invokes `claude -p` through an argv list with project-only settings, strict
   MCP config, `dontAsk`, an explicit tool allowlist, no session persistence,
   native budget/turn caps, and no dangerous permission bypass.
4. Terminates the whole process group on timeout, imports the capture, and
   evaluates it through the same assertion, structural diff, and report
   formatters as `maida diff --capture`.

Run all scenarios or one selected ID:

```bash
maida scenario run
maida scenario run --scenario edit-config --format json
```

The report never retains raw Claude stdout or stderr. A scenario status is
`pass`, `assertion_failed`, or `agent_failed`. Invalid manifest/environment
preflight exits `2`; runtime agent or capture failure takes precedence and
exits `10`; otherwise an assertion failure exits `1`, and all-pass exits `0`.

Claude's official [headless mode](https://code.claude.com/docs/en/headless) and
[CLI reference](https://code.claude.com/docs/en/cli-reference) document the
underlying non-interactive and safety flags.

Use `--host` and `--port` to change the bind address. Keep the receiver on a
trusted interface: it intentionally has no authentication because its default
use is a local process or an isolated CI job.

## Command-hook fallback

When OTLP is unavailable or does not include the tool input you need, install
the passive command hook below in the project's `.claude/settings.json`. The
example is ten nonblank lines and observes every supported lifecycle event:

```json
{
  "hooks": {
    "SessionStart": [{"hooks": [{"type": "command", "command": "maida capture claude-hook"}]}],
    "PreToolUse": [{"hooks": [{"type": "command", "command": "maida capture claude-hook"}]}],
    "PostToolUse": [{"hooks": [{"type": "command", "command": "maida capture claude-hook"}]}],
    "PostToolUseFailure": [{"hooks": [{"type": "command", "command": "maida capture claude-hook"}]}],
    "PermissionDenied": [{"hooks": [{"type": "command", "command": "maida capture claude-hook"}]}],
    "SessionEnd": [{"hooks": [{"type": "command", "command": "maida capture claude-hook"}]}]
  }
}
```

Claude sends one JSON object on stdin for each command-hook invocation. The
handler writes no stdout and returns no allow, deny, retry, or context fields,
so it never changes Claude's tool or permission behavior. Successful capture
exits 0. Capture errors use exit 10 rather than Claude's blocking exit code 2.

Hook records use the same hashed-session capture directory and normalizer as
OTLP. `PreToolUse` and its terminal event are paired by `tool_use_id`; successful,
failed, denied, preless, and incomplete calls all remain importable as ordinary
Maida `TOOL_CALL` spans. The raw session ID and transcript path are not stored.
Tool inputs and results pass through Maida's recursive redaction and field-size
limits before the atomic append.

`startup`, `resume`, `fork`, and `clear` start a new immutable capture segment.
The `compact` SessionStart source stays in the active segment. `SessionEnd`
closes and imports its segment automatically. If Claude exits abruptly before
that event, recover the active segment explicitly:

```bash
maida import claude-code --session-id "$CLAUDE_SESSION_ID"
```

See Claude Code's official [monitoring
reference](https://code.claude.com/docs/en/monitoring-usage) for exporter
variables and the beta trace hierarchy.

See Claude Code's official [hooks
reference](https://code.claude.com/docs/en/hooks) for command-hook stdin,
lifecycle, matcher, and exit-code behavior.
