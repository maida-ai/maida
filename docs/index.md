# Maida documentation

> **This directory is the single source of truth for Maida's documentation.**
> The published version lives at **[maida.ai/docs](https://maida.ai/docs/)**,
> which builds these files at the pinned engine release. Edit them here, in the
> same pull request as the behavior they describe -- never in the website repo.
>
> This page is the only exception: `maida-ai.github.io` keeps its own docs
> landing page, because that one is presentation rather than content.

**Maida** is the pre-merge behavioral regression gate for AI agents. It captures
structured traces (LLM calls, tool calls, state, errors), turns known-good
behavior into checked-in baselines, and blocks changes when policy says
structural behavior regressed.

**What it is:** a local-first SDK and CLI for collecting behavioral evidence,
comparing runs, and failing CI checks when agent behavior drifts beyond accepted
thresholds.

**What it is not:** a hosted telemetry product, a generic output eval platform,
or a framework lock-in layer. The local viewer helps inspect evidence, but the
core product is behavioral regression gating.

---

## In 60 seconds

```bash
pip install maida-ai     # or: uv tool install "maida-ai>=0.5"
maida demo               # traced run of a bundled simulated agent
maida view               # open the timeline at 127.0.0.1:8712
maida demo --regression  # watch the gate catch a bad refactor
```

Runs are stored locally under `~/.maida/runs/<trace_id>/`. Nothing leaves your
machine.

---

## Start here

| Page | What it covers |
|---|---|
| [Getting started](getting-started.md) | Install, first trace, first baseline, redaction |
| [Regression testing](regression-testing.md) | The end-to-end baseline -> policy -> gate workflow |
| [Guides index](guides/index.md) | All task-oriented walkthroughs |

## Guides

| Page | What it covers |
|---|---|
| [Regression testing](regression-testing.md) | Policy-v2 baseline sampling and the candidate gate |
| [Guardrails](guardrails.md) | Stop runaway runs with loop, count, and duration limits |
| [Viewer](viewer.md) | Timeline UI usage, URL params, live refresh |
| [Capture Claude Code](claude-code.md) | OTLP capture, import, and pinned scenarios |
| [Scheduled checks](scheduled-checks.md) | Batch verdicts over completed trace windows |
| [Gate draft extraction](extraction.md) | Derive policy and baseline drafts for human review |

## Integrations

| Page | What it covers |
|---|---|
| [Overview](integrations.md) | How adapters work and what they guarantee |
| [LangChain / LangGraph](integrations/langchain-langgraph.md) | Callback handler |
| [OpenAI Agents SDK](integrations/openai-agents.md) | Tracing adapter |
| [CrewAI](integrations/crewai.md) | Execution-hook adapter |
| [Langfuse import](langfuse.md) | Import completed Langfuse traces and gate them |

## Reference

| Page | What it covers |
|---|---|
| [CLI](cli.md) | Every command, option, output shape, and exit code |
| [SDK](sdk.md) | `@trace`, `traced_run`, and the event recorders |
| [Policy](reference/policy.md) | `.maida/policy.yaml` format and policy v2 semantics |
| [Trace format](reference/trace-format.md) | The versioned public data contract |
| [External emitter guide](reference/trace-emitter.md) | Emit native traces without an SDK |
| [Configuration](reference/config.md) | Env vars, YAML precedence, redaction, truncation |
| [Architecture](architecture.md) | Span schema, storage, viewer API, loop detection |

---

## Demos and examples

| Example | Path | How to run |
|--------|------|------------|
| **Minimal agent** (pure Python) | `examples/minimal/` | `python examples/minimal/simple_agent.py` |
| **LangChain minimal** | `examples/langchain/minimal.py` | `uv run --extra langchain python examples/langchain/minimal.py` |
| **OpenAI Agents minimal** | `examples/openai_agents/minimal.py` | `uv run --extra openai python examples/openai_agents/minimal.py` |
| **LangChain customer support** (advanced) | `examples/langchain/` | Set API keys, then follow `_customer_support/README.md` |
| **Demos** (short scripts) | `examples/demo/` | `python examples/demo/pure_python.py` |

Step-by-step notebooks live in
[maida-ai/maida-tutorials](https://github.com/maida-ai/maida-tutorials).

---

## Engine-only pages

These are not published to maida.ai -- they are working documents for this
repository:

- [Calibration table (issue #187)](calibration-187.md) -- a seeded offline
  measurement used to pick policy thresholds.
