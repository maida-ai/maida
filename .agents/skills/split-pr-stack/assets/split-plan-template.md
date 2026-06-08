# PR Split Plan

Generated: <DATE>
Repository: <OWNER>/<REPO>
PR: <PR_NUMBER>
Base branch: <BASE_REF>
Merge base: <BASE_SHA>
Backup branch: <BACKUP_BRANCH>
PR URL: <PR_URL>

## Goal

<Why this PR or commit is being split.>

## PR Scope Understanding

PR title/body promise:

```text
<summary of what the PR says it does>
```

Review comments source:

```text
<commit-stack-review helper JSON path or gh comments fallback path>
```

Reviewer concerns that affect split boundaries:

- <comment summary and affected component>

Modified components:

| Component | Files | Purpose | Tests/docs |
|---|---|---|---|
| <component> | <paths> | <purpose> | <paths/checks> |

Scope check:

- Promised and implemented: <items>
- Promised but missing: <items or none>
- Implemented but out of scope: <items or none>
- User decision needed before splitting: <items or none>

## Original Range

```text
<git log --oneline BASE..HEAD or target commit>
```

## Proposed Commit Sequence

| Order | Subject | Purpose | Files / hunks | Validation |
|---:|---|---|---|---|
| 1 | <subject> | <purpose> | <paths or hunk notes> | <test/check> |

## Rewrite Strategy

<Tip split | single historical commit split | multi-commit stack split>

## Risks

- <Conflict, hunk-boundary, dependency, or test risk>

## Final Results

| Old Commit | New Commit(s) | Notes |
|---|---|---|

Validation run:

```text
<commands and result summary>
```
