# invalid-spans

Intentionally malformed fixture for validator conformance. `meta.json` is a
valid current `spec_version: "0.2"` metadata file, but `spans.jsonl` contains
an invalid JSONL row and must fail validation with a malformed-JSON error.
