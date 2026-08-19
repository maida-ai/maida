# Emit Maida traces without an SDK

An external system can participate in Maida's behavioral regression workflow
by writing Maida's native trace format directly. No Maida SDK, framework
adapter, network collector, or exported event envelope is required.

A trace is one directory containing two files:

```text
emitted-run/
  meta.json
  spans.jsonl
```

Validate it before handing it to Maida:

```bash
maida validate-trace emitted-run/
# or
maida validate-trace emitted-run/meta.json
```

Validation is read-only. It does not copy or install the trace. To make an
already validated trace available to `maida baseline`, `maida diff`, or the
legacy single-run `maida assert` compatibility interface, emit it into the configured native location
`<data_dir>/runs/<trace_id>/`.

## Minimal completed trace

New emitters must use the full trace version:

```yaml
spec_version: "0.2.0"
```

Minimal `meta.json`:

```json
{
  "spec_version": "0.2.0",
  "trace_id": "0123456789abcdef0123456789abcdef",
  "run_name": "external-agent",
  "started_at": "2026-08-08T10:00:00.000Z",
  "ended_at": "2026-08-08T10:00:01.000Z",
  "duration_ms": 1000,
  "status": "ok",
  "counts": {
    "llm_calls": 0,
    "tool_calls": 0,
    "errors": 0,
    "loop_warnings": 0
  }
}
```

Minimal `spans.jsonl` contains one JSON object per line. A completed trace has
exactly one root span:

```json
{"trace_id":"0123456789abcdef0123456789abcdef","span_id":"0123456789abcdef","parent_span_id":null,"name":"external-agent","kind":"INTERNAL","start_time":"2026-08-08T10:00:00.000Z","end_time":"2026-08-08T10:00:01.000Z","duration_ms":1000,"attributes":{"maida.run_name":"external-agent"},"events":[],"status_code":"OK","status_description":""}
```

Trace IDs are 32 lowercase hexadecimal characters. Span IDs are 16 lowercase
hexadecimal characters and must be unique within the trace. Every span uses the
trace ID from `meta.json`.

## Required fields

The normative serializable shapes live in the versioned JSON Schemas in the
Maida core repository:

- [`meta.schema.json`](https://github.com/maida-ai/maida/blob/main/schemas/trace/0.2.0/meta.schema.json)
- [`span.schema.json`](https://github.com/maida-ai/maida/blob/main/schemas/trace/0.2.0/span.schema.json)

Every `meta.json` field in the minimal example is required. Nullable terminal
fields remain present with `null` while `status` is `running`. The four count
fields are required and describe the normalized LLM calls, tool calls, errors,
and loop warnings that downstream baselines and policies evaluate.

Every span record requires the complete envelope shown above. Child spans set
`parent_span_id` to another span in the same completed trace. Completed traces
have one root, no duplicate span IDs, no missing parents, and no parent cycles.
Running traces may be incomplete while spans are still arriving.

### LLM calls

A child span becomes an `LLM_CALL` when its attributes contain
`gen_ai.system` or `gen_ai.operation.name`. Use GenAI semantic-convention usage
keys when known:

```json
{
  "gen_ai.system": "anthropic",
  "gen_ai.operation.name": "chat",
  "gen_ai.usage.input_tokens": 20,
  "gen_ai.usage.output_tokens": 8,
  "gen_ai.usage.total_tokens": 28
}
```

Prompts and responses are optional span events named `gen_ai.user.message` and
`gen_ai.assistant.message`, with their content in the event `attributes`.

### Tool calls

A child span becomes a `TOOL_CALL` when it has `maida.tool_name`. Arguments and
results are optional JSON strings in `maida.tool.args` and `maida.tool.result`
span events:

```json
{
  "maida.tool_name": "read",
  "maida.status": "ok"
}
```

Use `status_code: "ERROR"`, `maida.error_type`, `maida.error_message`, and the
optional `maida.error_stack` attribute for failed operations.

## Optional enrichments

Readers ignore additive unknown top-level fields and attribute keys. Put
emitter-specific data under a stable namespace such as `emitter.*`, or encode
a namespaced object in the `maida.meta` attribute. Do not invent new event
types for one emitter; unknown structural spans remain ordinary parent nodes
and the downstream gate stays framework-agnostic.

External emitters own redaction and truncation before writing. Do not place
credentials, private keys, customer data, or unrestricted prompt/tool content
in trace fields.

## Main thread and Subthreads

The single root span represents the run. Its direct action descendants form the
Main thread. Represent Subthreads with the same OpenTelemetry parent topology,
not a second thread schema:

```text
run root
├── model-main
└── delegate (TOOL_CALL)
    └── model-subthread
        └── read (TOOL_CALL)
```

Here `delegate.parent_span_id` points to the run root,
`model-subthread.parent_span_id` points to `delegate`, and
`read.parent_span_id` points to `model-subthread`. Optional thread names belong
in namespaced metadata; `parent_span_id` remains the structural source of
truth.

## Validation and Exit codes

Text output is intended for a developer:

```text
Valid Maida trace 01234567 (spec_version 0.2.0, 4 spans, status ok)
```

Use JSON in emitter CI:

```bash
maida validate-trace emitted-run/ --json
```

The result contains `valid`, `trace_id`, `spec_version`, `status`,
`span_count`, and `diagnostics`. Each diagnostic has a stable `code`,
`location`, and sanitized `message`; source payload values are never echoed.

| Exit | Meaning |
| ---: | --- |
| `0` | Trace is valid |
| `1` | Trace content violates the schema or cross-record semantics |
| `2` | Path is missing, unreadable, or not a run directory/`meta.json` |
| `10` | Unexpected validator failure |

## Versioning and Breaking changes

The trace schema is independent from the Maida package version. Versioned
schema snapshots are immutable.

- Patch releases clarify or fix serialization without changing which trace
  documents are accepted.
- Minor releases add optional, backward-compatible fields or signals.
- Major releases remove or rename fields, change required types or semantics,
  or change the native two-file layout. Those are Breaking changes.

Readers accept the legacy `0.2` spelling and compatible `0.2.x` patch versions.
New emitters must declare `0.2.0` until a later public version is published.
See the [trace schema changelog](https://github.com/maida-ai/maida/blob/main/schemas/trace/CHANGELOG.md)
for the published lines.
