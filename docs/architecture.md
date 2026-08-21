# Architecture

How Maida works: OTel span schema, local storage, viewer API, UI, guardrails, integrations, and loop detection. These pieces provide the behavioral evidence used by baselines, assertions, diffs, and downstream reliability workflows. For the full public contract, see the [Trace format](reference/trace-format.md) reference.

---

## Span schema

Maida stores completed runs as OpenTelemetry-compatible spans. Every stored span is a JSON object with a common set of top-level fields:

| Field | Type | Description |
|---|---|---|
| `trace_id` | 32-hex-character string | OTel trace ID for the run |
| `span_id` | 16-hex-character string | OTel span ID |
| `parent_span_id` | 16-hex-character string or `null` | Parent span ID; `null` for the run root span |
| `name` | string | Label (run name, tool name, model name, etc.) |
| `kind` | string | OTel span kind such as `INTERNAL` or `CLIENT` |
| `start_time` | ISO8601 UTC with microseconds | Span start time |
| `end_time` | ISO8601 UTC with microseconds or `null` | Span end time |
| `duration_ms` | integer or `null` | Duration if applicable |
| `attributes` | object | Redacted/truncated span attributes |
| `events` | array | In-span events |
| `status_code` | string | `OK`, `ERROR`, or `UNSET` |
| `status_description` | string | Error description when present |

The root span represents the whole run. Child spans represent LLM calls, tool calls, state updates, loop warnings, and errors.

### Derived event view

| Type | Emitted by | Payload highlights |
|---|---|---|
| `RUN_START` | `@trace` / `traced_run` | `run_name`, `python_version`, `platform`, `cwd`, `argv` |
| `RUN_END` | `@trace` / `traced_run` | `status` (`ok` / `error`), `summary` (counts + duration) |
| `LLM_CALL` | `record_llm_call()` | `model`, `prompt`, `response`, `usage`, `provider`, `status`, `error` |
| `TOOL_CALL` | `record_tool_call()` | `tool_name`, `args`, `result`, `status`, `error` |
| `STATE_UPDATE` | `record_state()` | `state`, `diff` |
| `ERROR` | `@trace` (on exception) | `error_type`, `message`, `stack` |
| `LOOP_WARNING` | Automatic detection | `pattern`, `pattern_type`, `pattern_length`, `repetitions`, `window_size`, `evidence_event_ids` |

Some Maida workflows still need a flat event list. `spans_to_events()` projects the OTel span tree into event-like records such as `RUN_START`, `LLM_CALL`, `TOOL_CALL`, `STATE_UPDATE`, `ERROR`, `LOOP_WARNING`, and `RUN_END`. Baselines, assertions, diffs, and the viewer can use this projected view without changing the storage contract back to event files.

---

## Storage layout

- **Base directory:** `~/.maida/` (or `MAIDA_DATA_DIR`).
- **Per run:** `runs/<trace_id>/`
  - **meta.json** - Run metadata: `spec_version`, `trace_id`, `run_name`, `started_at`, `ended_at`, `duration_ms`, `status`, `counts` (llm_calls, tool_calls, errors, loop_warnings).
  - **spans.jsonl** - Append-only OTel span records; one span JSON object per line.

`meta.json` may be created with `status: "running"` while child spans are exported. When the root span ends, `meta.json` is overwritten with final status, counts, end time, and duration.

The CLI and some JSON payloads still use the user-facing term `run_id` for compatibility. In current storage, that value resolves to the OTel `trace_id`; short prefixes are resolved with trace ID prefix matching.

---

## Viewer API

The local server (FastAPI) exposes:

| Endpoint | Description |
|----------|-------------|
| `GET /api/runs` | List recent runs (metadata only). |
| `GET /api/runs/{trace_id}` | Run metadata (`meta.json`). |
| `GET /api/runs/{trace_id}/spans` | Span array plus the compatibility `events` projection for the run. |
| `GET /api/runs/{trace_id}/paths` | Local filesystem paths for the run (`run_dir`, `meta_json`, `spans_jsonl`). |
| `GET /api/runs/{trace_id}/rename` | Validate that a run can be renamed. |
| `POST /api/runs/{trace_id}/rename` | Rename a run (body: `{"run_name": "..."}`, updates `meta.json`). |
| `DELETE /api/runs/{trace_id}` | Delete a run directory and its contents (returns 204). |
| `GET /` | Static UI (`maida/ui_static/index.html`). |

Default bind: `127.0.0.1:8712`. The UI fetches runs and span data from these endpoints and renders a timeline.

---

## UI overview

