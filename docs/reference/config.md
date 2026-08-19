# Configuration reference

Maida is configured via **environment variables** and **YAML config files**. All settings are optional; defaults are tuned for safe, local-only tracing.

---

## Precedence

Configuration is merged in this order (highest wins):

1. **Environment variables**
2. **Project config:** `.maida/config.yaml` in project root (current working directory when config is loaded)
3. **User config:** `~/.maida/config.yaml`
4. **Defaults** (see below)

---

## Settings

### Data directory

| Env | YAML key | Default | Description |
|-----|----------|---------|-------------|
| `MAIDA_DATA_DIR` | `data_dir` | `~/.maida` | Base directory for runs. Runs are stored under `<data_dir>/runs/<trace_id>/`. |

**Example (env):**

```bash
export MAIDA_DATA_DIR=/path/to/my/maida/data
```

**Example (YAML):**

```yaml
# ~/.maida/config.yaml or .maida/config.yaml
data_dir: /path/to/my/maida/data
```

---

### Redaction

| Env | YAML key | Default | Description |
|-----|----------|---------|-------------|
| `MAIDA_REDACT` | `redact` | `1` (on) | Enable redaction. Use `1`, `true`, or `yes` to enable; any other value disables. |
| `MAIDA_REDACT_KEYS` | `redact_keys` | `api_key,token,authorization,cookie,secret,password` | Comma-separated list of key patterns (case-insensitive substring match). |
| `MAIDA_MAX_FIELD_BYTES` | `max_field_bytes` | `20000` | Maximum size in bytes for a string/field before truncation. Minimum enforced: 100. |

**Redaction behavior:**

- Applied **recursively** to nested dicts and lists (e.g. span attributes, projected event payloads, and meta).
- **Key match:** If a dict key contains any of the redact keys as a **case-insensitive substring**, the value is replaced with `__REDACTED__` (the key is not redacted; the value is). Example: `auth_token` matches `token`; `API_KEY` matches `api_key`.
- **Recursion depth:** Traversal is limited to depth 10 to avoid pathological structures; deeper values are replaced with the truncation marker.

**Truncation behavior:**

- Strings (and other values serialized to strings) longer than `MAIDA_MAX_FIELD_BYTES` bytes (UTF-8) are truncated and suffixed with `__TRUNCATED__`.
- At the recursion depth limit (10), the value is replaced with `__TRUNCATED__`.

**Example (env):**

```bash
export MAIDA_REDACT=1
export MAIDA_REDACT_KEYS="api_key,token,password,secret"
export MAIDA_MAX_FIELD_BYTES=10000
```

**Example (YAML):**

```yaml
redact: true
redact_keys:
  - api_key
  - token
  - authorization
  - password
max_field_bytes: 10000
```

---

### Loop detection

| Env | YAML key | Default | Description |
|-----|----------|---------|-------------|
| `MAIDA_LOOP_WINDOW` | `loop_window` | `12` | Number of recent events to consider for pattern detection. Minimum: 4. |
| `MAIDA_LOOP_REPETITIONS` | `loop_repetitions` | `3` | Consecutive repetitions of a pattern required to emit `LOOP_WARNING`. Minimum: 2. |

**Example (env):**

```bash
export MAIDA_LOOP_WINDOW=20
export MAIDA_LOOP_REPETITIONS=4
```

**Example (YAML):**

```yaml
loop_window: 20
loop_repetitions: 4
```

---

### Guardrails

Guardrails are opt-in limits that stop a run after Maida has enough evidence to show why it was aborted. They are applied after events are recorded, so the trace still contains the event that crossed the threshold.

