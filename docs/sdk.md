# SDK

The Maida Python SDK exposes a decorator, a context manager, and three recording functions. All recording attaches to the current active run (via contextvars). If there is no active run, recorders no-op unless implicit runs are enabled.

For the exact shape of stored events and run metadata, see the [Trace format](reference/trace-format.md) reference.

---

## `@trace`

Decorator that turns a function into a traced run.

```python
from maida import trace

@trace
def run_agent():
    ...
```

You can also enable run guardrails directly on the decorator:

```python
from maida import trace


@trace(
    name="support_agent",
    stop_on_loop=True,
    max_llm_calls=12,
    max_tool_calls=20,
    max_events=100,
    max_duration_s=30,
)
def run_agent():
    ...
```

**Behavior:**

- When the function is called **and no run is active:** creates a new run, emits `RUN_START`, runs the function, then emits `RUN_END`. On exception, emits `ERROR` then `RUN_END` with status `error` and re-raises.
- When called **inside an already active run:** runs the function without creating a new run or extra run events. All `record_*` calls inside still attach to the outer run.
- When a guardrail is enabled and crossed: records the triggering event, raises `LoopAbort` or `GuardrailExceeded`, records `ERROR`, records `RUN_END(status="error")`, and re-raises.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str \| None` | `None` | Run name shown in the UI and CLI |
| `stop_on_loop` | `bool` | `False` | Abort when loop detection emits `LOOP_WARNING` |
| `stop_on_loop_min_repetitions` | `int` | `3` | Minimum repeated pattern count required to abort on loop |
| `max_llm_calls` | `int \| None` | `None` | Abort after more than N LLM calls |
| `max_tool_calls` | `int \| None` | `None` | Abort after more than N tool calls |
| `max_events` | `int \| None` | `None` | Abort after more than N total events |
| `max_duration_s` | `float \| None` | `None` | Abort when elapsed run time reaches the configured limit |

---

## `traced_run`

Context manager that starts a traced run. Useful when a decorator doesn't fit - for example, in scripts, notebooks, or dynamic workflows.

```python
from maida import traced_run, record_tool_call, record_llm_call

with traced_run(name="my_pipeline"):
    record_tool_call(name="fetch", args={"url": "..."}, result="...")
    record_llm_call(model="gpt-4", prompt="...", response="...")
```

Guardrails are available here too:

```python
from maida import traced_run


with traced_run(
    name="react_guarded",
    stop_on_loop=True,
    max_llm_calls=8,
    max_tool_calls=12,
    max_events=60,
    max_duration_s=20,
):
    ...
```

**Behavior** is identical to `@trace`: creates a run if none is active, otherwise attaches to the existing one.

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str \| None` | `None` | Run name (shown in `maida list` and the timeline) |
| `stop_on_loop` | `bool` | `False` | Abort when loop detection emits `LOOP_WARNING` |
| `stop_on_loop_min_repetitions` | `int` | `3` | Minimum repeated pattern count required to abort on loop |
| `max_llm_calls` | `int \| None` | `None` | Abort after more than N LLM calls |
| `max_tool_calls` | `int \| None` | `None` | Abort after more than N tool calls |
| `max_events` | `int \| None` | `None` | Abort after more than N total events |
| `max_duration_s` | `float \| None` | `None` | Abort when elapsed run time reaches the configured limit |

### Guardrail precedence

Guardrails are resolved in this order:

1. Arguments passed to `@trace(...)` or `traced_run(...)`
2. Environment variables
3. `.maida/config.yaml` in the current project
4. `~/.maida/config.yaml`
5. Defaults

See [Guardrails](guardrails.md) and the [configuration reference](reference/config.md) for the full config surface.

---

## `has_active_run`

Returns `True` when an explicit traced run is active in the current context (i.e. inside a `@trace`-decorated function or a `traced_run` block).

```python
from maida import has_active_run

if has_active_run():
    print("Inside a traced run")
```

Useful when integration code or utilities need to conditionally record events only when tracing is active, without creating an implicit run.

---

## `record_llm_call`

Record an LLM call event.

