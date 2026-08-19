# `maida scenario run`

Runs pinned headless Claude Code prompts in isolated fixture workspaces and
evaluates their captures against checked-in baselines.

```bash
maida scenario run [MANIFEST] [--scenario ID] [--format text|json|markdown]
```

`MANIFEST` defaults to `.maida/scenarios.yaml`. The runner validates the whole
manifest and local environment before invoking Claude. Each selected scenario
gets a fresh temporary workspace containing only its declared Git-tracked
fixture files, a unique non-persisted Claude session, an ephemeral loopback
OTLP receiver, its own native USD cap, turn cap, and process-group timeout.
The exact project settings and strict MCP configuration are passed explicitly;
permissions use `dontAsk` and dangerous permission bypass is never enabled.

Reports contain no raw Claude stdout or stderr. Per-scenario status is `pass`,
`assertion_failed`, or `agent_failed`.

**Exit codes:** `0` all pass; `1` one or more assertion failures; `2` invalid
manifest, selection, config, executable, or version preflight; `10` any agent,
capture-import, or runtime failure. Agent failure takes precedence over
assertion failure when multiple scenarios run.

See [Run isolated scenarios](../claude-code.md#run-isolated-scenarios) for the
manifest schema and a complete example.
