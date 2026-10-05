# Maida documentation

**Check an agent change before merge.** A coding agent can return a plausible answer while looping, skipping verification, or rewriting a test to hide a bug. Output tests and evals may pass; Maida also checks the agent's execution behavior.

## Try Maida

Use Python 3.12–3.14 and your own Git repository. Claude Code and Codex follow the same `init → task → exit → check → printed view` flow. **Codex is unreleased**; use a development installation containing this stack before selecting it. The published installation example below supports Claude Code.

| Coding agent | Launch a new session | Setup distinction |
| --- | --- | --- |
| Claude Code | `claude` | `maida init --agent claude-code` |
| Codex (unreleased) | `codex` | `maida init --agent codex`; review/trust native Maida hooks when requested |

Complete one bounded task, exit the agent, then run `maida check` and its exact printed viewer command. See [Codex capture and native acceptance](codex.md) for the development install, coverage and recovery details.

```bash
uv tool install "maida-ai==0.6.1"

cd my-repo
maida init

# Run one normal Claude Code task and exit the session.

maida check
# Then run the exact "View:" command printed by Maida.
```

Approve the setup preview and start a new agent session. **Success looks like `3 active checks passed`**, your task's trace ID, and its viewer command. No tutorial clone or agent-code changes are needed. **Runs on your machine or CI runner. No Maida cloud account required.** Task evidence is not uploaded to Maida; your agent's normal provider use is separate. If Maida is in the project's uv environment, prefix its commands with `uv run`.

For example: `maida view 83aa19e3`. Use the command from your own report.

[Follow the first-task walkthrough](getting-started.md). For secondary proof, the [storefront demo](https://github.com/maida-ai/maida-tutorials/tree/main/demos/pr-gate) shows **green application tests approving $15 VIP shipping while Maida fails the agent change**. The deterministic rehearsal uses released Maida v0.6.1. For a smaller canned report without a clone, run `maida demo --regression`.

## Check an agent change

Instructions, skills, tools, model configuration, harness code, and application code can all change agent behavior. Maida gates the resulting change whether a human or the agent authored it.

- [Check a normal task](getting-started.md): `init → task → check → view`, then compare the next change.
- [What the first check covers](cli/check.md): completion, recorded loops, and guardrail events; it does not test answer correctness or compare a baseline.
- [Repeat a coding-agent scenario](cli/scenario-run.md): controlled task execution for comparisons.
- [Regression testing](regression-testing.md): reviewed requirements and comparative verdicts.

## Investigate a regression

**Run the exact `View:` command printed in the report**, inspect what the agent did, and repair an unintended change before repeating the task.

- [Viewer](viewer.md): the local execution timeline.
- [Diff](cli/diff.md): what changed against the baseline.
- [Capture recovery](claude-code.md): missing or incomplete evidence.
- [Debug-gate skill](https://github.com/maida-ai/skills/tree/main/product/maida-debug-gate): guided report investigation.

## Protect the next agent change

Review the behavior you observed, keep a baseline and policy, then reproduce a safe failure and repair. Observed checks need human acceptance; one successful task does not guarantee future behavior.

- [Review and keep your first requirements](getting-started.md#protect-the-next-agent-change).
- [Reviewed setup](cli/init.md#keep-a-reviewed-contract-later) and [intentional baseline acceptance](cli/accept.md).
- [Gate draft extraction](extraction.md): derive candidates for human review.
- [Guardrails](guardrails.md): stop runaway runs during development.
- [Scheduled checks](scheduled-checks.md): compare completed run windows.

## Add the PR gate

After the local comparison works, [add the Action and repository protection](https://github.com/maida-ai/maida-assert#add-the-merge-boundary). Keep ordinary correctness tests and security review. Required checks must reflect the current PR head; insufficient evidence is not approval.

- [Gate skill](https://github.com/maida-ai/skills/tree/main/product/maida-add-regression-gate): prepare a repeatable task and reviewable CI setup.
- [Python workflow generation](cli/init.md#add-a-python-gate-to-github): for an existing traced entrypoint.
- [Action acceptance](https://github.com/maida-ai/maida-assert/blob/main/docs/acceptance.md): review an intentional change, then require fresh results.

## Integrate another agent/framework

- [Integration overview](integrations.md): supported capture options and their coverage.
- [Claude Code](claude-code.md): automatic setup, recovery, and richer capture.
- [Python agent walkthrough](python-agent.md): install in the project environment and connect an entrypoint.
- [LangChain / LangGraph](integrations/langchain-langgraph.md) and [OpenAI Agents SDK](integrations/openai-agents.md): optional adapters.
- [CrewAI compatibility](integrations/crewai.md): retained adapter and unsupported installation path.
- [Langfuse import](langfuse.md): read existing runs into local Maida checks.
- [External emitter guide](reference/trace-emitter.md): write native evidence without an SDK.
- [Tutorials and examples](https://github.com/maida-ai/maida-tutorials): optional runnable practice; [all guides](guides/index.md).

## Reference

| Reference | Use it when you need |
|---|---|
| [CLI](cli.md) | Commands, options, output, run selection, and exit codes |
| [SDK](sdk.md) | Python decorators, contexts, and recorders |
| [Policy](reference/policy.md) | Requirements, metric kinds, and statistical semantics |
| [Trace format](reference/trace-format.md) | The versioned data contract and storage layout |
| [Configuration](reference/config.md) | Storage settings, redaction, and truncation |
| [Architecture](architecture.md) | Capture, schema, comparison, and viewer internals |
| [Privacy and optional usage counts](usage-ping.md) | Explicit consent and configured destinations |

## Documentation maintenance

This directory owns the documentation content published at [maida.ai/docs](https://maida.ai/docs/) from a pinned engine release. Edit content here with the behavior it describes. The website repository maintains its own presentation landing page.

[Calibration table](calibration-187.md) is an engine-only working document and is not published to the website.
