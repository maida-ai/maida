# `maida accept`

Updates an existing baseline from a completed run when a behavior change is intentional. Use it after inspecting `maida diff` and `maida view`; do not accept a regression just to make CI pass.

**Usage:**

```bash
maida accept [TRACE_ID] --baseline PATH --reason TEXT
```

**Arguments / options:**

| Argument/Option | Default | Description |
|---|---|---|
| `TRACE_ID` | *(latest run)* | OTel trace ID or prefix to accept |
| `--baseline`, `-b` | *(required)* | Existing baseline JSON file to update |
| `--reason`, `--message`, `-m` | *(required)* | Human-readable reason for accepting the change |

**Examples:**

```bash
maida diff --baseline .maida/baselines/my_agent.json
maida view
maida accept --baseline .maida/baselines/my_agent.json --reason "expected retrieval tool split"
git diff .maida/baselines/my_agent.json
```

**Exit codes:** `0` baseline updated or already matched; `2` run or baseline not found, invalid baseline, invalid run, or missing reason; `10` internal error.

When the accepted run changes baseline behavior, the baseline JSON is rewritten
with the same structural fields produced by `maida baseline` plus an
`acceptance` provenance object. It records `accepted_by`, `accepted_at`, the
reason, Maida version, source run ID, source repository/PR/commit, an
accepted-run verdict summary, and the previous baseline source run ID and
SHA-256. Subsequent Markdown assertion reports render this block under
**Baseline provenance**.

In GitHub write-back jobs, Maida reads `GITHUB_ACTOR`, `GITHUB_REPOSITORY`,
`GITHUB_SERVER_URL`, `MAIDA_PR_NUMBER`, and `MAIDA_EXPECTED_HEAD_SHA` so the
artifact identifies the approving user and exact PR revision. A local accept
records the local OS user and leaves unavailable PR/commit fields empty. If the
selected run already matches the baseline structurally, Maida prints
`no update written` and leaves the file untouched.
