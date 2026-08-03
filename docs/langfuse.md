# Importing Langfuse traces

**Langfuse tells you what happened; Maida tells you whether it changed.** The
Langfuse importer turns traces that already exist in Langfuse into local Maida
runs, so they can be inspected, baselined, and gated without adding another
instrumentation path.

The production interface is API-only and read-only. Maida sends authenticated
`GET /api/public/v2/observations` requests, normalizes the returned
observations, validates the result against Maida's current trace contract, and
writes it only to local Maida storage. It does not modify Langfuse data or
upload the imported run to a hosted Maida service.

## Configure access

No optional package is required. Set the same credentials used by Langfuse's
SDKs:

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-...
export LANGFUSE_SECRET_KEY=sk-lf-...
```

Langfuse Cloud is the default. For a regional or self-hosted deployment, set
`LANGFUSE_BASE_URL` or pass `--base-url`:

```bash
export LANGFUSE_BASE_URL=https://us.cloud.langfuse.com
# Optional request timeout in seconds (default: 5)
export LANGFUSE_TIMEOUT=15
```

Credentials are read from the environment; the CLI has no credential flags and
never stores credentials in a run.

The v2 observations endpoint requires Langfuse Cloud or self-hosted Langfuse
v4+. For older self-hosted installations, use the ClickHouse mapping reference
below or upgrade Langfuse; Maida does not silently fall back to a deprecated API.

## Import traces

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
uses server-side filters, then fetches every observation for each matching
trace. All API pages are followed by cursor.

Use `--json` for a machine-readable summary. Exit code `0` means every selected
complete trace was imported or already existed; `2` means invalid selection,
no matches, or only incomplete traces; `10` means an API, normalization, or
storage failure.

Re-importing the same Langfuse project and trace is idempotent. Maida derives a
stable destination trace ID and skips an identical existing import. It refuses
to overwrite a conflicting run.

## Mapping contract

One Langfuse trace becomes one Maida run. Its `traceName` becomes the recurring
Maida `run_name`; Langfuse session IDs remain source metadata rather than
splitting or naming runs. Maida creates a synthetic root span so source traces
with multiple roots, subagents, or absent ancestors still form one valid tree.

| Langfuse observation | Maida representation |
|---|---|
| Trace | One run plus a synthetic root span |
| `GENERATION` | LLM span / `LLM_CALL`, with model, input, output, and token usage |
| `TOOL` | Tool span / `TOOL_CALL`, with name, arguments, result, and error state |
| `SPAN`, `AGENT`, `CHAIN`, `RETRIEVER`, `EVALUATOR`, `EMBEDDING`, `GUARDRAIL` | Preserved structural span |
| `EVENT` | Zero-duration structural span when no end time is present |
| Unknown type | Preserved structural span and reported in the import summary |
| Session | `maida.meta.langfuse.session_ids` on the run root |

Parent-child links are retained when the parent observation is present.
Runtime-worker and other subagent hierarchies therefore remain visible as
parented structural spans (the Maida trace equivalent of subthreads). Missing
parents attach to the synthetic root and are recorded without inventing
framework-specific event types.

Unmapped types: none for the observation types in Langfuse's current published
API. A future or extension type is retained as a structural span and named in
the import summary instead of being dropped or promoted to a new Maida event
type.

Input/output, source metadata, cost details, and usage detail fields are
redacted and truncated with the active Maida configuration before persistence.
The `input`/`output`/`total` token keys and legacy Langfuse token aliases map to
Maida's normalized token counters. Cache, reasoning, cost, release, tag, and
environment details remain under `maida.meta.langfuse`.

Completed source errors remain error spans and make the imported run's status
`error`. A non-event observation without `endTime` is treated as incomplete and
skipped; the importer never fabricates its completion. Repeated LLM/tool action
patterns are evaluated by Maida's normal loop detector and can add
`LOOP_WARNING` spans.

## Self-hosted ClickHouse mapping reference

Self-hosted operators may query Langfuse's ClickHouse storage directly for
audit or migration work. The CLI does not execute SQL and does not accept
export files; the supported production path remains the read-only API. A
direct query must reconstruct the same observation-shaped records before this
mapping boundary, including at least:

- observation `id`, `traceId`, `projectId`, `type`, `name`, and
  `parentObservationId`;
- timezone-aware `startTime` and `endTime`;
- `input`, `output`, `metadata`, `level`, and `statusMessage`;
- model fields, `usageDetails`, `costDetails`, trace name, session ID,
  environment, release, and tags.

Query every observation belonging to each selected trace; selecting only rows
whose timestamps fall inside a discovery window can cut off parents or late
children. Column names and storage layout are Langfuse deployment details, so
prefer the API whenever possible.

## Synthetic conformance data

The repository fixture in `tests/fixtures/langfuse/api-v2/` is fully synthetic.
It models production-shaped pagination, missing-parent handling, generations,
tools, token extras, and a deterministic regression without containing customer
or partner data. Tests import its good trace, capture a baseline, then confirm
that the regression trace fails for structural and token-usage changes.
