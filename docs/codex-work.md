# Codex and local-only ChatGPT Work capture

**Unreleased.** These commands require a development checkout containing the Codex capture changes. The published `maida-ai==0.6.1` quickstart supports Claude Code. Local-only ChatGPT Work desktop compatibility still requires the acceptance journey below; fixture tests do not establish desktop support.

Maida observes native command hooks without application instrumentation. Codex and local-only Work use the same capture runtime. This route requires both orchestration and execution on the local machine. Work Cloud, dots, and ordinary Chat are outside this release. OpenAI documents that cloud orchestration cannot use local command or plugin hooks even when tools execute locally. See the [official hook documentation](https://learn.chatgpt.com/docs/hooks).

## Set up Codex

From an installed development environment inside your Git repository:

```bash
uv run maida init --agent codex
```

Review and approve the setup preview. Start a new Codex session in this repository, accept the native project trust review when appropriate, and use `/hooks` to inspect and trust the exact Maida hooks. Maida does not grant native trust. A changed hook requires another review. Complete one bounded task, leave the session open, and run:

```bash
uv run maida check --agent codex
```

Setup binds the receiver to this Maida installation's absolute Python interpreter and probes it outside the checkout. Plain agent usage can then capture without a global Maida executable, receiver process, session-ID extraction, or manual import. Repeating setup preserves the repository identity and evidence while repairing moved environments after approval; restart the client and review any changed hooks again.

## Prepare local-only Work

The desktop route does not require the Codex CLI:

```bash
uv run maida init --agent chatgpt-work
```

Review the local plugin and repository marketplace changes in the setup preview. After approval, fully restart the ChatGPT desktop app, open this repository in local-only Work, open the Plugins Directory, choose the repository marketplace, and install the Maida capture plugin. Review and trust its command hooks through the client's native hook review. Start a new local-only thread, complete one bounded task, then run:

```bash
uv run maida check --agent chatgpt-work
```

OpenAI's [plugin packaging documentation](https://developers.openai.com/plugins/build/plugins) describes repository marketplace discovery, desktop restart, installation, and cached plugin copies. Marketplace discovery does not mean the plugin is installed, enabled, trusted, or capturing. Setup means **configured; awaiting first capture** until a real task is observed. The Maida environment must remain executable on this machine even when the plugin is copied into the desktop cache.

Direct Codex hooks and Work plugin hooks may coexist. Identical deliveries are deduplicated. Hook payloads do not necessarily distinguish clients: attaching both routes does not let Maida prove that a task came from Work. An explicit provider selector narrows the attached capture runtime; it does not manufacture Work-specific provenance.

## What a check selects and covers

For Codex and local-only Work, a task is one runtime turn. A root `Stop` records observed termination only when observed tools have terminal pairs. Child stops do not complete the root task. Continuation or later tool activity invalidates completion until a subsequent valid root stop. Interrupts and session closure do not establish success. A recovered tool error remains visible even if the task later completes.

Plain `maida check` selects the most recently started task across attached providers in this repository. Use `--agent claude-code`, `--agent codex`, or `--agent chatgpt-work` to select an attached provider. Claude Code keeps its session-based completion behavior. A newer unfinished, interrupted, corrupt, or unimportable task gives recovery guidance instead of selecting an older successful task.

The first check evaluates observed completion, loops, and guardrail events. It does not apply an existing baseline or policy. Capture retains available source identity, tool pairing, subagent topology, and mapping provenance in the existing trace schema. It does not parse private transcripts or invent model calls, token usage, cost, answer correctness, or complete tool coverage. Inputs and results pass through redaction and field-size limits before local persistence. These observations are evidence about captured activity, not guarantees about unobserved actions.

`check` materializes a validated trace from short synchronous hook receipts. Identical evidence reuses its immutable imported run. Additional evidence produces a new snapshot identity; the earlier imported run remains available. The report prints its selected trace ID and exact viewer command. Use that command, such as `uv run maida view TRACE_ID`, to inspect the same snapshot. Bare `view`, SDK/Python storage, and ordinary default run selection retain their existing behavior. JSON and Markdown reports keep selection notices and viewer commands on stderr.

For a reviewed gate, explicitly select the captured trace:

```bash
uv run maida assert TRACE_ID --baseline .maida/baselines/agent.json --policy .maida/policy.yaml
```

## Recovery and detach

If there is no evidence, check that the new session uses this repository, the installation is enabled, and native hooks are trusted. For Work, also confirm that the plugin is installed and enabled in the repository marketplace and that orchestration is local. Restart after installing or updating the local plugin. An incomplete turn needs its actual terminal evidence; closing the session cannot repair missing tool completion. Inspect the recovery action printed by `check` for malformed or conflicting evidence.

To stop the shared runtime:

```bash
uv run maida detach --agent codex
```

Detaching Codex or ChatGPT Work disables **both** routes. After preview and approval, Maida removes only its owned registrations and disables the shared provider in the local pointer. Cached desktop plugin hooks become inert through that disablement. Restart the clients to refresh their configuration. Other providers and saved captures remain usable; explicit imported trace IDs still resolve after detach. Reconnect using the appropriate `init --agent` route, restart, reinstall an updated cached Work plugin when needed, and complete native trust review again.

Existing version-1 Claude installation pointers remain readable. Explicit attachment of another provider upgrades local installation state while preserving the project ID and evidence paths. Review any setup changes marked `TRACKED` before committing: repository-local marketplace and hook configuration may already be shared. Machine-specific generated artifacts belong in Git's local excludes.

## Acceptance before claiming desktop support

Record desktop version, operating system, Maida commit, and the installation environment. In a fresh repository and isolated test home, perform the actual desktop restart → repository marketplace discovery → plugin install → native trust review → new local-only Work turn → `check` → printed viewer journey. Keep the thread open for the first check, then start a second unfinished turn and confirm that `check` refuses to reuse the earlier completion. Complete that turn and inspect its paired tool activity and coverage limits.

Repeat with direct Codex capture attached too and confirm that activity is not counted twice. Check an interrupted turn, a subagent task, and a resumed/compacted thread. Detach the shared runtime while the desktop still holds its cached plugin, run another task, and confirm that no new receipt is stored. Confirm that Claude capture and saved imported IDs still work. Relocate the Maida environment, rerun setup, restart/reinstall, and review the changed hook definitions before confirming capture again. Record actual results and failures; generated files and simulated payloads cannot substitute for this journey.

The pinned Codex smoke in `scripts/codex_capture_smoke.py` uses Codex CLI `0.160.0`, a local canned Responses endpoint, and the real app-server lifecycle after native trust review. From the installed development environment, prepare a fresh fixture interactively:

```bash
uv run python scripts/codex_capture_smoke.py --prepare-directory /tmp/maida-codex-acceptance
```

Approve its Maida setup preview. Run the printed `review_command_posix` in a terminal, review the fixture's project and `/hooks` trust prompts, and exit that session without submitting a task. The command uses the fixture's isolated home and clears inherited credentials; it does not change your ordinary Codex setup. Then:

```bash
uv run python scripts/codex_capture_smoke.py --directory /tmp/maida-codex-acceptance
```

The smoke checks that the real read tool succeeded and runs `maida check` before closing the session. Unreviewed hooks fail preflight; they are never automatically trusted. On this managed Linux container, the pinned runtime's nested read-only sandbox rejects its app-server socket mount. Only inside an already isolated container, `--external-sandbox` uses the native protocol's external isolation policy and records that native nested sandbox coverage remains unverified. A completed model turn with a failed fixture tool cannot count as a passing smoke.

The smoke neither requires production credentials nor implements `codex exec --json` scenarios. Work Cloud capture through enterprise-managed remote MCP hooks is a future feature; it needs its own connectivity, evidence, and failure-behavior acceptance.
