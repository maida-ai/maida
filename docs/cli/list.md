# `maida list`

Lists recent runs (by `started_at` descending).

**Usage:**

```bash
maida list [--limit N] [--json]
```

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--limit`, `-n` | 20 | Maximum number of runs to list |
| `--json` | - | Output machine-readable JSON |

**Examples:**

```bash
maida list
maida list --limit 5
maida list --json
```

**Exit codes:** `0` success; `10` internal error.

**Text columns:** trace_id (short; displayed in the compatibility `run_id` column), run_name, started_at, duration_ms, llm_calls, tool_calls, status.
