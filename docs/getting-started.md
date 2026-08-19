# Getting started

## Installation

Requires Python 3.10 to 3.13 (`requires-python = ">=3.10,<3.14"`).

**With uv:**

```bash
uv tool install "maida-ai>=0.5"
```

**From source with uv:**

```bash
git clone https://github.com/maida-ai/maida.git
cd maida
uv sync
```

**From source with pip:**

```bash
git clone https://github.com/maida-ai/maida.git
cd maida
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

---

## Try it in 60 seconds

No repo clone, no config, no API keys:

```bash
uv tool install "maida-ai>=0.5"
maida demo        # trace a bundled simulated agent
maida view        # inspect the timeline in your browser
```

Then watch the gate catch a regression end-to-end -- baseline a good run, run a "refactored" agent that loops and calls a new tool, and see the failing report with a PR-comment preview:

```bash
maida demo --regression
```

When you're ready to wire up your own project:

```bash
maida init            # starter .maida/policy.yaml
maida init --github   # + GitHub Actions workflow
```

The generated workflow tracks `maida-ai/maida-assert@v5`, uses policy v2,
and grants the `checks: write` permission needed for the gate check.

Replace `MAIDA_AGENT_SCRIPT` with your traced entrypoint. Once you have
checked in a baseline, set `MAIDA_BASELINE` to its path. That enables
authorized maintainers to accept an intentional PR change with
`/maida accept [optional reason]`; the command stays inactive while the
baseline value is blank.

---

## Quickstart

**1. Decorate your entrypoint with `@trace`** so each invocation becomes a run. Maida stores the run as OTel-compatible spans and projects those spans into familiar `RUN_START`, `RUN_END`, `LLM_CALL`, `TOOL_CALL`, and `ERROR` event views for the viewer, baselines, assertions, and diffs.

**2. Call the recorders** inside that function so events attach to the current run:

```python
from maida import trace, record_llm_call, record_tool_call, record_state


@trace
def run_agent():
    record_tool_call(name="search_db", args={"query": "x"}, result={"count": 2})
    record_llm_call(
        model="gpt-4",
        prompt="Summarize",
        response="Done.",
        usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    )
    record_state(state={"step": 1}, meta={"label": "after_search"})


if __name__ == "__main__":
    run_agent()
```

**3. Run the script, then open the UI:**

```bash
python your_script.py
maida view
```

The viewer starts a local server (default `127.0.0.1:8712`) and opens the latest run in your browser. Use this trace as the evidence source for baselines, assertions, and diffs.

---

## Add guardrails during iteration

If you are iterating on an agent loop, add guardrails early so a bad prompt or tool policy does not spiral into dozens of repeated calls.

```python
from maida import trace


@trace(
    stop_on_loop=True,
    max_llm_calls=12,
    max_tool_calls=20,
    max_events=80,
    max_duration_s=30,
)
def run_agent(): ...
```

Useful defaults for local iteration:

- `stop_on_loop=True` for ReAct-style loops
- `max_llm_calls` when you want a token-budget ceiling
- `max_tool_calls` when tools are expensive or side-effectful
- `max_events` when you want a hard cap on trace size
- `max_duration_s` when the run should finish quickly

When a guardrail fires, Maida still writes the relevant trace evidence, then records `ERROR` and `RUN_END(status="error")` and re-raises a dedicated exception.

See [Guardrails](guardrails.md) for examples and [Configuration reference](reference/config.md) for env/YAML setup.

---

## Where data is stored

- **Default:** `~/.maida/runs/<trace_id>/`
  - `meta.json` - run metadata (`spec_version`, trace ID, status, counts, started_at, ended_at)
  - `spans.jsonl` - one OTel span record per line (append-only)

The CLI still uses the user-facing name `RUN_ID` in command arguments and JSON fields in a few places. Current runs are backed by OTel trace IDs, and short prefixes are resolved to the full trace ID.

---

## Overriding the data directory

Set the data directory so runs are stored somewhere else (e.g. project-local):

```bash
export MAIDA_DATA_DIR=/path/to/my/data
```

Config can also be set in `~/.maida/config.yaml` or `.maida/config.yaml` in the project root; environment variables take precedence. See the [configuration reference](reference/config.md) for the full list of options and precedence.

---

## Redaction (defaults and config)

- **Redaction is on by default.** Span attributes and projected event payloads are scanned for sensitive keys (e.g. `api_key`, `token`, `authorization`, `password`); matching values are replaced with `__REDACTED__`.
- **Large values** are truncated to a maximum size (default 20_000 bytes) and suffixed with `__TRUNCATED__`.

**Environment variables (override config files):**

| Variable | Default | Description |
|----------|---------|-------------|
| `MAIDA_REDACT` | `1` | `1`/`true`/`yes` to enable redaction |
| `MAIDA_REDACT_KEYS` | `api_key,token,authorization,cookie,secret,password` | Comma-separated keys (case-insensitive substring match) |
| `MAIDA_MAX_FIELD_BYTES` | `20000` | Max size for string/field before truncation |

Example: disable redaction (e.g. for trusted local inspection):

```bash
export MAIDA_REDACT=0
```

For full details (precedence, YAML keys, redaction/truncation behavior), see the [configuration reference](reference/config.md).
