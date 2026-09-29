# Get your first useful Maida check

Start with one short task your coding agent can do in your repository. The first checkpoint is a readable check of that actual execution. You can review a baseline and add CI later; neither is required to see your first result.

## 1. See a regression before configuring anything

Use Python 3.12–3.14. The explicit pin below installs the v0.6.0rc1 prerelease. Install the standalone CLI, then run the offline example:

```bash
uv tool install "maida-ai==0.6.0rc1"
maida demo --regression
```

Expect a FAIL verdict and a preview of the PR comment: the agent still answers, but repeats a tool and takes an unexpected path. The demo exits `0` after successfully showing the failing gate; an actual failed gate exits `1`. It uses canned data, no API keys, and no repository clone. Its baseline does not describe your agent.

## 2. Get a useful result in your own repository

Allow 10–15 minutes for this first checkpoint, including capture setup. Choose a task that normally takes your agent a minute or two. For example: **“Find the command this repository uses to run its tests. Cite the configuration file that defines it. Do not edit files or install dependencies.”** Check the answer against the cited file. You do not need to make a code change or introduce a regression yet.

Follow [Protect one coding-agent task](https://github.com/maida-ai/maida-tutorials/blob/main/guides/coding-agent.md#checkpoint-1-check-your-normal-work) to add passive capture while preserving existing hooks. Finish a fresh session normally so capture imports the completed run. Your repository can use any language. If you build a Python tool-calling agent, use the secondary [Python walkthrough](python-agent.md).

In the same terminal and local evidence directory used for capture:

```bash
maida list
maida assert --expect-status ok --no-loops --no-guardrails
```

Expect your task's completed run, then a report checking successful completion, recorded loop warnings, and recorded guardrail events. You have explicitly requested those three checks; no baseline is needed and no policy file is created. If the repository already has `.maida/policy.yaml`, inspect it first: `assert` also loads that policy. If capture is missing or the run is incomplete, fix capture before continuing. If a check fails, inspect that observation with `maida view`, fix the cause, and repeat the small task. Do not substitute a demo trace for your repository's evidence.

This first check answers whether those observed signals met your requirements. It does not compare against a known-good baseline or establish that the answer was correct. Hook capture observes tool activity and lifecycle; it does not establish complete model-call, token, or latency coverage. The [capture integration](claude-code.md) also supports a local loopback telemetry receiver; this is not telemetry sent to Maida. Your coding agent's ordinary provider use has its usual permissions and costs.

**You can stop here.** You have a report on your own task and local evidence to inspect. The 10–15 minute allowance is a setup target, not a measured activation claim. If setup consumes it, resolve that capture problem before adding policy or CI.

## 3. Keep a small contract for the next change

Keep the task text, starting commit, agent/model versions, and configuration together. Choose a successful observation of that task. Run `maida init --help` to check the installed command contract: versions with `--from-run` support the [reviewed init workflow](cli/init.md). It proposes a few observed invariants, lets you edit them, and records your explicit review before activating a baseline and policy.

The coding-agent walkthrough gives the commands for drafting, review, and checking the same task again. For Maida 0.5.x, follow its separate [compatibility walkthrough](https://github.com/maida-ai/maida-tutorials/blob/main/guides/coding-agent-0.5.md); that release needs a helper for comparing fresh captured sessions. Avoid copying a policy template for tools your task does not use.

After local PASS, reproduce one safe, relevant failure and repair it without replacing the baseline. Read the verdict itself: PASS means the observed evidence met the selected checks; FAIL means investigate the named violation; INCONCLUSIVE means the evidence did not settle the configured claim. Exit `0` alone does not mean approval. Setup errors mean repair the missing input or capture and rerun.

## 4. Add CI when the local check is useful

Keep the repeatable task, agent/configuration identity, reviewed baseline, and policy in Git. Captured evidence remains local. Repeatable coding-agent CI needs a [pinned scenario](cli/scenario-run.md); Python entrypoints use [`maida run`](cli/run.md).

Follow the [Action setup](https://github.com/maida-ai/maida-assert#blocking-mode-and-required-repository-settings) to configure the repository boundary. The [init reference](cli/init.md) explains generating a workflow for an existing traced Python entrypoint. For the combined acceptance listener, require the explicit `Maida / agent-check` commit status with branches up to date; the Action setup explains why the workflow job and named check must not also be requirements for this route. Keep other required checks and reviews. Test these settings on an actual PR, including the new PR head after acceptance. Trusted base policy and workflow-file protection are part of that setup; generating a file alone does not establish protection.

Add another task when it protects a real change you make. For storage and interoperability details, see the [trace format](reference/trace-format.md); for storage locations and redaction, see [configuration](reference/config.md).
