# Trace format (public contract)

This page describes the **public trace format** for Maida (`spec_version: "0.2.0"`). Traces use **OpenTelemetry spans** as the internal representation and are stored locally as **JSONL span records** plus a **run metadata file** (`meta.json`). The format is a public contract for local tooling and integrations.

**Versioning:** The trace format is independently versioned with full semantic versioning (`0.2.0`). Patch releases clarify or fix compatible serialization, minor releases add optional fields, and major releases contain breaking changes. Readers tolerate additive unknown fields. The loader also accepts the legacy `0.2` spelling and compatible `0.2.x` patch versions.

The versioned JSON Schemas in the Maida core repository are normative for the
serializable `meta.json` and span-record shapes. This reference defines the
cross-record lifecycle and topology semantics that JSON Schema cannot express.
Published version directories are immutable; the unversioned schema files are
current-version aliases. See the [external emitter guide](trace-emitter.md) for
a minimal trace and multi-thread examples.

---

## Run storage layout

Maida stores local runs under the configured data directory. The default is `~/.maida`, and callers can override it with `MAIDA_DATA_DIR` or config. The canonical per-run directory is `<data_dir>/runs/<trace_id>/`, where `<trace_id>` is the lowercase 32-hex-character OpenTelemetry trace ID.

```text
<data_dir>/
  runs/
    <trace_id>/
      meta.json
      spans.jsonl
```

`meta.json` and `spans.jsonl` are the required files for a completed `spec_version: "0.2.0"` run directory:

- **`meta.json`** - run metadata. It may be created while the run is active and is finalized when the root span ends.
- **`spans.jsonl`** - append-only span log. Each non-empty line is one serialized OpenTelemetry span JSON object.

Active runs are provisional: a viewer or external tool may observe `status: "running"`, `ended_at: null`, `duration_ms: null`, or a partially populated `spans.jsonl` while the process is still executing. For completed runs, consumers should require both files, use `meta.json.status` to distinguish `ok` from `error`, and tolerate additive files that are not part of this public contract.

- **Ordering:** Spans in `spans.jsonl` are in export order; use `start_time` for logical ordering.
- **Span hierarchy:** Root span (no `parent_span_id`) represents the run itself; child spans represent LLM calls, tool calls, state updates, warnings, and errors.

The CLI still uses the user-facing argument name `RUN_ID` in several commands for compatibility; in current storage that value resolves to a full OTel `trace_id`, and short prefixes are accepted when they uniquely match a run.

### Files and readers

| Path or payload | Required? | Stable for external tools? | Notes |
|-----------------|-----------|----------------------------|-------|
| `runs/<trace_id>/meta.json` | Yes | Yes | Run discovery and summary metadata. The file may exist with `status: "running"` before the run finishes. |
| `runs/<trace_id>/spans.jsonl` | Yes | Yes | Append-only OTel span records. Read one JSON object per line and ignore fields you do not understand. |
| `maida export` JSON | Optional generated artifact | Yes | Portable single-file view with `spec_version`, `run`, and projected `events`. |
| Viewer API `/api/runs/{trace_id}/spans` | Runtime API | Yes | Returns `spec_version`, `trace_id`, raw `spans`, and projected `events`. |
| Temporary files such as `.meta.json.<pid>.tmp` | No | No | Internal atomic-write implementation detail; do not read or depend on them. |

The version in `meta.json` applies to the complete run directory. External
tools should ignore unknown additive fields, preserve fields they do not
understand when rewriting data, and use `maida validate-trace` before relying
on externally emitted traces.

**Redaction and truncation:** All span attributes and event payloads written to disk pass through redaction and truncation before being written. See the configuration reference for `redact`, `redact_keys`, and `max_field_bytes`.

---

## Span envelope (all spans)

Every OTel span is serialized as a single JSON object with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | string | 32-hex-character OTel trace ID |
| `span_id` | string | 16-hex-character OTel span ID |
| `parent_span_id` | string \| null | 16-hex-character parent span ID, or `null` for root |
| `name` | string | Span name (e.g. model name, tool name, run name) |
| `kind` | string | `INTERNAL`, `CLIENT`, `SERVER`, `PRODUCER`, `CONSUMER` |
| `start_time` | string | UTC ISO8601 with microsecond precision and trailing `Z` |
| `end_time` | string \| null | UTC ISO8601 with microsecond precision and trailing `Z` |
| `duration_ms` | integer \| null | Duration in milliseconds |
| `attributes` | object | Key-value pairs (string, bool, int, float values) |
| `events` | array | In-span events (name, timestamp, attributes) |
| `status_code` | string | `OK`, `ERROR`, or `UNSET` |
| `status_description` | string | Error description when status is `ERROR` |

### Derived event view

Consumers that need the v0.1-style event list can use the `spans_to_events()` projection, which projects the span tree into a flat event list with `spec_version`, `event_type`, `ts`, `payload`, and related fields. This compatibility projection is what baselines, assertions, diffs, exports, and the local viewer use when they need event-like records.

The projection is deterministic for a given span list:

