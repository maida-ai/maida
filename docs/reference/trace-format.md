# Trace format (public contract)

This page describes the **public trace format** for Maida (`spec_version: "0.2"`). Traces use **OpenTelemetry spans** as the internal representation and are stored locally as **JSONL span records** plus a **run metadata file** (`meta.json`). The format is a public contract: consumers can rely on it for tooling and integrations.

**Versioning:** The trace format is versioned independently from the package version via `spec_version` (currently `"0.2"`). All Maida releases that use `spec_version "0.2"` share the same trace format. Additive changes (new optional fields, new event types) may be introduced without a spec version bump. Breaking changes will result in a new `spec_version`.

---

## Overview

- **Per run:** One directory `runs/<trace_id_hex>/` containing:
  - **spans.jsonl** - append-only; one JSON object per line (one OTel span per line).
  - **meta.json** - run metadata; written at run end (overwritten on finalize).
- **Ordering:** Spans in `spans.jsonl` are in export order; `start_time` provides logical ordering.
- **Span hierarchy:** Root span (no `parent_span_id`) represents the run itself; child spans represent LLM calls, tool calls, etc.

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

Consumers that need the v0.1-style event list can use `spans_to_events()` which projects the span tree into a flat event list with `event_type`, `ts`, `payload`, etc. This projection preserves the event shape documented below.

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

Each run has a `meta.json` file in its directory. It is written when the root span ends.

**Required fields:**

| Field | Type | Description |
|-------|------|-------------|
| `trace_id` | string | 32-hex-character OTel trace ID |
| `run_name` | string \| null | Optional run label |
| `started_at` | string | UTC ISO8601 with µs and `Z` |
| `ended_at` | string \| null | Set when run finishes |
| `duration_ms` | integer \| null | Total run duration in ms |
| `status` | string | `"ok"` \| `"error"` |
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

### Lifecycle semantics

- **On run start:** No file is written until the root span ends.
- **On run end:** `meta.json` is written with final `status`, `ended_at`, `duration_ms`, and `counts`.

---

## Versioning note

The trace format is a **public contract** versioned independently from the Maida package version. All releases using `spec_version "0.2"` share this format. Additive changes (e.g. new optional fields, new event types) are allowed without a spec version bump. Breaking changes (removing fields, changing types or semantics) will be accompanied by a new `spec_version`. The markdown reference on this page is **canonical**; JSON schemas in the repo root `schemas/` folder are best-effort for tooling.

### Changes from v0.1

- Storage files renamed: `run.json` → `meta.json`, `events.jsonl` → `spans.jsonl`
- Run directory keyed by OTel `trace_id` (32 hex chars) instead of UUIDv4 `run_id`
- Internal representation uses OTel span model with `trace_id`, `span_id`, `parent_span_id` hierarchy
- LLM calls use GenAI semantic convention attribute names (`gen_ai.system`, `gen_ai.request.model`, `gen_ai.usage.*`)
- Consumer-facing event view preserved via `spans_to_events()` projection
- `spec_version` bumped to `"0.2"`