| Env | YAML key | Default | Description |
|-----|----------|---------|-------------|
| `MAIDA_STOP_ON_LOOP` | `guardrails.stop_on_loop` | `false` | Abort when loop detection emits `LOOP_WARNING`. |
| `MAIDA_STOP_ON_LOOP_MIN_REPETITIONS` | `guardrails.stop_on_loop_min_repetitions` | `3` | Minimum repeated pattern count required to abort when `stop_on_loop` is enabled. Minimum: 2. |
| `MAIDA_MAX_LLM_CALLS` | `guardrails.max_llm_calls` | `null` | Abort after more than N LLM calls. Triggers at N+1. |
| `MAIDA_MAX_TOOL_CALLS` | `guardrails.max_tool_calls` | `null` | Abort after more than N tool calls. Triggers at N+1. |
| `MAIDA_MAX_EVENTS` | `guardrails.max_events` | `null` | Abort after more than N total events. |
| `MAIDA_MAX_DURATION_S` | `guardrails.max_duration_s` | `null` | Abort when elapsed run time reaches the configured number of seconds. |

**Important behavior:**

- **Existing event view only:** guardrails use normal projected `LOOP_WARNING`, `ERROR`, and `RUN_END` records; they do not add special trace types.
- **Loop aborts:** if `stop_on_loop=true`, Maida writes `LOOP_WARNING` first, then aborts.
- **Count-based limits:** `max_llm_calls=10` allows 10 calls and aborts after the 11th is recorded.
- **Exception propagation:** guardrail aborts raise `LoopAbort` or `GuardrailExceeded`; they are not swallowed.
- **Decorator/context-manager args win:** values passed to `@trace(...)` or `traced_run(...)` override env and YAML config.

**Example (env):**

```bash
export MAIDA_STOP_ON_LOOP=1
export MAIDA_STOP_ON_LOOP_MIN_REPETITIONS=3
export MAIDA_MAX_LLM_CALLS=50
export MAIDA_MAX_TOOL_CALLS=50
export MAIDA_MAX_EVENTS=200
export MAIDA_MAX_DURATION_S=60
```

**Example (YAML):**

```yaml
guardrails:
  stop_on_loop: true
  stop_on_loop_min_repetitions: 3
  max_llm_calls: 50
  max_tool_calls: 50
  max_events: 200
  max_duration_s: 60
```

See [Guardrails](../guardrails.md) for usage examples and lifecycle details.

---

### Run name (env only)

| Env | YAML | Default | Description |
|-----|------|---------|-------------|
| `MAIDA_RUN_NAME` | *(not in YAML)* | *(derived)* | Override the run name for the current process. Used when starting a run via `@trace`, `traced_run()`, or implicit run. If unset, run name is the explicit name argument, or a default like `path/to/script.py:main - 2026-02-18 14:12`. |

**Example:**

```bash
export MAIDA_RUN_NAME="my-experiment-v1"
```

---

### Implicit run (env only)

| Env | YAML | Default | Description |
|-----|------|---------|-------------|
| `MAIDA_IMPLICIT_RUN` | *(not in YAML)* | unset (off) | If set to `1`, the first `record_*` call with no active run creates an implicit run; all subsequent recorder calls attach to it until process exit. |

This is useful for scripts without a single `@trace` entrypoint. Only read from the environment; not configurable via YAML.

**Example:**

```bash
export MAIDA_IMPLICIT_RUN=1
```

---

## Full YAML example

```yaml
# ~/.maida/config.yaml or .maida/config.yaml
data_dir: ~/.maida
redact: true
redact_keys:
  - api_key
  - token
  - authorization
  - cookie
  - secret
  - password
max_field_bytes: 20000
loop_window: 12
loop_repetitions: 3
guardrails:
  stop_on_loop: false
  stop_on_loop_min_repetitions: 3
  max_llm_calls: null
  max_tool_calls: null
  max_events: null
  max_duration_s: null
```

---

## Safe-by-default local traces

- **Redaction is on by default** so that common secret-key values are not written to disk.
- **Data directory** defaults to `~/.maida` so traces are local by default.
- Maida.AI does not receive traces, prompts, outputs, source code, logs, or secrets by default. Local traces can still contain sensitive content depending on what your instrumentation records, so keep redaction on unless you are working in a trusted local setting.
- No cloud storage is used by the default trace-storage path. Override only what you need (e.g. `MAIDA_DATA_DIR` for project-local storage, or `MAIDA_REDACT=0` for trusted local inspection with full payloads).
