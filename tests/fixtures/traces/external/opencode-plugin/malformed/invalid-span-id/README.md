# Invalid Span ID Fixture

Expected behavior:

- `meta.json` is valid current metadata.
- `spans.jsonl` contains a span whose `span_id` is not a 16-character hex
  string.
- `loadValidatedRun` should reject this fixture with a clear span validation
  error.
