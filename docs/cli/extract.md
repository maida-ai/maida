# `maida extract`

Derives inactive, review-required gate drafts from a completed native trace
window without changing the source window or active `.maida` storage.

```bash
maida extract --window RUNS_DIR --out DRAFT_DIR [options]
```

| Argument/Option | Default | Description |
|---|---|---|
| `--window` | required | Native Maida `runs/` directory containing completed traces |
| `--out` | required | New directory for the atomic draft output |
| `--workflow` | every nonempty `run_name` | Exact workflow group; repeat for multiple groups |
| `--json` | `false` | Print `draft.json` content to stdout; notices remain on stderr |

The output contains `draft.json` plus one commented policy-v2 and immutable
baseline pair per workflow. It remains inactive until human review and an
intentional copy into the repository's gate paths. Exit `0` means extraction and
self-consistency verification succeeded, `2` means invalid input or selection,
and `10` means extraction or persistence failed without installing partial
output.

See [Extract reviewable gate drafts](../extraction.md) for grouping, privacy,
artifact layout, policy candidates, and review guidance.