- **Multi-file static UI** (HTML, JS, CSS); no build step. Served from `maida/ui_static/`.
- Loads run list from `/api/runs`; when a run is selected (or `run_id` / `run` in query), loads `/api/runs/{trace_id}/spans`.
- **Flat timeline:** the UI renders the compatibility `events` projection in chronological order. Each event can be expanded with payload/meta shown as formatted JSON.
- **Span data available:** the `/spans` response also includes raw OTel span records for consumers that need trace/span hierarchy.
- `LOOP_WARNING` events are displayed prominently.

---

## Guardrails

Guardrails are opt-in limits that stop a run before it burns more time, tokens, or tool calls than you intended. They are runtime safety limits and evidence capture tools; candidate policy enforcement belongs to `maida run`.

**Available guardrails:** `stop_on_loop`, `stop_on_loop_min_repetitions`, `max_llm_calls`, `max_tool_calls`, `max_events`, `max_duration_s`. All default to disabled.

**Behavior when a guardrail triggers:**

1. The triggering evidence is recorded using the existing projected event view
2. `LoopAbort` or `GuardrailExceeded` is raised
3. `ERROR` is recorded with `guardrail`, `threshold`, and `actual`
4. `RUN_END(status="error")` finalizes the projected run view
5. The exception propagates to the caller

**Configuration precedence** (highest wins): function args (`@trace(...)`, `traced_run(...)`) > env vars > project YAML > user YAML > defaults.

See [Guardrails](guardrails.md) for usage examples and [Configuration reference](reference/config.md) for all settings.

---

## Live-refresh viewer

The UI supports automatic polling so you can start `maida view` once and re-run your agent without manually refreshing.

- **Run list sidebar:** polls `GET /api/runs` every 3 seconds (configurable via `poll_runs` URL param, 1-60s). New runs appear automatically; removed runs are cleared from the sidebar.
- **Timeline:** when the current run has `status: "running"`, span data polls every 2 seconds from `/api/runs/{trace_id}/spans` (configurable via `poll_events` URL param, 1-60s). Polling stops when the run finishes.
- **Visibility gating:** polling pauses when the browser tab is not visible (Page Visibility API) and resumes when you switch back.
- **Visual indicator:** runs with `status: "running"` show a pulsing dot in the sidebar.

---

## Integration architecture

Maida adapters are thin translation layers that hook into a framework's callbacks and record LLM/tool evidence into the active Maida run. They do not introduce new trace types.

| Integration | Module | Hook mechanism |
|-------------|--------|----------------|
| LangChain / LangGraph | `maida.integrations.langchain` | Callback handler (`on_llm_start`/`on_tool_start`) |
| OpenAI Agents SDK | `maida.integrations.openai_agents` | Tracing processor (`GenerationSpanData`, `FunctionSpanData`, `HandoffSpanData`) |
| CrewAI | `maida.integrations.crewai` | Execution hooks (`before/after_llm_call`, `before/after_tool_call`) |

**Integration lifecycle:** `maida._integration_utils` provides `_invoke_run_enter` / `_invoke_run_exit` callbacks that adapters register with. This ensures adapters activate only when an explicit Maida run is active.

**Guardrails with integrations:** when a guardrail fires inside a framework callback, adapters raise `_MaidaAbortSignal` (a `BaseException` subclass) to bypass the framework's `except Exception` error handling and stop execution immediately. The lifecycle layer unwraps that signal so user code sees `LoopAbort` or `GuardrailExceeded`.

All integrations are optional dependencies; the core package does not depend on any framework. See [Integrations](integrations.md) for usage details.

---

## Loop detection

- **Input:** A sliding window of the last N projected events (default N=12; `MAIDA_LOOP_WINDOW`).
- **Signature:** Each event is reduced to a string: for `LLM_CALL` ->
  `"LLM_CALL:"+model`; for `TOOL_CALL` -> `"TOOL_CALL:"+tool_name` plus a
  bounded, shape-only argument signature when args are present; otherwise
  `event_type`. Scalar argument values never enter the signature.
- **Rule:** Look for a contiguous block of signatures that repeats K times (default K=3; `MAIDA_LOOP_REPETITIONS`) at the end of the window. A block of one signature is reported as `pattern_type: "repeated_call"`; a longer block is reported as `pattern_type: "cycle"`. If found, emit one `LOOP_WARNING` per distinct pattern per run (deduplicated by pattern + repetitions).
- **Payload:** `pattern` (e.g. "LLM_CALL:gpt-4 -> TOOL_CALL:search"),
  `pattern_type`, `pattern_length`, `repetitions`, `window_size`, and
  `evidence_event_ids`.

No ML; purely pattern-based on event type, name, and compact tool-argument structure to give quick feedback on repetitive agent behavior.
