# Check your coding agent before merge

**Tests may say the result looks fine while the agent's execution behavior regressed.** Maida checks an agent change before merge. Start with a report on one normal task in your own repository; protect the next change once that report is useful.

## Try Maida in your repository

Use Python 3.12–3.14 and a Git repository where you use Claude Code:

```bash
uv tool install "maida-ai==0.6.1"

cd my-repo
maida init

# Run one normal Claude Code task and exit the session.

maida check
# Follow the printed:
maida view <TRACE_ID>
```

Approve init's setup preview, then start a **new** Claude Code session with `claude`. Do one normal task and exit normally. For a small first task, ask: **“Find this repository's test command and cite the configuration file that defines it. Do not edit files or install dependencies.”** Check the answer against that file.

**A successful report says `3 active checks passed`**, shows your task's trace ID, and prints the exact viewer command. Replace `<TRACE_ID>` with that printed ID to open the same task's timeline. You have a useful report without creating a baseline or policy, changing agent code, or cloning tutorials.

**Runs on your machine or CI runner. No Maida cloud account required.** Task evidence is not uploaded to Maida. Your coding agent still uses its usual model provider, permissions, and costs.

Already installed Maida in the project's uv environment? Use `uv run maida init`, `uv run maida check`, and the printed `uv run maida view <TRACE_ID>`. Init connects that installation to the agent; start plain `claude` afterward.

## Investigate a regression

**Follow `maida view <TRACE_ID>` from the report.** Inspect the named failure and the sequence of tool calls. Fix its cause and repeat the task. Missing or unfinished capture gives recovery guidance instead of showing an older successful task.

The first `maida check` requires successful completion, no recorded loop warnings, and no recorded guardrail events. It does not yet compare the task with a known-good run or use an existing policy. It observes tool activity and session lifecycle; answer correctness and complete model-call, token, and latency coverage are outside this check. Keep your ordinary tests and evals.

## Protect the next agent change

**Keep the behavior you reviewed, then check the next change against it.** An agent change can affect instructions, skills, tools, model configuration, harness code, or application code. Maida gates the resulting behavioral change whether a human or the agent authored it.

Choose a successful task you understand. Keep its task text, starting commit, agent/model versions, and configuration with the review. Use the trace ID printed by `maida check`:

```bash
maida init --from-run <TRACE_ID>
```

Review `.maida/starter/policy.yaml`. It proposes a few checks from the observed task: completion, no recorded loops, and no recorded guardrail events. Delete requirements the task does not need; keep at least one meaningful check. These are **candidates for your review**, not guarantees inferred from one task. The draft is inactive until you accept it:

```bash
maida init --reviewed --reason "This task must finish without recorded loops or guardrail stops"
```

This keeps a baseline (the reviewed observation), a policy (the requirements), and a review record. Repeat the **same task from the same starting state** in a fresh session, changing only what you intend to compare. Exit, then:

```bash
maida check
# Use the new trace ID from this report:
maida assert <CANDIDATE_TRACE_ID> --baseline .maida/baselines/agent.json --policy .maida/policy.yaml
```

Read the verdict itself: **PASS** means the evidence met the selected checks; **FAIL** names a violation; **INCONCLUSIVE** means the evidence did not settle the claim. Exit `0` alone is not approval. Captured task IDs are explicit here because an assertion without an ID retains SDK/Python run selection.

Reproduce one safe, relevant failure, then repair it without replacing the baseline. The starter checks above do not automatically protect test execution or prevent test rewriting; add reviewed requirements for responsibilities you need to protect. See the [init reference](cli/init.md#keep-a-reviewed-contract-later) and [regression-testing guide](regression-testing.md) when you need more precise checks.

## See green tests hide a broken change

In the [canonical storefront demo](https://github.com/maida-ai/maida-tutorials/tree/main/demos/pr-gate), a coding agent changes the VIP regression test to approve **$15 shipping instead of $0**. All four application tests pass. **Maida fails the agent change** for rewriting the protected regression test. Its stronger reviewed policy also demonstrates skipped verification and an agent weakening its own instructions. The deterministic rehearsal uses released Maida v0.6.1 and runs offline after installation.

The demo repository is optional practice. For a quick canned report without cloning anything:

```bash
maida demo --regression
```

Expect FAIL and a PR-comment preview. The rehearsal exits `0` after showing the expected failure; a real failed check exits `1`.

## Add the PR gate

Once local pass → safe failure → repair works, [add the Action and required repository protection](https://github.com/maida-ai/maida-assert#add-the-merge-boundary). CI needs a repeatable task, pinned versions, explicit budgets, and the reviewed policy and baseline on the base branch. A saved interactive session alone is not a repeatable CI task. See [coding-agent scenarios](cli/scenario-run.md) or, for an existing Python entrypoint, [`maida run`](cli/run.md).

Test the gate on a real PR and again on the new head after intentional acceptance. Required checks, trusted base policy, and workflow-file review protection are part of the boundary; generating a workflow does not establish enforcement.

## Setup help and other integrations

Init previews automatic local setup and preserves existing settings and other hooks. If agent detection is ambiguous, use `maida init --agent claude-code`. First-run setup needs an interactive terminal; noninteractive setup previews changes and exits `2`. To upgrade an older standalone install, use `uv tool install --force "maida-ai==0.6.1"`, rerun init, follow its recovery guidance, and restart the agent session. See [init](cli/init.md) for inherited hooks or malformed settings.

To stop capture, run `maida detach --agent claude-code`, approve the preview, and restart the agent session. Other hooks and saved evidence are preserved. Reconnect with `maida init --agent claude-code`.

Your repository can use any language. For another integration, see the [integration overview](integrations.md). Building a Python tool-calling agent? Use the [Python walkthrough](python-agent.md), including installation into the project environment. For deeper questions, use [capture coverage and recovery](claude-code.md), [configuration and redaction](reference/config.md), [trace format](reference/trace-format.md), and the [CLI reference](cli.md).
