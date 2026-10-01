# Contributing to Maida

Thanks for your interest in Maida. This document covers dev setup, tests, lint/format, integrations, shared contracts, and release preparation.

---

## Versioning and compatibility

This is the source of truth for release-version policy across Maida repositories. The `maida-ai` engine sets the `MAJOR.MINOR` compatibility line. A releasable sibling component uses that same line when it has been tested against it, with its own independent `PATCH` number. For example, an Action `v0.5.2` and engine `v0.5.3` can belong to the same supported line. A sibling does not publish an empty release merely because the engine advanced; it adopts a new line when its support for that line is verified. Each release states its tested engine range and the functionality it supports. Matching numbers alone do not prove feature parity or compatibility. Changes to shared contracts are propagated and checked across repositories before claiming support. Trace, policy, and report schema versions are separate from package versions.

The `maida-ai` Python package uses immutable full Git tags such as `v0.6.1`. Git tags drive the dynamic package version through `uv-dynamic-versioning`; do not edit `maida/_version.py` or add a static version to `pyproject.toml`. Publish the corresponding full version, without `v`, on PyPI. Do not create or move a shortened `vMAJOR.MINOR` tag in this repository. Use patch releases for compatible fixes and minor releases for compatible additions. While the package is at `0.x`, a minor release may also contain an incompatible change; document those changes explicitly. Use a PEP 440 `.postN` release only for a correction to packaging or release metadata that does not change runtime behavior. Runtime fixes get a new patch release. The source-archive workflow currently accepts only stable and `rcN` tags; a `.postN` PyPI correction needs separate release handling.

