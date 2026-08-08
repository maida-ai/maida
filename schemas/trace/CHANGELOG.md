# Maida trace schema changelog

Versioned schema directories are immutable once published. The unversioned
`schemas/run.schema.json` and `schemas/event.schema.json` files are stable
aliases for the current metadata and span schemas.

## 0.2.0

- Published the native `meta.json` and `spans.jsonl` record shapes as
  Draft 2020-12 JSON Schemas.
- Declared the OpenTelemetry trace/span ID, lifecycle, count, span envelope,
  and in-span event fields used by Maida readers.
- Kept additive top-level fields and namespaced attributes available for
  compatible emitter enrichment.
- Documented `0.2` as a legacy spelling accepted by readers. New emitters must
  write the full `0.2.0` version.
