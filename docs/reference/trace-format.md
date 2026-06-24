# Trace format (public contract)

This page describes the **public trace format** for Maida (`spec_version: "0.2"`). Traces use **OpenTelemetry spans** as the internal representation and are stored locally as **JSONL span records** plus a **run metadata file** (`meta.json`). The format is a public contract: consumers can rely on it for tooling and integrations.

**Versioning:** The trace format is versioned independently from the package version via `spec_version` (currently `"0.2"`). All Maida releases that use `spec_version "0.2"` share the same trace format. Additive changes (new optional fields, new event types) may be introduced without a spec version bump. Breaking changes will result in a new `spec_version`.

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

`meta.json` and `spans.jsonl` are the required files for a completed `spec_version: "0.2"` run directory:

- **`meta.json`** - run metadata. It may be created while the run is active and is finalized when the root span ends.
- **`spans.jsonl`** - append-only span log. Each non-empty line is one serialized OpenTelemetry span JSON object.

Active runs are provisional: a viewer or external tool may observe `status: "running"`, `ended_at: null`, `duration_ms: null`, or a partially populated `spans.jsonl` while the process is still executing. For completed runs, consumers should require both files, use `meta.json.status` to distinguish `ok` from `error`, and tolerate additive files that are not part of this public contract.

- **Ordering:** Spans in `spans.jsonl` are in export order; use `start_time` for logical ordering.
- **Span hierarchy:** Root span (no `parent_span_id`) represents the run itself; child spans represent LLM calls, tool calls, etc.

**Redaction and truncation:** All span attributes and event payloads written to disk pass through redaction and truncation before being written. See the configuration reference for `redact`, `redact_keys`, and `max_field_bytes`.

---

## Span envelope (all spans)

Every OTel span is serialized as a single JSON object with these fields:

| Field | Type | Description |
|-------|------|-------------|
| `spec_version` | string | Storage contract version (e.g. `"0.2"`) |
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

Consumers that need the v0.1-style event list can use the `spans_to_events()` projection, which projects the span tree into a flat event list with `event_type`, `ts`, `payload`, etc. This projection preserves the event shape documented below.

The projection is deterministic for a given span list:

- every stored span becomes one primary event-like object
- the root span becomes `RUN_START`
- child spans become `LLM_CALL`, `TOOL_CALL`, `STATE_UPDATE`, `LOOP_WARNING`, `ERROR`, or `UNKNOWN` based on documented attributes and span events
- root-span `exception`, `state`, and `maida.loop.warning` span events are surfaced as additional projected events
- a synthetic `RUN_END` event is appended from the root span's end state
- projected events are sorted by `ts`

The stored OTel spans are the durable local files. Projected events are the compatibility view used by `maida export`, baselines, assertions, diffs, and the local viewer.

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
  "repetitions": 3,
  "window_size": 6,
  "evidence_event_ids": ["event_uuid_1", "event_uuid_2"]
}
```

- Emitted at most once per run per distinct pattern (deduplicated).
- If `stop_on_loop` guardrails are enabled, `LOOP_WARNING` is still written first and is then followed by `ERROR` and `RUN_END(status="error")`.

---

## meta.json schema

Each run has a `meta.json` file in its directory. It is created as running metadata when child spans are exported and overwritten with final metadata when the root span ends.

All fields in this table are required keys for the current `meta.json` contract. Fields whose type includes `null` are still required keys, but may be `null` while a run is active or when the value is not available.

| Field | Type | Description |
|-------|------|-------------|
| `spec_version` | string | Storage contract version (`"0.2"`) |
| `trace_id` | string | 32-hex-character OTel trace ID |
| `run_name` | string \| null | Optional run label |
| `started_at` | string | UTC ISO8601 with µs and `Z` |
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

There are no stable optional `meta.json` fields in the current contract. Future optional fields may be added without a `spec_version` bump, so external tools should ignore unknown fields rather than fail closed.

`spec_version` in `meta.json` (`"0.2"`) declares the storage contract version in-band. The version is also present in public API/export/projection envelopes that include `spec_version: "0.2"`:

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

## Stability for external tooling

The trace format is a **public contract** versioned independently from the Maida package version. All releases using `spec_version "0.2"` share this format. Additive changes (for example, new optional fields, new event types, or additional span attributes) are allowed without a spec version bump. Breaking changes (removing fields, changing types, changing required field semantics, or changing storage file names) will be accompanied by a new `spec_version`.

The markdown reference on this page is canonical. JSON schemas in the repo root `schemas/` folder are best-effort helpers for tooling, not the source of truth.

### Stable for external tooling

- directory layout: `<data_dir>/runs/<trace_id>/`
- required files: `meta.json` and `spans.jsonl`
- required `meta.json` keys, value types, and lifecycle semantics documented above
- required span envelope keys documented above
- `spans_to_events()` projection keys: `spec_version`, `event_id`, `run_id`, `parent_id`, `event_type`, `ts`, `duration_ms`, `name`, `payload`, and `meta`
- documented event types and payload shapes
- `spec_version` behavior for public API/export/projection envelopes

### Internal and subject to change

- temporary files used for atomic writes
- exact export timing while a run is active
- private helper function names and module layout
- UI-only transformations and display state
- undocumented OTel attributes, in-span event attributes, or extra files
- legacy compatibility readers for older pre-`0.2` local files

External tools should rely on the stable contract above, ignore unknown additive fields, and avoid deriving schemas from private implementation details or a single observed run. Treat schema drift against this page as a compatibility issue.

## Related CLI commands

These commands read from or write to the storage contract documented here:

- [`maida list`](../cli.md#maida-list) reads `meta.json` for recent runs.
- [`maida view`](../cli.md#maida-view) serves `meta.json`, `spans.jsonl`, and the projected event timeline through the local viewer API.
- [`maida export`](../cli.md#maida-export) writes one JSON document containing `spec_version`, run metadata, and projected events.
- [`maida baseline`](../cli.md#maida-baseline) reads a run and captures stable structural metrics for future comparison.
- [`maida assert`](../cli.md#maida-assert) reads a run, baseline, and policy to evaluate behavioral regression checks.
- [`maida diff`](../cli.md#maida-diff) reads two runs or a run plus baseline and reports structural differences.

### Changes from v0.1

- Storage files renamed: `run.json` → `meta.json`, `events.jsonl` → `spans.jsonl`
- Run directory keyed by OTel `trace_id` (32 hex chars) instead of UUIDv4 `run_id`
- Internal representation uses OTel span model with `trace_id`, `span_id`, `parent_span_id` hierarchy
- LLM calls use GenAI semantic convention attribute names (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*`)
- Consumer-facing event view preserved via `spans_to_events()` projection
- `spec_version` bumped to `"0.2"`
