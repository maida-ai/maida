# Importing Langfuse traces

**Langfuse tells you what happened; Maida tells you whether it changed.** The
Langfuse importer turns traces that already exist in Langfuse into local Maida
runs, so they can be inspected, baselined, and gated without adding a second
instrumentation path.

The integration is API-only and read-only. Maida sends authenticated
`GET /api/public/v2/observations` requests, normalizes the observations,
validates the result against Maida's current trace contract, and writes only to
local Maida storage. It does not modify Langfuse data or upload the imported
run to a hosted Maida service.

Until the importer is included in the next PyPI release, install the current
`main` revision:

```bash
uv tool install "maida-ai>=0.5"
```

## Configure access

No optional package is required. Set the credentials used by the Langfuse SDK:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
```

Langfuse Cloud is the default. For a regional or self-hosted deployment, set
the base URL and, optionally, the request timeout:

```bash
export LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
# Optional request timeout in seconds (default: 5)
export LANGFUSE_TIMEOUT=15
```

Credentials are read from the environment. The CLI has no credential flags
and never stores credentials in a run. The v2 observations endpoint requires
Langfuse Cloud or self-hosted Langfuse v4+. For older self-hosted
installations, use the ClickHouse mapping reference below or upgrade
Langfuse; Maida does not silently fall back to a deprecated API.

## Import a trace

Import one complete source trace by its Langfuse trace ID:

```bash
maida import langfuse --trace-id 7f0d4a2c...
```

Or discover traces in a bounded, timezone-aware interval. `--from` is
inclusive and `--to` is exclusive:

```bash
maida import langfuse \
  --from 2026-08-01T00:00:00Z \
  --to 2026-08-02T00:00:00Z \
  --trace-name support-agent \
  --session-id session-42 \
  --environment production
```

`--trace-name`, `--session-id`, and repeatable `--environment` options narrow
range discovery. They cannot be combined with `--trace-id`. Range discovery
uses server-side filters and then fetches every observation for each selected
trace. All cursor pages are followed.

Use `--json` for a machine-readable summary. Exit code `0` means every selected
complete trace was imported or already existed; `2` means invalid selection,
no matches, or only incomplete traces; `10` means an API, normalization, or
storage failure.

Re-importing the same Langfuse project and trace is idempotent. Maida derives a
stable destination trace ID and skips an identical existing import. It refuses
to overwrite a conflicting run.

## Mapping contract

One Langfuse trace becomes one Maida run. Its `traceName` becomes the recurring
Maida `run_name`. Session IDs remain source metadata. Maida creates a synthetic
root span so source traces with multiple roots, subagents, or absent ancestors
still form one valid tree.

| Langfuse observation | Maida representation |
|---|---|
| Trace | One run plus a synthetic root span |
| `GENERATION` | LLM span / `LLM_CALL`, with model, input, output, and token usage |
| `TOOL` | Tool span / `TOOL_CALL`, with name, arguments, result, and error state |
| `SPAN`, `AGENT`, `CHAIN`, `RETRIEVER`, `EVALUATOR`, `EMBEDDING`, `GUARDRAIL` | Preserved structural span |
| `EVENT` | Zero-duration structural span when no end time is present |
| Unknown type | Preserved structural span and reported in the import summary |
| Session | `maida.meta.langfuse.session_ids` on the run root |

Parent-child links are retained when the parent observation is present, so
runtime-worker and other subagent hierarchies stay visible as parented
structural spans. Missing parents
attach to the synthetic root. Inputs, outputs, metadata, costs, and usage
details pass through Maida's active redaction and truncation before
persistence. Completed source errors remain errors; incomplete non-event
observations are skipped rather than given a fabricated completion.

## Legacy local single-trace compatibility

The current CI path is the Action `trace-command` workflow below. For local
migration workflows that already import one completed trace at a time, the
legacy single-run interface remains available:

```bash
maida view
maida baseline --out baselines/support-agent.json

# Import the next completed run, then gate the latest local run.
maida import langfuse --trace-id NEXT_TRACE_ID
maida assert \
  --baseline baselines/support-agent.json \
  --policy .maida/policy.yaml
```

The compatibility path uses the same structural evaluator for changed tool paths, new tools, repeated work,
loops, status changes, and configured latency or token envelopes.

## Run the importer in GitHub Actions

The Action accepts a trusted `trace-command` when the run comes from an
importer rather than a Python agent entrypoint. Keep credentials in GitHub
secrets and select exactly one completed source trace:

```yaml
- uses: maida-ai/maida-assert@v5
  env:
    LANGFUSE_PUBLIC_KEY: ${{ secrets.LANGFUSE_PUBLIC_KEY }}
    LANGFUSE_SECRET_KEY: ${{ secrets.LANGFUSE_SECRET_KEY }}
    LANGFUSE_TRACE_ID: ${{ vars.LANGFUSE_TRACE_ID }}
  with:
    trace-command: maida import langfuse --trace-id "$LANGFUSE_TRACE_ID"
    baseline: baselines/support-agent.json
    policy: .maida/policy.yaml
```

Imported traces use a fixed one-trial gate because a source trace is already a
completed observation, not a script Maida can rerun statistically. Do not pass
`--trials` in `extra-args`, select a range that creates several runs, or build
`trace-command` from pull-request-controlled text. Policies that need repeated
independent executions should use `agent-script` instead.

## Try it without an account

The [offline Langfuse import demo](https://github.com/maida-ai/maida-tutorials/tree/main/demos/langfuse_import)
runs the real importer against a loopback fake API. Its fully synthetic fixture
proves idempotent import, a baseline pass, and a deterministic structural
regression failure without a Langfuse account, API key, LLM, or external
network request.

## Privacy and failure behavior

- Requests are read-only and go only to the configured Langfuse origin.
- Imported data stays under the local `MAIDA_DATA_DIR` (by default
  `~/.maida/`); there is no default upload or telemetry path.
- Credentials remain environment-only and are not copied into trace metadata.
- Invalid selections and incomplete-only results exit `2` with an actionable
  message. Authentication, API, normalization, or storage failures exit `10`.
- A future Langfuse observation type is retained as structural signal and
  named in the summary instead of being silently dropped.