- every stored span becomes one primary event-like object
- the root span becomes `RUN_START`
- child spans become `LLM_CALL`, `TOOL_CALL`, `STATE_UPDATE`, `LOOP_WARNING`, `ERROR`, or `UNKNOWN` based on documented attributes and span events
- root-span `exception`, `state`, and `maida.loop.warning` span events are surfaced as additional projected events
- a synthetic `RUN_END` event is appended from the root span's end state
- projected events are sorted by `ts`

Projection rules are part of the public contract at the event-type level: Maida preserves the event types and payload shapes below for consumer-facing workflows. The exact internal helper names and private implementation modules that perform the projection are not public API.

---

## Event types

| Type | Description |
|------|-------------|
| `RUN_START` | Run started (emitted by `@trace` / `traced_run`) |
| `RUN_END` | Run finished (ok or error) |
| `LLM_CALL` | One LLM invocation (model, prompt, response, usage) |
| `TOOL_CALL` | One tool invocation (name, args, result, status) |
| `STATE_UPDATE` | State snapshot or diff (e.g. between steps) |
| `ERROR` | Exception captured (type, message, stack) |
| `LOOP_WARNING` | Loop detection: repeated pattern in recent events |

---

## Payload schemas by event type

### RUN_START

```json
{
  "run_name": "optional string or null",
  "python_version": "3.11.7",
  "platform": "darwin | linux | win32",
  "cwd": "/path/to/cwd",
  "argv": ["script.py", "arg1"]
}
```