```python
from maida import record_llm_call

record_llm_call(
    model="gpt-4",
    prompt="Summarize the search results.",
    response="Found 2 users.",
    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    meta={"step": "summarize"},
    provider="openai",
    temperature=0.7,
    stop_reason="stop",
    status="ok",
    error=None,
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `model` | `str` | *(required)* | Model name (e.g. `"gpt-4"`, `"claude-3-opus"`) |
| `prompt` | `Any` | `None` | Prompt sent to the model (string, dict, or list) |
| `response` | `Any` | `None` | Model response |
| `usage` | `dict \| None` | `None` | Token usage: `prompt_tokens`, `completion_tokens`, `total_tokens` |
| `meta` | `dict \| None` | `None` | Freeform metadata (labels, tags, etc.) |
| `provider` | `str` | `"unknown"` | Provider name (`"openai"`, `"anthropic"`, `"local"`, etc.) |
| `temperature` | `float \| None` | `None` | Sampling temperature |
| `stop_reason` | `str \| None` | `None` | Why the model stopped (`"stop"`, `"length"`, etc.) |
| `status` | `str` | `"ok"` | `"ok"` or `"error"` |
| `error` | `str \| BaseException \| dict \| None` | `None` | Error details when `status="error"` |

Payload and meta are redacted and truncated according to config before storage.

---

## `record_tool_call`

Record a tool call event.

```python
from maida import record_tool_call

record_tool_call(
    name="search_db",
    args={"query": "active users"},
    result={"count": 42},
    meta=None,
    status="ok",
    error=None,
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | *(required)* | Tool name |
| `args` | `Any` | `None` | Arguments passed to the tool |
| `result` | `Any` | `None` | Tool return value |
| `meta` | `dict \| None` | `None` | Freeform metadata |
| `status` | `str` | `"ok"` | `"ok"` or `"error"` |
| `error` | `str \| BaseException \| dict \| None` | `None` | Error details when `status="error"` |

**Recording a failed tool call:**

```python
try:
    result = my_tool(args)
    record_tool_call(name="my_tool", args=args, result=result)
except Exception as e:
    record_tool_call(name="my_tool", args=args, status="error", error=e)
    raise
```

Payload and meta are redacted and truncated.

---

## `record_state`

Record a state-update event (e.g. agent state snapshot between steps).

```python
from maida import record_state

record_state(
    state={"step": 3, "messages": ["..."]},
    meta={"label": "after_search"},
    diff=None,
)
```

**Parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `state` | `Any` | `None` | State snapshot (object or string) |
| `meta` | `dict \| None` | `None` | Freeform metadata |
| `diff` | `Any` | `None` | Optional diff from previous state |

Redaction and truncation apply. Does not increment LLM/tool counts; useful for timeline context.

---

## Example: tracing a ReAct-style agent loop

A typical pattern - instrument a loop that alternates between LLM reasoning and tool execution:

```python
from maida import trace, record_llm_call, record_tool_call, record_state

TOOLS = {"search": search_fn, "calculator": calc_fn}

@trace
def react_agent(question: str):
    messages = [{"role": "user", "content": question}]

    for step in range(10):
        response = llm_chat(messages)
        record_llm_call(
            model="gpt-4",
            prompt=messages,
            response=response,
            usage=response.get("usage"),
        )

        action = parse_action(response)
        if action is None:
            return response["content"]

        tool_fn = TOOLS[action["tool"]]
        result = tool_fn(**action["args"])
        record_tool_call(
            name=action["tool"],
            args=action["args"],
            result=result,
        )

        messages.append({"role": "assistant", "content": response["content"]})
        messages.append({"role": "tool", "content": str(result)})
        record_state(state={"step": step, "messages_count": len(messages)})

    return "Max steps reached"
```

After running, `maida view` shows every LLM call, tool call, and state update in order - making it easy to see where the agent went wrong or got stuck in a loop.

---

## Implicit runs (`MAIDA_IMPLICIT_RUN=1`)

By default, calling `record_llm_call` / `record_tool_call` / `record_state` **outside** a `@trace`-decorated function or `traced_run` block does nothing.

If you set:

```bash
export MAIDA_IMPLICIT_RUN=1
```

then the first recorder call with no active run creates a single **implicit run**. All subsequent recorder calls attach to it until process exit, when the run is automatically finalized. Use this for scripts that don't have a single top-level entrypoint.

---

## Redaction and truncation

- **Redaction:** Dict keys matching configured patterns (e.g. `api_key`, `token`, `password`) have their values replaced with `__REDACTED__`. Applied recursively (depth limit: 10).
- **Truncation:** Strings exceeding `MAIDA_MAX_FIELD_BYTES` (default 20000) are truncated and suffixed with `__TRUNCATED__`.

**Config precedence (highest first):**

1. Environment variables (`MAIDA_REDACT`, `MAIDA_REDACT_KEYS`, `MAIDA_MAX_FIELD_BYTES`)
2. `.maida/config.yaml` in project root
3. `~/.maida/config.yaml`

See the [configuration reference](reference/config.md) for the full list of env vars, YAML keys, and defaults.
