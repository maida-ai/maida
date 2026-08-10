# Changelog

## Unreleased

## v0.5.0

First PyPI release of the synchronized cross-repository contract. Install with
`uv tool install "maida-ai>=0.5"`; the GitHub Action moves to
`maida-ai/maida-assert@v5`.

### Highlights

- **Statistical gate (`maida run`)** - the primary gate command. Runs an agent over multiple isolated trials, aggregates them with a Wilson-bound three-verdict rule (pass / fail / inconclusive), and publishes tri-state statistical gate reports (`report_version` `2.0.0`).
- **Policy v2** - tiered, directional policy with two-tier acceptance semantics. Individual assertions can be disabled outright, and unreachable policy configurations are rejected at load time.
- **`maida accept`** - explicit baseline acceptance that records provenance, plus the scaffolded `/maida accept` PR command for authorized maintainers.
- **External trace contract** - versioned native trace schemas, an emitter guide, and `maida validate-trace` let systems emit and validate Maida traces without an SDK, with cross-repo conformance vectors.
- **Claude Code capture** - `maida capture claude-code` (OTLP receiver) and `maida capture claude-hook` (passive hook), `maida import claude-code` to normalize captures into Maida traces, and `maida scenario run` for isolated scenarios.
- **Langfuse import** - `maida import langfuse` normalizes Langfuse observations into local runs so you can gate traces you already collect.
- **`maida drift`** - windowed drift evaluation over completed trace windows, for scheduled (non-PR) checks.
- **`maida extract`** - derives inactive policy and baseline drafts from real trace windows for human review.
- **Adapter conformance** - LangChain, OpenAI Agents, and CrewAI adapters verified against a shared conformance contract, including payload-privacy protections.

### Upgrading

- **Install from PyPI.** Replace the `git+https://github.com/maida-ai/maida.git@main` requirement with `maida-ai>=0.5`.
- **Repin the Action.** Workflows tracking `maida-ai/maida-assert@main` should move to `maida-ai/maida-assert@v5`, and the `accept-command` and `write-back` sub-actions with it. The Action now defaults `maida-version` to `v0.5.0` instead of the development channel.
- **`maida run` is the primary gate.** `maida assert` remains supported for single-run evaluation; new workflows should call `maida run`.
- **Policy files must declare `version: 2`.** Unreachable metric configurations are now rejected when the policy loads rather than silently passing.

### Contract

| Surface | Version |
|---------|---------|
| Trace schema | `0.2.0` |
| Baseline schema | `0.3.0` |
| Policy schema | `2` |
| Report schema | `2.0.0` |

## v0.4

### Highlights

- **`maida demo`** - bundled simulated agent: `pip install maida-ai && maida demo` produces a traced run with no repo clone, no network, and no API keys.
- **`maida demo --regression`** - the full gate story in one command: baseline a known-good run, run a "refactored" agent that loops, calls a new tool, and burns more tokens, then show the failing report with a PR-comment preview.
- **`maida init`** - scaffolds a commented starter `.maida/policy.yaml`, and with `--github` a ready-to-edit gate workflow.
- **Latest-run defaults** - `maida assert`, `maida baseline`, `maida export`, and `maida diff` no longer require a run ID; they default to the most recent run (announced on stderr so stdout stays machine-readable).
- **Richer gate reports** - the markdown report (the PR comment) now leads with a verdict, lists failed checks first with expected vs actual values, collapses passing checks, embeds a "What changed vs baseline" structural diff, and ends with a local-repro snippet. The text report appends the same diff on failure.
- **OTel storage contract** - run lifecycle, storage APIs, CLI, server, and viewer moved onto trace-ID/`meta.json`/`spans.jsonl` storage, with JSON schemas updated to match.

## v0.3

### Highlights

- **Project renamed** - CLI changed from `agentdbg` to `maida`; repo changed from `RefineHQ-AI` to `maida-ai`.
- **Assertions** - `maida assert`, `maida baseline`, `maida diff` let you catch structural regressions before they merge.
- **OpenTelemetry** - Internal telemetry migrated from custom JSONL to OpenTelemetry spans. Trace format spec bumped to `"0.2"`. Local storage uses `spans.jsonl` + `meta.json` per OTel trace ID. Optional OTLP export via `OTEL_EXPORTER_OTLP_ENDPOINT`.

## v0.2

### Highlights

- **Run guardrails** - `stop_on_loop`, `max_llm_calls`, `max_tool_calls`, `max_events`, and `max_duration_s` let you kill runaway agents mid-execution. Configurable via decorator args, env vars, or YAML.
- **Live-refresh viewer** - `agentdbg view` now stays running; the timeline UI polls for new runs and events automatically so you never need to manually refresh.
- **OpenAI Agents SDK integration** - thin adapter (`agentdbg.integrations.openai_agents`) maps SDK tracing hooks to AgentDbg events. Optional dependency.
- **CrewAI integration** - execution-hook adapter (`agentdbg.integrations.crewai`) for CrewAI workflows. Optional dependency.
- **Run summary panel** - the viewer shows per-run KPIs (call counts, duration, status) with jump-to-error and jump-to-loop shortcuts.
- **Jupyter tutorials** - three self-contained notebooks (LangChain, OpenAI Agents, Guardrails) that run without API keys.

### Known issues

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