- **run_name** is set from: `MAIDA_RUN_NAME` (env), explicit `@trace("...")` / `@trace(name="...")` or `traced_run(name="...")`, or default `path:function - YYYY-MM-DD HH:MM`. See [configuration reference](config.md#run-name-env-only).
- **argv** may contain secrets; values for options matching redact keys are redacted before write.

### RUN_END

```json
{
  "status": "ok | error"
}
```

### LLM_CALL

```json
{
  "model": "string",
  "prompt": "string | object | null",
  "response": "string | object | null",
  "usage": {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0
  },
  "provider": "openai | anthropic | local | unknown",
  "temperature": 0.0,
  "stop_reason": "string | null",
  "status": "ok | error",
  "error": "object | null"
}
```

- `usage` fields may be `null` if unknown.
- `prompt` and `response` may be redacted or truncated by config.
- When `status` is `"error"`, `error` is an object with `error_type`, `message`, and optional `stack` (same shape as ERROR event payload).

### TOOL_CALL

```json
{
  "tool_name": "string",
  "args": "object | string | null",
  "result": "object | string | null",
  "status": "ok | error",
  "error": "object | null"
}
```

- When `status` is `"error"`, `error` is an object with `error_type`, `message`, and optional `stack` (same shape as ERROR event payload).

### STATE_UPDATE

```json
{
  "state": "object | string | null",
  "diff": "object | string | null"
}
```

- `diff` is optional; may be omitted if not computed.

### ERROR

Error payloads use a consistent shape (same for standalone ERROR events and nested `error` in LLM_CALL/TOOL_CALL):

```json
{
  "error_type": "ExceptionClassName",
  "message": "string",
  "stack": "string | null"
}
```

- Use **`error_type`** (not `type`) for the exception class name.
- Guardrail aborts also use `ERROR`; consumers should use `error_type` values such as `GuardrailExceeded` or `LoopAbort` to distinguish intentional guardrail stops.

### LOOP_WARNING

```json
{
  "pattern": "string",
  "pattern_type": "repeated_call | cycle",
  "pattern_length": 1,
  "repetitions": 3,
  "window_size": 6,
  "evidence_event_ids": ["event_uuid_1", "event_uuid_2"]
}
```

- Emitted at most once per run per distinct pattern (deduplicated).
- Tool-call patterns include a bounded structural signature of `args` when
  present. Keys and value types contribute to the signature; raw scalar values
  do not.
- `pattern_type` is `repeated_call` for a one-call pattern and `cycle` for a
  multi-call pattern. `pattern_length` is the number of signatures in the
  repeated block.
- If `stop_on_loop` guardrails are enabled, `LOOP_WARNING` is still written first and is then followed by `ERROR` and `RUN_END(status="error")`.

---

## meta.json schema

Each run has a `meta.json` file in its directory. It is created as running metadata when child spans are exported and overwritten with final metadata when the root span ends.

All fields in this table are required keys for the current `meta.json` contract. Fields whose type includes `null` are still required keys, but may be `null` while a run is active or when the value is not available.

| Field | Type | Description |
|-------|------|-------------|
| `spec_version` | string | Storage contract version (`"0.2.0"`) |
| `trace_id` | string | 32-hex-character OTel trace ID |
| `run_name` | string \| null | Optional run label |
| `started_at` | string | UTC ISO8601 with microsecond precision and `Z` |
| `ended_at` | string \| null | Set when run finishes |
| `duration_ms` | integer \| null | Total run duration in ms |
| `status` | string | `"running"` \| `"ok"` \| `"error"` |
| `counts` | object | See below |

**counts** object:

```json
{
  "llm_calls": 0,
  "tool_calls": 0,
  "errors": 0,
  "loop_warnings": 0
}
```

There are no stable optional `meta.json` fields in the current contract. Future optional fields may be added without a `spec_version` bump, so external tools should ignore unknown fields rather than fail closed. Tools that modify metadata should preserve unknown fields.

`spec_version` in `meta.json` (`"0.2.0"`) declares the storage contract version in-band. Individual span records in `spans.jsonl` do **not** repeat `spec_version`; the version from `meta.json` applies to the entire run directory. The version is also present in public API/export/projection envelopes that include `spec_version: "0.2.0"`:

- `GET /api/runs`
- `GET /api/runs/{trace_id}/spans`
- `GET /api/runs/{trace_id}/paths`
- `maida list --json`
- `maida export`
- projected event objects returned by `spans_to_events()`

### Lifecycle semantics

- **During a run:** child spans are appended to `spans.jsonl`; `meta.json` may appear with `status: "running"` so the local viewer can discover active runs.
- **On run end:** `meta.json` is overwritten with final `status`, `ended_at`, `duration_ms`, and `counts`.

---

## Versioning note

The trace format is a **public contract** versioned independently from the Maida package version. All releases using the `0.2` compatibility line share this format. Additive changes such as optional fields or additional span attributes remain compatible.

- **Patch releases** clarify or fix serialization without changing accepted documents.
- **Minor releases** add optional, backward-compatible fields or signals.
- **Major releases** contain breaking changes such as removed or renamed fields, changed required types or semantics, or a changed storage layout.

The versioned JSON Schemas and this semantic reference are maintained together.
Once published, each versioned schema directory is **immutable**. The
[schema changelog](https://github.com/maida-ai/maida/blob/main/schemas/trace/CHANGELOG.md)
records each published line.

### Stable versus internal

External tooling may rely on:

- The `runs/<trace_id>/meta.json` and `runs/<trace_id>/spans.jsonl` storage layout.
- The in-band `spec_version` in `meta.json`.
- The required fields, types, and lifecycle semantics documented on this page.
- The required span envelope keys documented on this page.
- The `spans_to_events()` projection keys: `spec_version`, `event_id`, `run_id`, `parent_id`, `event_type`, `ts`, `duration_ms`, `name`, `payload`, and `meta`.
- The projected event types and payload shapes documented here.
- Public API, export, and projection envelopes that include `spec_version`.
- Short trace ID prefix resolution through the CLI.

External tooling should not rely on:

- Temporary atomic-write files, write timing, or filesystem implementation details beyond the documented lifecycle.
- Private Python module names, helper function names, or internal class names.
- Undocumented span attributes or event attributes staying unchanged.
- Legacy v0.1 files (`run.json`, `events.jsonl`) for new runs.

### Compatibility expectations

The public compatibility boundary starts at `spec_version: "0.2.0"`. Readers accept the legacy `0.2` spelling and compatible `0.2.x` patch versions, but new emitters must declare `0.2.0`. Maida does not promise full backwards compatibility for pre-v0.2 local run directories. Legacy v0.1 files (`run.json`, `events.jsonl`) may be recognized by thin compatibility readers so commands can fail with clear upgrade or migration guidance, but external tools should not treat v0.1 as a supported storage contract.

For current-format traces, readers should fail closed on malformed required files or unsupported future `spec_version` values, while keeping validation errors actionable and free of raw prompt, response, tool-argument, or secret payloads. Additive fields in v0.2 should be ignored unless this page documents them as required.

### CLI commands that read and write runs

- [`maida demo`](../cli/demo.md) and instrumented SDK runs write local traces.
- [`maida list`](../cli/list.md) reads `meta.json` to discover recent runs.
- [`maida view`](../cli/view.md) reads run metadata and span data through the local viewer API.
- [`maida export`](../cli/export.md) reads `meta.json` plus `spans.jsonl` and writes a portable JSON envelope with `spec_version`, run metadata, and projected events.
- [`maida validate-trace`](../cli/validate-trace.md) validates an external native trace without installing or modifying it.
- [`maida run`](../cli/run.md) executes isolated candidate trials and
  evaluates the current policy-v2 gate.
- [`maida baseline`](../cli/baseline.md) and [`maida diff`](../cli/diff.md)
  read trace IDs, span data, and projected events.
- [`maida accept`](../cli/accept.md) reads a run and rewrites an existing
  baseline after an intentional behavior change.
- [`maida assert`](../cli/assert.md) remains the legacy single-run reader
  for migration and direct completed-trace inspection.

### Changes from v0.1

- Storage files renamed: `run.json` -> `meta.json`, `events.jsonl` -> `spans.jsonl`
- Run directory keyed by OTel `trace_id` (32 hex chars) instead of UUIDv4 `run_id`
- Internal representation uses OTel span model with `trace_id`, `span_id`, `parent_span_id` hierarchy
- LLM calls use GenAI semantic convention attribute names (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*`)
- Consumer-facing event view preserved via `spans_to_events()` projection
- `spec_version` bumped to `"0.2.0"`
