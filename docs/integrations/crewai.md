# CrewAI

**Status: available.** An optional adapter lives at `maida.integrations.crewai`. Importing it registers CrewAI execution hooks that automatically record LLM and tool calls into the active Maida run.

**Requirements:** `crewai[tools]` must be installed. Install Maida with the CrewAI extra:

```bash
uv add "maida-ai[crewai]>=0.5"
```

If `crewai` is not installed, importing the integration raises a clear `ImportError` with install instructions.

**Usage:**

```python
import maida
from maida.integrations import crewai as maida_crewai  # registers hooks


@maida.trace
def run_crew():
    # ... your CrewAI crew.kickoff() or flow.kickoff() ...
    pass
```

The adapter captures:

- **LLM calls** (`before_llm_call` / `after_llm_call`): records model, prompt messages, and response via `record_llm_call`.
- **Tool calls** (`before_tool_call` / `after_tool_call`): records tool name, args, result, and timing via `record_tool_call`.

Framework-specific context (agent role, task description, executor ID) is stored in `meta.crewai.*`.

The offline example — <a href="/docs/assets/examples/crewai-minimal.py" download>download it</a>, or run
[`examples/crewai/minimal.py`](https://github.com/maida-ai/maida/blob/main/examples/crewai/minimal.py)
from a checkout — sends fake data through CrewAI's public hook contexts, so it
exercises the adapter without starting a crew, LLM, or API call. The
environment flag disables CrewAI's separate anonymous package telemetry for
this deterministic run:

```bash
CREWAI_DISABLE_TELEMETRY=true python examples/crewai/minimal.py
maida view
```

The normal run has this structural signature:

- event sequence: `RUN_START -> LLM_CALL -> TOOL_CALL(search_docs) -> RUN_END`
- tool sequence: `search_docs` (one call)
- LLM calls: one `offline` call
- terminal status: `ok`

Capture that known-good behavior and confirm it passes the gate:

```bash
CREWAI_DISABLE_TELEMETRY=true python examples/crewai/minimal.py
maida baseline --out crewai-baseline.json
maida assert --baseline crewai-baseline.json
```

Then use the deterministic regression mode and run a strict tool-call check:

```bash
CREWAI_DISABLE_TELEMETRY=true python examples/crewai/minimal.py --regression
maida assert --baseline crewai-baseline.json --tool-call-tolerance 0
```

Regression mode records three consecutive `search_docs` calls, producing
`RUN_START -> LLM_CALL -> TOOL_CALL -> TOOL_CALL -> TOOL_CALL -> LOOP_WARNING -> RUN_END`.
The run itself still ends `ok`, but the assertion reports the tool-call
increase and exits with code `1` — so the gate catches the structural
regression even though the agent completed successfully.

When a guardrail fires in a hook, the adapter stores the public
`GuardrailExceeded`, raises an internal `BaseException` signal past CrewAI's
`except Exception` handling, and lets the Maida boundary record `ERROR` plus
`RUN_END(status="error")`. As a defensive fallback inside an active run, call
`maida_crewai.raise_if_aborted()` after framework execution.

For a full multi-agent workflow, an incomplete-hook failure, and a guarded-loop walkthrough, continue with the [full CrewAI tutorial](https://github.com/maida-ai/maida-tutorials/blob/main/CrewAI/Mock%20CrewAI%20Agent.ipynb).

**Notes:**

- The adapter requires an active Maida run — wrap your entrypoint with `@trace` or `traced_run(...)`.
- Hook ordering caveat: if another before-hook returns `False` and blocks execution, that specific call may not be captured.
- CrewAI's current hooks do not expose token usage, so CrewAI `LLM_CALL` events record `usage` as unknown.
- If a run ends before an after-hook arrives, the pending call is recorded with `status="error"` and `completion="missing_after_hook"` in its CrewAI metadata.
- The fake-hook-only example unregisters CrewAI's event-bus exit callback to avoid a current one-shot interpreter-shutdown hang. That cleanup is specific to the example and should not be copied into a long-lived Crew or Flow application.
