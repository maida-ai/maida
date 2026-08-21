# `maida validate-trace`

Validates an externally emitted native Maida trace without installing or
modifying it.

```bash
maida validate-trace PATH [--json]
```

`PATH` is either a directory containing `meta.json` and `spans.jsonl`, or that
directory's `meta.json`. Text mode prints a concise success result to stdout or
actionable diagnostics to stderr. `--json` keeps stdout machine-readable for
both success and failure, with `valid`, trace metadata, span count, and
sanitized diagnostics.

**Exit codes:** `0` valid; `1` invalid trace content; `2` missing, unreadable,
or unsupported input path; `10` unexpected validator failure.

See [Emit Maida traces without an SDK](../reference/trace-emitter.md) for the
required fields, JSON Schemas, enrichment rules, and subthread topology.
