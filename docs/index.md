# Maida

**Maida** is a pre-merge behavioral regression gate for AI agents. It captures structured traces (LLM calls, tool calls, state, errors), turns known-good behavior into checked-in baselines, and blocks changes when policy says structural behavior regressed.

**What it is:** A local-first SDK and CLI for collecting behavioral evidence, comparing runs, and failing CI checks when agent behavior drifts beyond accepted thresholds. The current workflow is optimized for pre-merge CI, but the trace and policy primitives are the same foundation for broader reliability workflows.

**What it is not:** It is not a hosted telemetry product, a generic output eval platform, or a framework lock-in layer. The local viewer helps inspect evidence, but the core product is behavioral regression gating.

---

## In 60 seconds

**1. Install:**

```bash
pip install maida-ai
```

**2. Run the bundled demo agent** (simulated; no repo clone, no API keys):

```bash
maida demo
```

**3. Open the timeline viewer:**

```bash
maida view
```

A browser tab opens showing every event in the run - tool calls, LLM calls, timing. Data is stored locally under `~/.maida/runs/<trace_id>/`.

From there, capture a baseline with `maida baseline`, gate future runs with `maida assert`, and scaffold your own project with `maida init`. To watch the gate catch a regression end-to-end on canned data, run:

```bash
maida demo --regression
```

---

## Demos and examples

| Example | Path | How to run |
|--------|------|------------|
| **Minimal agent** (pure Python) | `examples/minimal/` | `python examples/minimal/simple_agent.py` |
| **LangChain minimal** | `examples/langchain/minimal.py` | `uv run --extra langchain python examples/langchain/minimal.py` |
| **OpenAI Agents minimal** | `examples/openai_agents/minimal.py` | `uv run --extra openai python examples/openai_agents/minimal.py` |
| **LangChain customer support** (advanced) | `examples/langchain/` | Set API keys, then follow `_customer_support/README.md` |
| **Demos** (short scripts) | `examples/demo/` | `python examples/demo/pure_python.py` or `python examples/demo/langchain.py` |

After any run, inspect evidence with `maida view`, capture baselines with `maida baseline`, and check regressions with `maida assert`.

---

## Documentation

| Page | Description |
|------|-------------|
| [Getting started](getting-started.md) | Installation (uv/pip), quickstart, data dir, redaction |
| [Guardrails](guardrails.md) | Stop runaway runs with loop, count, and duration limits |
| [Regression testing](regression-testing.md) | Baseline, assert, and diff workflow for catching agent regressions |
| [CLI](cli.md) | `demo`, `init`, `list`, `view`, `export`, `baseline`, `accept`, `assert`, `diff` with options and exit codes |
| [Viewer](viewer.md) | Timeline UI usage, URL params, live refresh, and development |
| [SDK](sdk.md) | `@trace`, `traced_run`, `has_active_run`, `record_llm_call`, `record_tool_call`, `record_state` |
| [Integrations](integrations.md) | LangChain handler, OpenAI Agents adapter, and planned adapters |
| [Architecture](architecture.md) | Event schema, storage layout, viewer API, loop detection |
| **Reference** | |
| [Trace format](reference/trace-format.md) | OTel span envelope, event projection, `meta.json`, and `spans.jsonl` public contract |
| [Configuration](reference/config.md) | Env vars, YAML precedence, redaction, truncation, loop detection, guardrails |
| [Policy YAML](reference/policy.md) | Assertion policy file format, fields, threshold semantics, CLI mapping |
