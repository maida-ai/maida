# `maida init`

**Automatic capture setup and `maida check` are new after shipped v0.6.0.** Check `maida init --help` for `--agent` and `maida check --help` before using the default setup path below. The reviewed starter workflow requires `--from-run`, available in v0.6.0.

## Set up your first task

Run `maida init` inside your Git repository. Maida uses repository configuration before installed agent commands to detect Claude Code. It previews passive observer additions or upgrades in `.claude/settings.local.json`, a local setup file in `.maida/local.json`, and their Git local exclude entries, then asks once before writing anything. Shared `.claude/settings.json` is preserved; local setup does not install hooks for teammates. Existing settings, permissions and other hooks are preserved, and compatible inherited Maida observers are reused. Repeating setup adds no duplicate observers and keeps the same project identity. `--force` does not bypass first-run approval.

Hooks use the absolute Python executable from the Maida environment running init. Setup validates the capture command outside the repository without relying on PATH or a project import path. This supports `uv run maida init` followed by plain `claude`; no global Maida command or `uv run claude` is needed. If you remove or relocate that environment, rerun init from the replacement environment to update local Maida observers after approval.

Exact legacy Maida observers in local settings are upgraded after approval, preserving other handlers and settings. Older inherited Maida observers cannot be silently replaced: shared project hooks require `maida detach --agent claude-code` followed by init, and user-wide hooks require removing those Maida entries in Claude Code's `/hooks` menu followed by init. Setup identifies the affected file and writes nothing in this case. Compatible bound observers inherited from the same environment are reused.

```bash
maida init
```

After approval, follow the single printed next action: start a new Claude Code session here, run one bounded task, exit the session, then:

```bash
maida check
```

No policy or baseline is created. The report concerns the newest task captured for this repository; an unfinished or missing task cannot fall back to another repository's evidence. Hook capture observes tool activity and lifecycle, not answer correctness or complete model-call, token or latency coverage. Capture follows your configured redaction settings. Task evidence stays on this machine; no local receiver setup or telemetry sent to Maida is involved.

The installation pointer records a generated project ID, format version, capture choice and, after detaching, its enabled state. It is local state, not authoritative policy. Git's local exclude file keeps it and any existing or newly written local Claude settings out of ordinary commits; no exclude is added for an absent local settings file when all observers are inherited. Claude evidence defaults to `~/.maida/projects/<project-id>/runs/`, `captures/` and `onboarding/`; a Maida storage override changes the parent for Claude capture while preserving isolation. SDK tracing, Python trial runners and other importers retain their exact configured storage directory. New clones and worktrees initialized separately get separate IDs. No remote URL is used as identity.

Setup attaches capture without changing ordinary SDK/Python commands. `list`, `view`, `export`, `baseline`, `accept`, `diff`, starter `--from-run latest` and `assert` keep their existing run selection. `maida assert --baseline PATH` automatically selects the latest SDK/Python candidate. `maida check` automatically selects this repository's Claude task and prints its trace ID and exact viewer command; when launched through uv, it prints `uv run maida view TRACE_ID`. Explicit Claude trace IDs resolve automatically, including after detach. See [run selection](../cli.md#run-selection-after-capture-setup).

To stop capturing, run `maida detach --agent claude-code`. It previews removal and asks for one approval, preserves other hooks and saved evidence, and can be reversed with `maida init --agent claude-code`. See the [detach command](detach.md) for existing-session and shared-hook behavior.

If multiple agent environments are detected, Maida names them and writes nothing. For a Claude Code task, resolve the ambiguity with `maida init --agent claude-code`; this still previews changes and asks for approval. Unsupported environments receive an explicit explanation. Noninteractive setup is preview-only and exits `2`; approve setup from an interactive terminal. Malformed settings, disabled hooks and write failures identify a concrete repair action.

## Keep a reviewed contract later

Initialize from a successful run of one real task. Maida proposes a small policy from what it observed, shows the source workflow and trace IDs, and waits for your explicit review before activating anything. It never invents a Python entrypoint or a forbidden tool.

## Observe one task

Capture your coding-agent task using the [coding-agent walkthrough](../getting-started.md) and take its run ID from the first check's report, or run your existing [traced Python agent](../python-agent.md) and select the completed run in `maida list`, then:

```bash
maida init --from-run RUN_ID
```

