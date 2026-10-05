# Native Codex capture

**Unreleased.** This feature belongs to the development stack and must stay marked unreleased until the patch containing it is published. The published installation examples in the [shared first-task guide](getting-started.md) support Claude Code. For development, use the checkout's environment (`uv sync --dev`, then `uv run maida ...`); do not expect a released package to contain this feature.

Claude Code and Codex share the same first-report flow. The only client distinction is the launch command: `claude` for Claude Code; `codex` for Codex, with native Maida hook review/trust when requested. Follow the shared guide for task selection, investigation and accepting a reviewed baseline; this page covers Codex's capture boundary and acceptance.

## Setup and one-terminal flow

From the development environment in your Git repository:

```bash
uv run maida init --agent codex
codex
# Review/trust Maida hooks if requested; complete one bounded repository task.
# Exit Codex.
uv run maida check
# Run the exact printed "View:" command.
```

Approve init's setup preview. It merges `.codex/hooks.json`, binds the installed Maida interpreter, preserves unrelated hooks/configuration and file permissions, and adds machine-specific artifacts to Git's local excludes. Tracked configuration changes are flagged for review. Setup reports **configured; awaiting first capture**: writing configuration does not verify native capture. Maida never grants trust on your behalf. Follow Codex's native project/hook review when requested; changed commands need renewed review. The [official hook documentation](https://learn.chatgpt.com/docs/hooks) defines this trust boundary.

Automatic detection selects a single supported provider; ambiguous Claude/Codex environments require `--agent claude-code` or `--agent codex`. Repeating init preserves v2 project identity and evidence while repairing a moved environment after approval. Older local pointers are rejected without migration: move `.maida/local.json` aside and rerun init. Saved evidence remains on disk; a new identity gets a new capture namespace.

## Coverage, lifecycle and selection

A Codex task is a runtime turn. Capture uses only `SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Stop`, `Interrupt` and `SessionEnd`. A root stop with paired observed tools establishes completion. Later tool activity invalidates that completion until another root stop. Child stops cannot complete the root. Session closure preserves completed turns but cannot repair missing tool completion or manufacture success. Recovered tool errors remain visible.

Lifecycle identity and mapping provenance are preserved without conversational content. Prompt text, assistant messages and transcripts are never persisted. Tool inputs/results pass through redaction and truncation before storage. Model calls, token usage, cost, complete tool coverage, subagent topology and answer correctness are outside this evidence. Structural similarity is not correctness; these observations cannot guarantee behavior outside the available evidence.

Plain `maida check` selects the newest started task across attached enabled Claude and Codex providers. `--agent codex` or `--agent claude-code` narrows selection. A newer unfinished, interrupted, corrupt or unimportable task gives recovery guidance instead of older success. Claude retains session-based completion. Checking before exit can work, but onboarding checks after exiting Codex.

Check materializes a validated immutable snapshot. Changed evidence produces a new trace ID and retains the earlier imported run. Use the exact trace ID and viewer command printed by your report, including `uv run maida view TRACE_ID` when invoked through uv. JSON/Markdown keep these notices on stderr. SDK/Python storage and ordinary default run selection remain unchanged. First-run checks test observed completion, loops and guardrail events; they do not apply a reviewed baseline or policy.

## Recovery and detach

For missing evidence, confirm the session's repository, provider enablement, native trust review and that the bounded task used a hook-observed tool. For incomplete or interrupted evidence, run a fresh bounded task, let it finish, exit and check again. Preserve malformed/conflicting evidence and follow the printed recovery action; do not replace it with older success.

```bash
uv run maida detach --agent codex
```

After preview and approval, Maida removes only its owned native hooks and disables Codex capture. Existing session handlers become inert; exit/restart Codex to reload configuration. Other hooks, Claude capture, policy, baselines and saved evidence remain available. Explicit imported IDs resolve after detach. Reconnect with `uv run maida init --agent codex` and review changed native hooks when requested. This stack contains no ChatGPT Work, plugin or marketplace setup.

## Native acceptance status and procedure

Interactive native capture and browser timeline acceptance remain pending; fixture tests do not establish interactive compatibility. Native Windows acceptance remains pending. Linux tests exercise actual bound hook subprocesses, parse seven hooks with the pinned native runtime, and drive a real native tool with a canned local model endpoint. Those checks do not replace the complete trusted interactive journey. Do not call Codex complete until the journey below is recorded on supported POSIX and native Windows paths.

The pinned smoke uses Codex CLI `0.160.0`. It creates a fresh Git repository and isolated home/config/storage, clears inherited credentials, and serves a canned model endpoint on loopback. It never edits a trust database or installs/upgrades Codex. Prepare once from an interactive terminal:

```bash
uv run python scripts/codex_capture_smoke.py --prepare-directory /tmp/maida-codex-acceptance
uv run python scripts/codex_capture_smoke.py --interactive-directory /tmp/maida-codex-acceptance
```

On Windows use a new absolute temporary directory, such as `C:\Temp\maida-codex-acceptance`, in both commands. Approve Maida's setup preview, launch the real Codex terminal, review/trust the fixture project and hooks in the native UI, complete the one-file read task, and exit Codex. The smoke then runs check and the exact printed viewer command with a loopback port and browser disabled; it verifies the task ID, successful tool count, and absence of conversation in Maida storage. Its output distinguishes automated viewer selection from pending visual browser review. Run `uv run python scripts/codex_capture_smoke.py --view-directory /tmp/maida-codex-acceptance` (use your fixture directory on Windows) to launch the exact printed command in the isolated fixture environment and inspect the actual timeline, then record the Codex version, OS, Maida commit, trace ID and results.

For additional protocol acceptance, `--directory` runs the real pinned app-server after user-reviewed trust and checks after shutdown. Untrusted hooks fail preflight. On this managed Linux environment the nested native sandbox has previously rejected app-server socket mounts. `--external-sandbox` is only for protocol tests inside an already isolated container and records native nested sandbox coverage as unverified; it is not the interactive acceptance route. Neither route adds `codex exec --json` scenario support.
