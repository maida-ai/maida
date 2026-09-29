# `maida init`

**Requires Maida 0.6.0 or newer.** Check `maida init --help` for `--from-run` before following this page. The [getting-started guide](../getting-started.md) also routes older installations to compatible commands.

Initialize from a successful run of one real task. Maida proposes a small policy from what it observed, shows the source workflow and trace IDs, and waits for your explicit review before activating anything. It never invents a Python entrypoint or a forbidden tool.

## Observe one task

Capture and import your coding-agent task using the [coding-agent walkthrough](../getting-started.md), or run your existing [traced Python agent](../python-agent.md). Select the completed run in `maida list`, then:

```bash
maida init --from-run RUN_ID
```

`--from-run latest` explicitly chooses the latest stored run. Check the displayed workflow and trace ID: it might belong to another project or the demo. Repeat `--from-run` to include several successful observations with the same workflow name. Duplicate, incomplete, failed and mixed-workflow selections are rejected before any files are written.

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

The command checks one observed execution. A failing invariant exits `1`; a passing observed check exits `0`. A different tool order or step count is allowed unless you explicitly add those contracts. Investigate a failure with `maida view`; accept intentional baseline changes only after reviewing their reason and evidence.

## Add a Python gate to GitHub

For an existing traced Python entrypoint, add the workflow after reviewing the starter:

```bash
maida init --github --agent-script path/to/your_agent.py
```

The path must identify a real Python file inside this repository. The workflow uses the reviewed policy and baseline paths, installs declared project dependencies from `uv.lock`/`pyproject.toml` or `requirements.txt`, and runs the selected entrypoint in both the gate and the isolated acceptance-capture job. If your entrypoint needs optional dependency groups, system packages or runtime credentials, add those to the two execution jobs before enabling CI. The pinned Action's acceptance runner uses Python 3.12; verify your project's compatibility. Existing stored coding-agent captures remain a local gate; this option does not fabricate a reproducible coding-agent scenario.

Run the printed `maida run` command locally, commit the policy, baseline, review record and workflow, then require the explicit `Maida / agent-check` commit status with branches up to date and configure workflow review protection. For this combined acceptance listener, do not also require the `agent-check` job or `Maida statistical gate` named check: dispatch jobs run at the default-branch SHA, and a named check can remain attached to a suppressed bot-push suite. The named check and sticky report still provide evidence; the explicit status publishes the protected decision on the verified PR head. Read the [Action protection requirements](https://github.com/maida-ai/maida-assert#blocking-mode-and-required-repository-settings). Policies are evaluated from the trusted PR base. Introducing or changing `.maida/` needs separate explicit configuration acceptance; this scaffold does not bypass it. The write job remains data-only and never checks out or runs candidate code.

All Action components are pinned to the reviewed commit `8cba7033c01e89f7a3364a3f4b922b39d65fb6e3`. The scaffold uses `maida-ai/maida-assert@8cba7033c01e89f7a3364a3f4b922b39d65fb6e3` and `maida-ai/maida-assert/accept-command@8cba7033c01e89f7a3364a3f4b922b39d65fb6e3`; upgrading the pin is a separate reviewable change.

Use `--force` only after reviewing the files that will be replaced. Maida preflights all target files, refuses symlinked or escaping outputs, and preserves existing files by default. Plain `maida init` explains the next capture/selection step and writes nothing.

Exit codes: `0` draft or activation completed; `2` missing observations, invalid configuration or file collision; `10` unexpected internal failure.