`--from-run latest` chooses the latest SDK/Python run as before capture setup. Use the Claude run ID from its report to draft from that task. Check the displayed workflow and trace ID: the latest SDK run might belong to another project or the demo. Repeat `--from-run` to include several successful observations with the same workflow name. Duplicate, incomplete, failed and mixed-workflow selections are rejected before any files are written.

The inactive draft lives in `.maida/starter/`:

- `policy.yaml` proposes up to three invariants: successful completion, no observed loop warnings, and no observed guardrail events. Rules unsupported by the selected evidence are omitted. It adds no measured tolerances or population pass-rate claim.
- `baseline.json` records the fixed structural sample without prompts, responses, tool arguments or results.
- `review.json` records source trace IDs, the workflow, observation count and baseline hash.

## Review and activate

Read `.maida/starter/policy.yaml`. Delete any rule your task does not require, and keep at least one invariant. A rule observed once is a candidate contract, not a guarantee about future executions. The starter covers only recorded structural signals; answer correctness and unobserved actions remain outside its scope.

After you have reviewed the candidates:

```bash
maida init --reviewed --reason "This task must finish without loops or guardrail stops"
```

Maida reloads the edited policy, checks it against every selected source observation, and verifies the baseline hash. It writes `.maida/policy.yaml` and `.maida/baselines/agent.json`, and records the acceptance reason, local user, timestamp and accepted artifact hashes. This is local policy acceptance; it does not grant a GitHub configuration-acceptance token or authorize a merge.

Run the same task again and check the new observation:

```bash
maida assert --baseline .maida/baselines/agent.json --policy .maida/policy.yaml
```

For a captured Claude task, run `maida check` to obtain the new task's run ID, then pass that ID to `maida assert RUN_ID --baseline .maida/baselines/agent.json --policy .maida/policy.yaml`. The command above without an ID selects the latest SDK/Python candidate. Each command checks one observed execution. A failing invariant exits `1`; a passing observed check exits `0`. A different tool order or step count is allowed unless you explicitly add those contracts. Investigate a captured task with the printed viewer command; accept intentional baseline changes only after reviewing their reason and evidence.

## Add a Python gate to GitHub

For an existing traced Python entrypoint, add the workflow after reviewing the starter:

```bash
maida init --github --agent-script path/to/your_agent.py
```

The path must identify a real Python file inside this repository. The workflow uses the reviewed policy and baseline paths, installs declared project dependencies from `uv.lock`/`pyproject.toml` or `requirements.txt`, and runs the selected entrypoint in both the gate and the isolated acceptance-capture job. If your entrypoint needs optional dependency groups, system packages or runtime credentials, add those to the two execution jobs before enabling CI. The pinned Action's acceptance runner uses Python 3.12; verify your project's compatibility. Existing stored coding-agent captures remain a local gate; this option does not fabricate a reproducible coding-agent scenario.

Run the printed `maida run` command locally, commit the policy, baseline, review record and workflow, then require the explicit `Maida / agent-check` commit status with branches up to date and configure workflow review protection. For this combined acceptance listener, do not also require the `agent-check` job or `Maida statistical gate` named check: dispatch jobs run at the default-branch SHA, and a named check can remain attached to a suppressed bot-push suite. The named check and sticky report still provide evidence; the explicit status publishes the protected decision on the verified PR head. Read the [Action protection requirements](https://github.com/maida-ai/maida-assert#blocking-mode-and-required-repository-settings). Policies are evaluated from the trusted PR base. Introducing or changing `.maida/` needs separate explicit configuration acceptance; this scaffold does not bypass it. The write job remains data-only and never checks out or runs candidate code.

All Action components use the `v0.6.0` release tag, including `maida-ai/maida-assert@v0.6.0` and `maida-ai/maida-assert/accept-command@v0.6.0`. Publish the Action release before running this workflow. For production workflows, replace the tag with the reviewed full commit SHA and keep that revision consistent across every Action component.

Use `--force` only after reviewing the starter or workflow files that will be replaced. Maida preflights all target files, refuses symlinked or escaping outputs, and preserves existing files by default. First-run hook setup always merges and requires approval for changes.

Exit codes: `0` capture setup completed, already configured or declined, or draft/activation completed; `2` unsupported/ambiguous environment, noninteractive setup needing approval, missing observations, invalid configuration or file collision; `10` unexpected internal failure.
