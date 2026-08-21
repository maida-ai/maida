# `maida view`

Starts the local viewer server and optionally opens the browser. Default bind: `127.0.0.1:8712`.

**Usage:**

```bash
maida view [TRACE_ID] [--host HOST] [--port PORT] [--no-browser] [--json]
```

**Arguments / options:**

| Argument/Option | Default | Description |
|-----------------|---------|-------------|
| `TRACE_ID` | (latest) | Run to view; can be a full 32-hex-character OTel trace ID or a prefix |
| `--host`, `-H` | 127.0.0.1 | Bind host |
| `--port`, `-p` | 8712 | Bind port |
| `--no-browser` | - | Do not open the browser; only start the server |
| `--json` | - | Print the selected trace ID in the `run_id` compatibility field, url, and status as JSON, then start server |

**Examples:**

```bash
maida view
maida view a1b2c3d4
maida view --port 9000 --no-browser
maida view --json
```

**Exit codes:** `0` success; `2` run not found (or no runs); `10` internal error.

With `--json`, output shape: `{"spec_version":"0.2.0","run_id":"...","url":"http://127.0.0.1:8712/?run_id=...","status":"serving"}`.
