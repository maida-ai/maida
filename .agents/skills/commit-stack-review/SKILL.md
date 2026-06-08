---
name: commit-stack-review
description: Address GitHub pull request review comments per atomic commit in a stacked or multi-commit PR. Use when the user wants to preserve commit structure while fixing PR review comments, run one safe interactive rebase over the PR stack, group comments by commit with the bundled GitHub helper, maintain a local review-resolution report, draft optional replies, avoid broad staging, and never push rewritten history without explicit instruction.
---

# Commit Stack Review

Use this skill to address GitHub PR review comments inside the commits where they belong while preserving an atomic commit stack.

## Resources

- `scripts/get-review-comments`: fetch grouped PR review comments as JSON. Invoke this by skill-relative path from the repository root.
- `scripts/pr-review-comments.graphql`: GraphQL query used by the helper.
- `scripts/group-by-commit.jq`: jq transformer used by the helper.
- `assets/review-report-template.md`: local report skeleton.
- `assets/reply-template.md`: optional reply patterns.

Do not reimplement the helper scripts unless they fail and the user asks for debugging.

## Safety Invariants

- Require a clean working tree before history rewriting, or ask the user how to preserve dirty work.
- Create a backup branch before rebasing.
- Use one interactive rebase from the PR merge base; do not run independent rebases per commit.
- Never squash, drop, reorder, or push commits unless the user explicitly asks.
- Never run `git add .`, `git add -A`, `git add --all`, `git add -u`, or `git commit -a`.
- Stage only explicit files relevant to the current review comment and current atomic commit.
- Keep `_review_report/` local and unstaged unless the user asks to include it in the PR.
- Never post GitHub replies automatically; draft optional replies in the report.
- Use editor-safe rebase continuation: `GIT_EDITOR=true git rebase --continue`.

## Workflow

1. Identify `OWNER`, `REPO`, `PR`, `BASE_REF`, and `BASE`.
2. Check `git status --porcelain`; stop on dirty state unless the user directs preservation.
3. Create `BACKUP_BRANCH="backup/pr-${PR}-before-review-fixes-$(date +%Y%m%d-%H%M%S)"` with `git branch "$BACKUP_BRANCH"`.
4. Extract review comments into `_review_report/pr-${PR}-comments-$(date +%Y%m%d).json`.
5. Create or update `_review_report/pr-${PR}-$(date +%Y%m%d).md` from `assets/review-report-template.md`.
6. Stop before rebasing if any actionable comment is unmatched or mapped only by unsafe subject assumptions.
7. Start one interactive rebase from `BASE` and mark only commented commits as `edit`.
8. At each stop, inspect the matching comments, make only commit-local fixes, stage explicit files, amend if needed, and continue with `GIT_EDITOR=true git rebase --continue`.
9. Resolve conflicts conservatively; after staging resolved files, continue with `GIT_EDITOR=true git rebase --continue`.
10. Run relevant validation, then `git range-diff "$BACKUP_BRANCH"...HEAD` and `git log --oneline "$BASE"..HEAD`.
11. Summarize the backup branch, report path, amended commits, validation, rebase status, and that no push was performed.

## Extract Comments

Infer missing PR context when needed:

```bash
OWNER="$(gh repo view --json owner --jq '.owner.login')"
REPO="$(gh repo view --json name --jq '.name')"
PR="$(gh pr view --json number --jq '.number')"
BASE_REF="$(gh pr view "$PR" --repo "$OWNER/$REPO" --json baseRefName --jq '.baseRefName')"
git fetch origin "$BASE_REF"
BASE="$(git merge-base HEAD "origin/$BASE_REF")"
```

Run the helper from the repository root using the skill path:

```bash
SKILL_DIR=".agents/skills/commit-stack-review"
mkdir -p _review_report
COMMENTS_JSON="_review_report/pr-${PR}-comments-$(date +%Y%m%d).json"
"$SKILL_DIR/scripts/get-review-comments" -o "$OWNER" -r "$REPO" -p "$PR" -u > "$COMMENTS_JSON"
```

The `-u` flag enables subject-based remapping after the helper verifies current PR commit subjects are unique. Without `-u`, comments map by SHA only. If the helper fails, report the command and error and do not start the rebase.

List commented commits:

```bash
jq -r '
  .[]
  | [.commit_order, .short_commit, .subject, .count, (.mapping_reasons | join(","))]
  | @tsv
' "$COMMENTS_JSON"
```

Stop before rebasing if any group has `is_current_commit: false`, `commit_order: 999999`, or `mapping_reasons` containing `unmatched`, unless the user provides a manual mapping.

## Report

Use `assets/review-report-template.md` as the report shape. For every review comment, set exactly one status:

- `resolved`: changed the relevant commit, or verified the code already satisfies the concern.
- `outdated`: the referenced code no longer exists, moved, or was superseded later in the stack.
- `need follow-up`: clarification, product judgment, security/legal review, or design approval is needed.
- `rejected`: intentionally not applied; include a concise technical rationale.

Use `assets/reply-template.md` only when a GitHub reply would be useful. Keep replies optional and do not post them without explicit user instruction.

## Rebase Stops

At each `edit` stop, identify the current commit:

```bash
CURRENT_SHA="$(git rev-parse HEAD)"
CURRENT_SHORT="$(git rev-parse --short HEAD)"
CURRENT_SUBJECT="$(git log -1 --format=%s)"
```

Find matching comments:

```bash
jq --arg sha "$CURRENT_SHA" --arg short "$CURRENT_SHORT" --arg subject "$CURRENT_SUBJECT" '
  .[]
  | select(.commit == $sha or (.short_commit | startswith($short)) or .subject == $subject)
' "$COMMENTS_JSON"
```

For each comment, inspect the referenced file and nearby code before deciding. If code changes are needed, stage explicit pathspecs only:

```bash
git status --short
git add path/to/relevant_file path/to/relevant_test
git diff --cached --name-only
git diff --cached
git commit --amend --no-edit
GIT_EDITOR=true git rebase --continue
```

If no code changes are needed, update only the local report and continue:

```bash
GIT_EDITOR=true git rebase --continue
```

During an `edit` stop, `git add` plus `git rebase --continue` does not include new changes in the stopped commit. Run `git commit --amend --no-edit` before continuing when files changed.

## Conflicts

Resolve conflicts by preserving the intended changes of the commit currently being replayed. Avoid unrelated cleanup. After resolving:

```bash
git add path/to/resolved_file
GIT_EDITOR=true git rebase --continue
```

If unsure, stop. Tell the user they can recover with `git rebase --abort` and that `BACKUP_BRANCH` still points to the pre-rebase state.

## Final Response

Report:

- backup branch name
- report path
- commits amended
- comment counts by status
- validation commands run
- whether the rebase completed
- that no push was performed

Prefer concise, evidence-backed status over a long narrative.
