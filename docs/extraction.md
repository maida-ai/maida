# Extract reviewable gate drafts

`maida extract` derives inactive baseline and policy drafts from a validated
window of completed native traces. It groups runs by their exact `run_name` and
uses structural evidence only. The result is a starting point for human review,
not an active gate.

```bash
maida extract --window /srv/agent-export/runs --out ./maida-draft
```

The input must be a native directory ending in `runs/`. Every trace is validated
before extraction, running or malformed traces are rejected, and the source
files remain byte-for-byte unchanged.

## Select workflows

With no selector, every nonempty `run_name` group is extracted. Repeat
`--workflow` to select exact groups:

```bash
maida extract --window /srv/agent-export/runs --out ./maida-draft \
  --workflow orders-agent \
  --workflow billing-agent
```

Selectors are exact and must be unique. Missing, empty, or duplicate selections
exit without creating the output directory.

## Draft layout

Maida creates the output atomically and refuses to replace an existing path or
write inside the input window:

```text
maida-draft/
├── draft.json
└── workflows/<safe-name>-<stable-hash>/
    ├── baseline.json
    └── policy.yaml
```

The manifest begins with:

```yaml
draft_version: 1.0.0
review_required: true
```

`draft.json` records oldest-first trace IDs, structural-signature clusters and
their representatives, tool union and intersection, exact observed step bands,
tool/token ceilings, and terminal-state sets. A cluster includes only its
sanitized structural signature: tool and event order, tool counts, model names,
and terminal state.

The generated `baseline.json` contains the complete selected fixed-size sample.
The commented policy-v2 `policy.yaml` proposes only candidates supported by all
selected evidence:

- required tools when the intersection is nonempty;
- successful terminal state when every selected run ended `ok`;
- no-loop and no-guardrail invariants when every run satisfied them;
- the exact measured step band; and
- measured upper tool-call and token limits.

Maida reloads both generated files and runs the draft against the full selected
source window. The directory is installed only when every workflow returns
PASS. A failed self-consistency check leaves no partial draft behind.

## Privacy and activation

Extraction never copies prompts, responses, tool arguments, or tool results.
It also omits arbitrary source metadata, spans, and absolute paths. The command
is local and never writes to `.maida`, including active policy, baseline, and
run storage.

Review and edit each candidate with the workflow owner. After human review,
copy only the approved `policy.yaml` and `baseline.json` into the repository's
normal gate paths. There is no extraction path that activates a draft.

Use `--json` for a machine-readable manifest on stdout. Completion and review
notices stay on stderr:

```bash
maida extract --window /srv/agent-export/runs --out ./maida-draft --json
```

Exit `0` means the draft was verified and installed. Exit `2` means the input,
selection, or output path was invalid. Exit `10` means extraction, verification,
or atomic persistence failed unexpectedly; no partial output is retained.