The GitHub Action has its own versioning and tag policy in the [`maida-assert` README](https://github.com/maida-ai/maida-assert#versioning). Other repositories document their own release mechanism and how they verify Maida compatibility in their contributor docs or README.

### Preparing an engine release

For a stable release, use a full PEP 440 tag such as `v0.6.1` and an explicit install pin such as `maida-ai==0.6.1`. Before publishing:

1. Move the shipped `CHANGELOG.md` entries from **Unreleased** into the release section. Review compatibility changes, supported Python versions, and migration steps, including changes to command defaults and capture setup.
2. Update current install examples and the required engine release in `contracts/current-main.json`. Move shipped commands from `contracts/unreleased-cli.json` into the released command lists, clear their pending entries, and update `based_on` to the new engine tag. Keep the independently versioned schemas aligned with `maida/schema_versions.py`; a package release alone does not bump schema versions. Historical compatibility pins remain historical.
3. Propagate affected consumer snapshots and run the [cross-repository checks](#shared-contracts-and-cross-repository-changes). Keep the independently versioned Action reference on its verified release; an engine patch does not require a matching Action patch.
4. Run the full local checks:

   ```bash
   uv sync --locked --all-extras --dev
   uv lock --check
   uv run --locked --all-extras pytest --cov=maida --cov-report=term-missing
   uv run --locked ruff check .
   uv run --locked ruff format --check .
   ```

Verify the supported Python matrix from `pyproject.toml` and `.github/workflows/unittest-fast.yml` (currently 3.12, 3.13, and 3.14). The fast workflow excludes `slow`, `long`, and `longrunning` tests; the release workflow runs the full suite on Python 3.12. A fast-CI pass alone does not replace the full release checks.

Build with `uv build` (source distribution and wheel rebuilt from it), then inspect the wheel's version, Python requirement, extras, CLI entrypoint, bundled viewer and schemas, and rewritten README links in `*.dist-info/METADATA`. The packaging test below exercises the sdist-to-wheel path, bundled files, and README rewriting; the release tests exercise archive checksums, tag validation, and draft creation locally without publishing:

```bash
uv run --locked --all-extras pytest tests/test_packaging.py tests/test_readme_links_rewrite.py tests/test_release_workflow.py
```

Smoke-test the installed wheel from outside the checkout with isolated `MAIDA_DATA_DIR` storage so imports come from the installed artifact. `maida demo --regression` must produce the intended FAIL report and exit `0` for a completed rehearsal; an actual failed gate exits `1`. Gate exit `0` can also mean INCONCLUSIVE; consumers must read the report verdict rather than infer PASS from the exit code.

Publish only from the reviewed, committed release tree whose immutable full tag resolves to that commit. A successful build from a dirty checkout can contain changes absent from its version tag, so it is verification evidence rather than a publishable release artifact. Do not move an existing release tag as part of routine preparation. Consumer snapshot propagation and Action compatibility need their own verification before claiming cross-repository support.

For a coordinated engine and Action release, prepare both release trees first. Publish the engine package to PyPI before regenerating the Action's lockfile against that version; the engine's Action reference is documentation and contract metadata, not an install dependency. Then test and release the Action, and verify the generated workflow against its published tag. Keep the interval between publications short, and do not use the new Action reference in a required consumer check until that tag exists.

Pushing a full stable or `rcN` tag runs `.github/workflows/release.yml`: it tests the source, archives the exact event commit, signs and verifies build provenance, and creates a draft GitHub release with `maida.tar.gz`, `SHA256SUMS`, and `provenance.jsonl`. Review the draft's tag, notes, assets, and provenance before manually publishing it; immutable release protection takes effect on publication. Release candidates retain the prerelease classification. This workflow creates a source-archive release; building and publishing Python distributions to PyPI is a separate step.

---

## Dev setup

Supported Python versions are 3.12–3.14; `.python-version` selects 3.14 for local development. `uv sync` creates `.venv`, installs the default dev dependency group, and installs Maida in editable mode. No separate `uv venv` or editable install is needed.

1. **Clone and install with uv:**

   ```bash
   git clone https://github.com/maida-ai/maida.git
   cd maida
   uv sync --locked
   ```

2. **Enable an optional integration or all supported extras:**

   ```bash
   uv sync --locked --extra langchain
   # Or match the test environment used by CI:
   uv sync --locked --all-extras --dev
   ```

   The current extras are `langchain` and `openai`. Use the same `--extra` or `--all-extras` flag on subsequent `uv run` commands so synchronization keeps the framework dependencies installed. CrewAI support is paused after v0.5.3: its adapter remains in-tree, but there is no current `crewai` extra. See the [historical integration instructions](docs/integrations/crewai.md).

Work on a focused feature branch and keep commits reviewable. If dependencies change, update `pyproject.toml` and `uv.lock` together; otherwise keep the committed lockfile unchanged.

---

## Running tests

```bash
uv run pytest
```

Run a specific file or test:

```bash
uv run pytest tests/tracing/test_tracing.py
uv run pytest tests/tracing/test_tracing.py -k "test_trace_success"
```

Integration coverage requires the supported extras. To match fast CI:

```bash
uv run --locked --all-extras --all-groups pytest --config-file=pytest.toml -m "not slow and not long and not longrunning"
```

Run the full suite with coverage before a release or a change to shared behavior:

```bash
uv run --locked --all-extras pytest --cov=maida --cov-report=term-missing
```

`pytest.toml` enables strict markers and parallel execution with `-n auto`. Add `-n 0` for a serial debugging run, or `-n 2` to limit workers on a smaller machine. Preserve coverage and include success, regression, boundary, and error cases for behavior changes. Tests must isolate storage with temporary directories and patched HOME or `MAIDA_DATA_DIR`; use deterministic fixtures and fake framework/provider calls rather than live services or API keys.

---

## Lint and format

- **Ruff** is used for linting and formatting. Dev dependencies include `ruff` and `pre-commit`.
- Format and lint:

  ```bash
  uv run ruff check .
  uv run ruff format --check .
  # Apply formatting when needed:
  uv run ruff format .
  ```

- **Pre-commit:** Install the optional hooks with `uv run pre-commit install`, or run them manually with `uv run pre-commit run --all-files`. In addition to Ruff, the hooks check whitespace, final newlines, YAML, and large files. Tests and both Ruff checks remain required even if you do not install hooks.

Ruff configuration lives in `ruff.toml`. No type-check command is currently configured.

---

## Adding integrations / adapters

Maida is **framework-agnostic** at the core. Integrations are optional adapters that map framework callbacks to Maida's recording API.

If you want to add or extend an integration (e.g. another framework):

1. **Optional dependencies only.** The integration must live under `maida.integrations.*` and depend on the framework via optional extras (e.g. `[langchain]` in `pyproject.toml`). The core package must not depend on the framework.
2. **Keep the core framework-agnostic.** All recording goes through the public API: `record_llm_call`, `record_tool_call`, `record_state`. The integration's job is to translate framework events into those calls.
3. **Deterministic tests, no network.** Tests for the integration should be deterministic and not perform real LLM or network calls. Use mocks or in-memory stubs.
4. **Map callbacks to record_*.** Implement the framework's callback/hook interface and call the appropriate `record_*` functions so that events attach to the current run (or an implicit run if `MAIDA_IMPLICIT_RUN=1`).
5. **Preserve guardrail propagation.** If the framework catches `Exception` in its callback/hook dispatcher, escalate guardrail failures to `_MaidaAbortSignal(BaseException)` so they stop execution. See [maida/integrations/CONTRIBUTING.md](maida/integrations/CONTRIBUTING.md) for the full pattern and pitfalls.
6. **Meet the conformance contract.** Prove normalized lifecycle, LLM, tool, error, guardrail, terminal-state, payload-safety, and metadata behavior with deterministic offline tests. See the [adapter conformance contract](maida/integrations/CONTRIBUTING.md#adapter-conformance-contract).

New integrations should be documented in [docs/integrations.md](docs/integrations.md) with usage and install instructions. For the guardrail propagation patterns, error handling, and testing checklist, see the [integration adapter guide](maida/integrations/CONTRIBUTING.md).

---

## Shared contracts and cross-repository changes

The engine owns [contracts/current-main.json](contracts/current-main.json), [behavioral conformance vectors](contracts/conformance), and the public [schemas](schemas). See [contracts/README.md](contracts/README.md) for ownership and release semantics. Consumer copies under `tests/contracts/` are vendored test inputs, not independently maintained contracts. Add development-only commands to `contracts/unreleased-cli.json` until their engine release is prepared.

When changing CLI commands, schema compatibility, reports, or shared behavior, update the implementation, contract, reference docs, and affected consumer snapshots together. Run the engine's contract checks:

```bash
uv run --locked --all-extras pytest tests/test_cross_repo_contracts.py tests/test_plan_contract.py tests/test_published_trace_schemas.py tests/test_docs.py
```

With sibling repositories checked out beside `maida`, run:

```bash
uv run --no-project python scripts/check_cross_repo_sync.py --workspace ..
```

This compares the engine snapshot with `maida-assert`, `maida-ts`, `maida-ai.github.io`, `maida-tutorials`, and `maida-workflows`, the Python conformance vectors with their TypeScript copies, and TypeScript trace fixtures with their Python copies. Run the affected siblings' own checks as well; byte equality alone does not verify behavior. The scheduled/manual `.github/workflows/cross-repo-sync.yml` compares sibling `main` branches, so a local pass does not confirm the remote workflow passed.

---

## Example folders

Runnable examples and their workflow tests live in [maida-tutorials/examples](https://github.com/maida-ai/maida-tutorials/tree/main/examples), alongside the gradual tutorials and demo scenarios. The engine repository owns library behavior and deterministic adapter conformance tests; the tutorial repository owns example dependency setup and the learner's complete success/failure workflow. The installed offline `maida demo --regression` remains part of the engine package.

When changing an adapter or public command, update the relevant tutorial and rerun its workflow checks. Start with the [examples catalog](https://github.com/maida-ai/maida-tutorials/blob/main/examples/README.md); optional integrations and historical CrewAI examples have explicit environment requirements there.

---

## Documentation

- **User docs** live in `docs/`: [getting started](docs/getting-started.md), [CLI](docs/cli.md), [SDK](docs/sdk.md), [integrations](docs/integrations.md), [architecture](docs/architecture.md).
- The guardrails feature has a dedicated page: [docs/guardrails.md](docs/guardrails.md). Keep this page, the README, and the config/reference docs aligned whenever guardrail behavior changes.
- **Reference docs** (public contracts) are in `docs/reference/`:
  - [Trace format](docs/reference/trace-format.md) - span schema, meta.json, spans.jsonl, and payloads.
  - [Configuration](docs/reference/config.md) - env vars, YAML precedence, redaction, loop detection, guardrails.
  - [Policy and schema compatibility](docs/reference/policy.md) - metric kinds, verdicts, thresholds, and independently versioned schema streams.

Write each Markdown prose paragraph on one source line and rely on editor soft wrapping. Preserve meaningful line breaks in lists, tables, blockquotes, and fenced code. Website documentation is synced from its source repositories; update engine-owned pages here and use the website's sync workflow rather than maintaining a separate copy.

When you change behavior that affects the trace format or configuration, update the relevant reference doc and any linked pages. For guardrails specifically, check all of:

- `README.md`
- `docs/guardrails.md`
- `docs/sdk.md`
- `docs/reference/config.md`
- `docs/reference/trace-format.md`

---

## Summary

- **Setup:** `uv sync --locked` (or `uv sync --locked --all-extras --dev` for adapter coverage).
- **Tests:** `uv run pytest`; full adapter and coverage checks use `uv run --locked --all-extras pytest --cov=maida --cov-report=term-missing`.
- **Lint/format:** `uv run ruff check .` and `uv run ruff format --check .`; apply formatting with `uv run ruff format .`.
- **Integrations:** Optional deps, map framework callbacks -> `record_*`, deterministic tests, document in `docs/integrations.md`.
