# `maida export`

Exports one run to a single JSON file (run metadata + events array).

**Usage:**

```bash
maida export [TRACE_ID] --out FILE
```

**Arguments / options:**

| Argument/Option | Description |
|---|---|
| `TRACE_ID` | Run to export; can be a full 32-hex-character OTel trace ID or a prefix. Defaults to the latest run when omitted |
| `--out`, `-o` | Output file path (JSON) |

**Examples:**

```bash
maida export --out run-export.json   # latest run
maida export a1b2c3d4 -o ./exports/run-export.json
```

**Exit codes:** `0` success; `2` run not found; `10` internal error.

Output file contains: `spec_version`, `run` (run metadata), `events` (array of event objects).
