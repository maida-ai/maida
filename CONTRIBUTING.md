# Contributing to Maida

Thanks for your interest in Maida.
This document covers dev setup, tests, lint/format, and how to add integrations.

---

## Versioning and compatibility

This is the source of truth for release-version policy across Maida repositories. The `maida-ai` engine sets the `MAJOR.MINOR` compatibility line. A releasable sibling component uses that same line when it has been tested against it, with its own independent `PATCH` number. For example, an Action `v0.5.2` and engine `v0.5.3` can belong to the same supported line. A sibling does not publish an empty release merely because the engine advanced; it adopts a new line when its support for that line is verified. Each release states its tested engine range and the functionality it supports. Matching numbers alone do not prove feature parity or compatibility. Changes to shared contracts are propagated and checked across repositories before claiming support. Trace, policy, and report schema versions are separate from package versions.

The `maida-ai` Python package uses immutable full Git tags such as `v0.5.3`. Git tags drive the version in `pyproject.toml`; do not edit a version string by hand. Publish the corresponding full version, without `v`, on PyPI. Do not create or move a shortened `vMAJOR.MINOR` tag in this repository. Use patch releases for compatible fixes and minor releases for compatible additions. While the package is at `0.x`, a minor release may also contain an incompatible change; document those changes explicitly. Use a PEP 440 `.postN` release only for a correction to packaging or release metadata that does not change runtime behavior. Runtime fixes get a new patch release.

The GitHub Action has its own versioning and tag policy in the [`maida-assert` README](https://github.com/maida-ai/maida-assert#versioning). Other repositories document their own release mechanism and how they verify Maida compatibility in their contributor docs or README.

---

## Dev setup

1. **Clone and install with uv (recommended):**

   ```bash
   git clone https://github.com/Maida-AI/maida.git
   cd maida
   uv venv && uv sync && uv pip install -e .
   ```

   This creates a venv, installs dependencies (including dev), and installs the package in editable mode.

2. **Optional dependencies (e.g. LangChain integration):**

   ```bash
   uv pip install -e ".[langchain]"
   ```

---

## Running tests

```bash
uv run pytest
```

Run a specific file or test:

```bash
uv run pytest tests/test_tracing.py
uv run pytest tests/test_tracing.py -k "test_trace_creates_run"
```

---

## Lint and format

- **Ruff** is used for linting and formatting. Dev dependencies include `ruff` and `pre-commit`.
- Format and lint:

  ```bash
  uv run ruff check .
  uv run ruff format .
  ```

- **Pre-commit:** If you use pre-commit, install hooks with `uv run pre-commit install`. The project does not require pre-commit for contributions; running ruff before pushing is sufficient.

---

## Adding integrations / adapters

Maida is **framework-agnostic** at the core. Integrations are optional adapters that map framework callbacks to Maida's recording API.

If you want to add or extend an integration (e.g. another framework):

1. **Optional dependencies only.** The integration must live under `maida.integrations.*` and depend on the framework via optional extras (e.g. `[langchain]` in `pyproject.toml`). The core package must not depend on the framework.
2. **Keep the core framework-agnostic.** All recording goes through the public API: `record_llm_call`, `record_tool_call`, `record_state`. The integration's job is to translate framework events into those calls.
3. **Deterministic tests, no network.** Tests for the integration should be deterministic and not perform real LLM or network calls. Use mocks or in-memory stubs.
4. **Map callbacks to record_*.** Implement the framework's callback/hook interface and call the appropriate `record_*` functions so that events attach to the current run (or an implicit run if `MAIDA_IMPLICIT_RUN=1`).
5. **Use `_MaidaAbortSignal` for guardrail propagation.** Every framework catches `Exception` in its callback/hook dispatcher. To actually stop execution when a guardrail fires, your adapter must escalate to `_MaidaAbortSignal(BaseException)`. See [maida/integrations/CONTRIBUTING.md](maida/integrations/CONTRIBUTING.md) for the full pattern and pitfalls.
6. **Meet the conformance contract.** Prove normalized lifecycle, LLM, tool, error, guardrail, terminal-state, payload-safety, and metadata behavior with deterministic offline tests. See the [adapter conformance contract](maida/integrations/CONTRIBUTING.md#adapter-conformance-contract).

New integrations should be documented in [docs/integrations.md](docs/integrations.md) with usage and install instructions. For the guardrail propagation patterns, error handling, and testing checklist, see the [integration adapter guide](maida/integrations/CONTRIBUTING.md).

---

## Example folders

Examples live under `examples/` and must stay runnable from the repo root:

- **`examples/minimal/`** – minimal pure-Python agent (no extra deps); run `python examples/minimal/simple_agent.py`.
- **`examples/langchain/minimal.py`** – minimal LangChain chain; requires `[langchain]` extra.
- **`examples/langchain/`** – advanced LangChain/LangGraph customer-support demo (`customer_support.py` + `_customer_support/`); requires installing the `examples/langchain` dependencies and API keys (see `_customer_support/README.md`).
- **`examples/demo/`** – short demo scripts (`pure_python.py`, `langchain.py`).
- **`examples/crewai/`** – CrewAI example stubs (no runnable script yet; see CHANGELOG for status).

When changing directory layout or run commands, update README, [docs/index.md](docs/index.md) (Demos section), and this list.

---

## Documentation

- **User docs** live in `docs/`: [getting started](docs/getting-started.md), [CLI](docs/cli.md), [SDK](docs/sdk.md), [integrations](docs/integrations.md), [architecture](docs/architecture.md).
- The guardrails feature has a dedicated page: [docs/guardrails.md](docs/guardrails.md). Because guardrails are a core user-facing wedge, keep this page, the README, and the config/reference docs aligned whenever behavior changes.
- **Reference docs** (public contracts) are in `docs/reference/`:
  - [Trace format](docs/reference/trace-format.md) - event schema, run.json, payloads.
  - [Configuration](docs/reference/config.md) - env vars, YAML precedence, redaction, loop detection, guardrails.

When you change behavior that affects the trace format or configuration, update the relevant reference doc and any linked pages. For guardrails specifically, check all of:

- `README.md`
- `docs/guardrails.md`
- `docs/sdk.md`
- `docs/reference/config.md`
- `docs/reference/trace-format.md`

---

## Summary

- **Setup:** `uv venv && uv sync && uv pip install -e .`
- **Tests:** `uv run pytest`
- **Lint/format:** `uv run ruff check .` and `uv run ruff format .`
- **Integrations:** Optional deps, map framework callbacks -> `record_*`, deterministic tests, document in `docs/integrations.md`.
