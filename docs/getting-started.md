# Get your first useful Maida check

Start with one short task your coding agent can do in your repository. The first checkpoint is a readable check of that actual execution. You can review a baseline and add CI later; neither is required to see your first result.

## 1. Set up in your repository

Use Python 3.12–3.14. Install the standalone CLI, then run setup inside your Git repository:

```bash
uv tool install maida-ai
maida init
```

Automatic capture setup is new after shipped v0.6.0. These instructions require an installation whose `maida init --help` includes `--agent`; until that release is published, use a wheel built from this checkout. Shipped v0.6.0 supports the reviewed starter flow below but does not perform automatic first-run setup.

Maida detects your environment, prioritizing repository configuration over installed agent commands. For Claude Code, it shows the passive capture hooks, local installation pointer and storage location before asking for one approval. It preserves existing settings and hooks and creates no policy or baseline. If detection is ambiguous, it names the environments; for a Claude Code task, rerun `maida init --agent claude-code`. Unsupported environments and malformed settings receive a concrete recovery action before anything is written.

Setup writes local Claude settings, keeping shared team settings and existing Python run storage intact. To stop capture, run `maida detach --agent claude-code` and approve its removal preview. Other hooks and saved evidence are preserved. Exit and restart any existing Claude Code session afterward; reconnect with `maida init --agent claude-code`.

Existing SDK/Python commands and baseline gates keep selecting runs as before setup. The first check below automatically selects this repository's captured task. To inspect that task later, use the run ID shown in its report, for example `maida view RUN_ID`.

Your repository can use any language. Building a Python tool-calling agent? Use the secondary [Python walkthrough](python-agent.md), including installation into the project environment.

## 2. Get a useful result in your own repository

After approval, start a new Claude Code session in this repository. Choose a task that normally takes a minute or two. For example: **“Find the command this repository uses to run its tests. Cite the configuration file that defines it. Do not edit files or install dependencies.”** Check the answer against the cited file. Exit the session normally, then run:

```bash
maida assert --expect-status ok --no-loops --no-guardrails
```

Expect a report checking your task's observed completion, recorded loop warnings and recorded guardrail events. No baseline is needed. Evidence is automatically scoped to this repository; you do not manage a storage environment variable or select a trace ID for this check. An unfinished newest session produces a recovery message rather than showing an older task. If a check fails, follow the printed `maida view RUN_ID` command, fix the cause and repeat the bounded task. If the repository already has `.maida/policy.yaml`, `assert` also loads it.

This first check answers whether the observed signals met the three requested checks. It does not compare against a known-good baseline or establish answer correctness. Hook capture observes tool activity and lifecycle, not complete model-call, token or latency coverage. It follows your configured redaction settings. No telemetry receiver setup is required and no task evidence is sent to Maida. Your agent's normal provider use has its usual permissions and costs.

**You can stop here.** You have a report on your own task and local evidence to inspect. The target is under five minutes from `init` to a report for a bounded task; this is not a measured activation claim. Resolve capture errors before adding policy or CI.

For an offline rehearsal with the currently shipped release, use `uv tool install "maida-ai==0.6.0"`, then `maida demo --regression`. It shows a FAIL verdict and PR-comment preview on canned data, needs no clone or API keys and exits `0` after showing the failing gate. An actual failed check exits `1`; demo evidence is not selected as your first captured task.

## 3. Keep a small contract for the next change

Keep the task text, starting commit, agent/model versions, and configuration together. Choose a successful observation of that task. Run `maida init --help` to check the installed command contract: versions with `--from-run` support the [reviewed init workflow](cli/init.md). It proposes a few observed invariants, lets you edit them, and records your explicit review before activating a baseline and policy.

The [init reference](cli/init.md#observe-one-task) gives the commands for drafting, review and checking the same task again. Avoid copying a policy template for tools your task does not use.

After local PASS, reproduce one safe, relevant failure and repair it without replacing the baseline. Read the verdict itself: PASS means the observed evidence met the selected checks; FAIL means investigate the named violation; INCONCLUSIVE means the evidence did not settle the configured claim. Exit `0` alone does not mean approval. Setup errors mean repair the missing input or capture and rerun.

## 4. Add CI when the local check is useful

Keep the repeatable task, agent/configuration identity, reviewed baseline, and policy in Git. Captured evidence remains local. Repeatable coding-agent CI needs a [pinned scenario](cli/scenario-run.md); Python entrypoints use [`maida run`](cli/run.md).

Follow the [Action setup](https://github.com/maida-ai/maida-assert#blocking-mode-and-required-repository-settings) to configure the repository boundary. The [init reference](cli/init.md) explains generating a workflow for an existing traced Python entrypoint. For the combined acceptance listener, require the explicit `Maida / agent-check` commit status with branches up to date; the Action setup explains why the workflow job and named check must not also be requirements for this route. Keep other required checks and reviews. Test these settings on an actual PR, including the new PR head after acceptance. Trusted base policy and workflow-file protection are part of that setup; generating a file alone does not establish protection.

Add another task when it protects a real change you make. For storage and interoperability details, see the [trace format](reference/trace-format.md); for storage locations and redaction, see [configuration](reference/config.md).
