# `maida import langfuse`

Imports existing Langfuse traces through the read-only v2 observations API and
stores validated Maida runs locally. One Langfuse trace becomes one Maida run.

**Usage:**

```bash
maida import langfuse --trace-id TRACE_ID [--base-url URL] [--json]
maida import langfuse --from TIME --to TIME [FILTERS] [--json]
```

**Selection options:**

| Option | Description |
|---|---|
| `--trace-id` | Import one complete Langfuse trace; mutually exclusive with range options |
| `--from` | Inclusive, timezone-aware start of range discovery |
| `--to` | Exclusive, timezone-aware end of range discovery |
| `--trace-name` | Restrict range discovery to one recurring trace name |
| `--session-id` | Restrict range discovery to one Langfuse session |
| `--environment` | Restrict discovery to an environment; repeat for multiple values |
| `--base-url` | Override `LANGFUSE_BASE_URL` for cloud region or self-hosting |
| `--json` | Print a machine-readable import summary |

Set `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY` before running the command.
`LANGFUSE_TIMEOUT` optionally controls the request timeout. The command performs
only `GET /api/public/v2/observations` requests and writes only to local Maida
storage.

**Examples:**

```bash
maida import langfuse --trace-id 7f0d4a2c...
maida import langfuse --from 2026-08-01T00:00:00Z --to 2026-08-02T00:00:00Z --trace-name support-agent
```

**Exit codes:** `0` imported or already present; `2` invalid selection, no
matches, or only incomplete traces; `10` API, normalization, or storage failure.

See [Importing Langfuse traces](../langfuse.md) for the mapping, pagination,
redaction, idempotence, and self-hosted ClickHouse reference.
