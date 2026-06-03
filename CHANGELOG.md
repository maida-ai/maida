# Changelog

## v0.3

### Highlights

- **Project renamed** - CLI changed from `agentdbg` to `maida`; repo changed from `RefineHQ-AI` to `maida-ai`.
- **Assertions** - `maida assert`, `maida baseline`, `maida diff` let you catch structural regressions before they merge.

## v0.2

### Highlights

- **Run guardrails** - `stop_on_loop`, `max_llm_calls`, `max_tool_calls`, `max_events`, and `max_duration_s` let you kill runaway agents mid-execution. Configurable via decorator args, env vars, or YAML.
- **Live-refresh viewer** - `agentdbg view` now stays running; the timeline UI polls for new runs and events automatically so you never need to manually refresh.
- **OpenAI Agents SDK integration** - thin adapter (`agentdbg.integrations.openai_agents`) maps SDK tracing hooks to AgentDbg events. Optional dependency.
- **CrewAI integration** - execution-hook adapter (`agentdbg.integrations.crewai`) for CrewAI workflows. Optional dependency.
- **Run summary panel** - the viewer shows per-run KPIs (call counts, duration, status) with jump-to-error and jump-to-loop shortcuts.
- **Jupyter tutorials** - three self-contained notebooks (LangChain, OpenAI Agents, Guardrails) that run without API keys.

### Known issues

- **No CrewAI example or tutorial** - `examples/crewai/` has no runnable `.py` script and there is no CrewAI tutorial notebook. The integration itself works and is tested, but end-user reference material is missing.
- **Thread-pool context propagation** - `contextvars` are not copied into worker threads. If tools execute concurrently via a thread pool, events may be lost or mis-ordered. Single-threaded agent loops are unaffected. Fix planned for v0.3.
- **`cwd()` project-root heuristic** - when the CLI is invoked from outside the project directory, the project-level config file may not be found. Workaround: run `agentdbg` from the project root or set config via env vars.

## v0.1

- **Core tracing API** - `@trace` decorator and `traced_run()` context manager to wrap agent code with zero framework coupling.
- **Event recording** - `record_llm_call()`, `record_tool_call()`, and `record_state()` for structured, append-only event capture.
- **Local storage** - JSONL + JSON files under `~/.agentdbg/runs/`; no cloud, no accounts.
- **Browser timeline viewer** - `agentdbg view` serves a vanilla-JS UI that renders the full event timeline for a run.
- **CLI** - `agentdbg list`, `agentdbg view`, and `agentdbg export` for run management.
- **Automatic secret redaction** - sensitive keys are scrubbed from payloads before they hit disk.
- **Loop detection** - repeated-event-sequence detector that emits `LOOP_WARNING` events.
- **LangChain / LangGraph integration** - callback handler that translates LangChain callbacks into AgentDbg events.
