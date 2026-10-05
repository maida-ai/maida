# `maida detach`

**Unreleased extension:** `maida detach --agent codex` or `--agent chatgpt-work` detaches the shared runtime and disables both Codex and local-only Work capture, including cached plugin handlers. Other attached providers and saved imported IDs remain usable. Only Maida-owned hooks and marketplace entries are removed after preview and approval; restart clients to refresh configuration. See [Codex and local-only Work recovery](../codex-work.md#recovery-and-detach). Released v0.6.1 behavior below applies to Claude Code.

Detach Maida's Claude Code capture from the current Git repository. This command is available from v0.6.1.

```bash
maida detach --agent claude-code
```

Maida shows a warning and the files it would change, then asks once before writing. It removes exact Maida command hooks from `.claude/settings.local.json` and `.claude/settings.json`: both legacy `maida capture claude-hook` and the bound absolute-Python invocations installed by init. Shell wrappers and commands with extra arguments are preserved. Other hooks, permissions and settings are preserved. Before confirmation, tracked files are explicitly labelled `TRACKED`, with a warning that the change affects shared team configuration. Review that Git diff before committing.

Saved captures, imported runs, policies, baselines, Git excludes and the repository's local identity remain. Detach sets `enabled: false` in a valid `.maida/local.json`, so cached Maida hooks in an existing session stop recording immediately. Exit and restart Claude Code to reload its hook settings. If the local identity is damaged, Maida can still remove the observers with `--agent claude-code`; it preserves the damaged file and tells you to exit existing sessions.

Disabled hook invocations remain quiet by default. Set `MAIDA_DEBUG=1` in the environment inherited by Claude Code to print the repository, disabled state and reconnect command on stderr; the same diagnostic is available through the `maida.cli` debug logger. No hook payload is logged. Ordinary SDK/Python commands retain their existing run selection. Saved Claude tasks remain readable by the trace IDs shown in their reports; leaving the local identity file does not redirect ordinary commands.

User-wide and managed hooks are outside this operation. Custom shell wrappers around Maida are preserved; remove those handlers using Claude Code's `/hooks` menu if you also want them detached. Detach does not disable every Claude hook or uninstall Maida. Run detach before uninstalling the CLI to avoid missing-command notices from installed hooks.

Reconnect using the same identity and saved evidence:

```bash
maida init --agent claude-code
```

Repeated detach is a no-op. Declining changes nothing. Noninteractive detach shows the preview and exits `2`; run it in an interactive terminal to approve. Malformed settings, symlinks, concurrent edits and write failures identify the file or repair action, preserve existing configuration, and exit `2`. Only Claude Code is currently supported.
