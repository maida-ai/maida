# Architecture

How Maida works: event schema, storage, viewer API, UI, guardrails, and loop detection. These pieces provide the behavioral evidence used by baselines, assertions, diffs, and downstream reliability workflows. For the full public contract (envelope, event types, payload schemas, run.json), see the [Trace format](reference/trace-format.md) reference.

---

## Event schema

Every event is a JSON object with a common set of top-level fields:

| Field | Type | Description |
|---|---|---|
| `spec_version` | `"0.1"` | Schema version |
| `event_id` | UUID string | Unique event identifier |
| `run_id` | UUID string | Run this event belongs to |
| `parent_id` | UUID string or `null` | Parent event (for nesting) |
| `event_type` | string | One of the types below |
| `ts` | ISO8601 UTC (`2026-02-15T20:31:05.123Z`) | Timestamp with milliseconds |
| `duration_ms` | integer or `null` | Duration if applicable |
| `name` | string | Label (tool name, model name, etc.) |
| `payload` | object | Event-type-specific data |
| `meta` | object | Freeform user-defined metadata |

### Event types

| Type | Emitted by | Payload highlights |
|---|---|---|
| `RUN_START` | `@trace` / `traced_run` | `run_name`, `python_version`, `platform`, `cwd`, `argv` |
| `RUN_END` | `@trace` / `traced_run` | `status` (`ok` / `error`), `summary` (counts + duration) |
| `LLM_CALL` | `record_llm_call()` | `model`, `prompt`, `response`, `usage`, `provider`, `status`, `error` |
| `TOOL_CALL` | `record_tool_call()` | `tool_name`, `args`, `result`, `status`, `error` |
| `STATE_UPDATE` | `record_state()` | `state`, `diff` |
| `ERROR` | `@trace` (on exception) | `error_type`, `message`, `stack` |
| `LOOP_WARNING` | Automatic detection | `pattern`, `repetitions`, `window_size`, `evidence_event_ids` |

Events are written as one JSON object per line (JSONL) and flushed after each write.

---

## Storage layout

- **Base directory:** `~/.maida/` (or `MAIDA_DATA_DIR`).
- **Per run:** `runs/<run_id>/`
  - **run.json** - Run metadata: `run_id`, `run_name`, `started_at`, `ended_at`, `duration_ms`, `status`, `counts` (llm_calls, tool_calls, errors, loop_warnings), `last_event_ts`.
  - **events.jsonl** - Append-only; one event per line.

`run.json` is created at run start (status `running`) and updated at run end (status `ok` or `error`, counts, ended_at, duration_ms).

---

## Viewer API

The local server (FastAPI) exposes:

| Endpoint | Description |
|----------|-------------|
| `GET /api/runs` | List recent runs (metadata only). |
| `GET /api/runs/{run_id}` | Run metadata (run.json). |
| `GET /api/runs/{run_id}/events` | Events array for the run. |
| `GET /api/runs/{run_id}/paths` | Local filesystem paths for the run (run_dir, run_json, events_jsonl). |
| `POST /api/runs/{run_id}/rename` | Rename a run (body: `{"run_name": "..."}`, updates run.json). |
| `DELETE /api/runs/{run_id}` | Delete a run directory and its contents (returns 204). |
| `GET /` | Static UI (`maida/ui_static/index.html`). |

Default bind: `127.0.0.1:8712`. The UI fetches runs and events from these endpoints and renders a timeline.

---

## UI overview

- **Multi-file static UI** (HTML, JS, CSS); no build step. Served from `maida/ui_static/`.
- Loads run list from `/api/runs`; when a run is selected (or `run_id` in query), loads `/api/runs/{run_id}/events`.
- **Flat timeline:** events are shown in chronological order (write order / `ts`). Each event is expandable with payload shown as formatted JSON. Nesting by `parent_id` is not required.
- `LOOP_WARNING` events are displayed prominently.

---

## Guardrails

Guardrails are opt-in limits that stop a run before it burns more time, tokens, or tool calls than you intended. They are runtime safety limits and evidence capture tools; post-run policy enforcement belongs to `maida assert`.

**Available guardrails:** `stop_on_loop`, `stop_on_loop_min_repetitions`, `max_llm_calls`, `max_tool_calls`, `max_events`, `max_duration_s`. All default to disabled.

**Behavior when a guardrail triggers:**

1. The triggering event is recorded using existing event types (no new types)
2. `LoopAbort` or `GuardrailExceeded` is raised
3. `ERROR` event is recorded (payload includes `guardrail`, `threshold`, `actual`)
4. `RUN_END(status="error")` finalizes the run
5. The exception propagates to the caller

**Configuration precedence** (highest wins): function args (`@trace(...)`, `traced_run(...)`) > env vars > project YAML > user YAML > defaults.

See [Guardrails](guardrails.md) for usage examples, [Configuration reference](reference/config.md) for all settings.

---

## Live-refresh viewer

The UI supports automatic polling so you can start `maida view` once and re-run your agent without manually refreshing.

- **Run list sidebar:** polls `GET /api/runs` every 3 seconds (configurable via `poll_runs` URL param, 1–60s). New runs appear automatically; removed runs are cleared from the sidebar.
- **Event timeline:** when the current run has `status: "running"`, events poll every 2 seconds (configurable via `poll_events` URL param, 1–60s). Polling stops when the run finishes.
- **Visibility gating:** polling pauses when the browser tab is not visible (Page Visibility API) and resumes when you switch back.
- **Visual indicator:** runs with `status: "running"` show a pulsing dot in the sidebar.

---

## Integration architecture

Maida adapters are thin translation layers that hook into a framework's callbacks and emit `record_llm_call` / `record_tool_call` events. They do not introduce new event types.

| Integration | Module | Hook mechanism |
|-------------|--------|----------------|
| LangChain / LangGraph | `maida.integrations.langchain` | Callback handler (`on_llm_start`/`on_tool_start`) |
| OpenAI Agents SDK | `maida.integrations.openai_agents` | Tracing processor (`GenerationSpanData`, `FunctionSpanData`, `HandoffSpanData`) |
| CrewAI | `maida.integrations.crewai` | Execution hooks (`before/after_llm_call`, `before/after_tool_call`) |

**Integration lifecycle:** `maida._integration_utils` provides `_invoke_run_enter` / `_invoke_run_exit` callbacks that adapters register with. This ensures adapters activate only when an explicit Maida run is active.

**Guardrails with integrations:** when a guardrail fires inside a framework callback, adapters raise `_MaidaAbortSignal` (a `BaseException` subclass) to bypass the framework's `except Exception` error handling and stop execution immediately.

All integrations are optional dependencies; the core package does not depend on any framework. See [Integrations](integrations.md) for usage details.

---

## Loop detection

- **Input:** A sliding window of the last N events (default N=12; `MAIDA_LOOP_WINDOW`).
- **Signature:** Each event is reduced to a string: for `LLM_CALL` -> `"LLM_CALL:"+model`, for `TOOL_CALL` -> `"TOOL_CALL:"+tool_name`, else `event_type`.
- **Rule:** Look for a contiguous block of signatures that repeats K times (default K=3; `MAIDA_LOOP_REPETITIONS`) at the end of the window. If found, emit one `LOOP_WARNING` per distinct pattern per run (deduplicated by pattern + repetitions).
- **Payload:** `pattern` (e.g. "LLM_CALL:gpt-4 -> TOOL_CALL:search"), `repetitions`, `window_size`, `evidence_event_ids`.

No ML; purely pattern-based on event type and name to give quick feedback on repetitive agent behavior.
