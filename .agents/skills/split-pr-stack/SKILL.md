---
name: split-pr-stack
description: Split a large GitHub PR or oversized commit into smaller atomic commits while preserving the same PR branch or stack. Use when the user asks to break up a large PR, split one commit into multiple commits, make a stacked PR more reviewable, understand PR scope from the PR description, review comments, and modified files, identify out-of-scope or missing promised behavior, preserve commit order while rewriting history, create a split plan, avoid broad staging, validate the rewritten stack, and never push rewritten history without explicit instruction.
---

# Split PR Stack

Use this skill to split oversized PR changes into reviewable atomic commits on the same branch or PR stack.

## Resources

- `assets/split-plan-template.md`: copy this shape into `_split_report/` before rewriting history.
- `../commit-stack-review/scripts/get-review-comments`: reuse this helper to read PR review comments when the sibling `commit-stack-review` skill is present.

## Safety Invariants

- Require a clean working tree before history rewriting, or ask the user how to preserve dirty work.
- Create a backup branch before any reset or rebase.
- Do not push, force-push, drop commits, squash commits, or publish rewritten history unless the user explicitly asks.
- Keep the work in the same PR branch or stack unless the user asks for new branches.
- Use one interactive rebase over the affected stack range. Do not run separate rebases for each split.
- Never run `git add .`, `git add -A`, `git add --all`, `git add -u`, or `git commit -a`.
- Stage only explicit files or hunks that belong to the next atomic commit.
- Inspect `git diff --cached --name-only` and `git diff --cached` before every commit.
- Use editor-safe rebase continuation: `GIT_EDITOR=true git rebase --continue`.
- Keep `_split_report/` local and unstaged unless the user asks to include it in the PR.

## Atomicity Criteria

A split commit should have one clear purpose, a coherent message, and enough code/tests/docs to make sense on its own. Prefer commits like:

- preparatory refactor with no behavior change
- focused behavior change
- adapter/integration change
- regression tests or fixtures paired with the behavior they verify
- docs/examples only when they describe behavior already introduced

Avoid commits that mix unrelated cleanup, change public behavior without tests, or leave later commits unable to replay cleanly.

## Workflow

1. Identify the PR base and current stack range.
2. Read the PR title/body, review comments, modified files, aggregate diff, and current commit list.
3. Identify the PR's promised scope, implemented components, missing promised behavior, and out-of-scope behavior.
4. Report scope concerns to the user before rewriting if the PR does not do what it promises or does extra unrelated work.
5. Write a split plan in `_split_report/pr-split-<DATE>.md` using `assets/split-plan-template.md`.
6. Create a backup branch.
7. Choose the rewrite strategy: tip/uncommitted split, single historical commit split, or multi-commit stack split.
8. Build each atomic commit with explicit staging only.
9. Run targeted validation as commits are built when practical, then broader validation after the rewrite.
10. Run `git range-diff "$BACKUP_BRANCH"...HEAD` and inspect the final log.
11. Summarize the new commit sequence, scope findings, validation, backup branch, report path, and that no push was performed.

## Discover Context

Infer PR context when useful:

```bash
OWNER="$(gh repo view --json owner --jq '.owner.login')"
REPO="$(gh repo view --json name --jq '.name')"
PR="$(gh pr view --json number --jq '.number')"
BASE_REF="$(gh pr view "$PR" --repo "$OWNER/$REPO" --json baseRefName --jq '.baseRefName')"
git fetch origin "$BASE_REF"
BASE="$(git merge-base HEAD "origin/$BASE_REF")"
```

Inspect the current stack:

```bash
git status --porcelain
git log --oneline --decorate "$BASE"..HEAD
git diff --stat "$BASE"..HEAD
git diff --name-status "$BASE"..HEAD
```

Stop if the working tree is dirty and the user has not directed how to preserve it.

## Understand PR Scope

Do this before planning the split. The agent must understand the PR as a product/code change, not only as a pile of hunks.

Read the PR title and body:

```bash
gh pr view "$PR" --repo "$OWNER/$REPO" --json title,body,author,baseRefName,headRefName,url
```

Read review comments with the sibling `commit-stack-review` helper when available:

```bash
mkdir -p _split_report
REVIEW_HELPER=".agents/skills/commit-stack-review/scripts/get-review-comments"
COMMENTS_JSON="_split_report/pr-${PR}-comments-$(date +%Y%m%d).json"
if [ -x "$REVIEW_HELPER" ]; then
  if ! "$REVIEW_HELPER" -o "$OWNER" -r "$REPO" -p "$PR" -u > "$COMMENTS_JSON" 2> "${COMMENTS_JSON}.err"; then
    gh pr view "$PR" --repo "$OWNER/$REPO" --comments > "_split_report/pr-${PR}-comments-$(date +%Y%m%d).txt"
  fi
else
  gh pr view "$PR" --repo "$OWNER/$REPO" --comments > "_split_report/pr-${PR}-comments-$(date +%Y%m%d).txt"
fi
```

If the helper fails because comments cannot be mapped safely, keep the error in the split report and continue scope analysis from PR body, files, and `gh pr view --comments`. Do not use unsafe comment-to-commit mapping as a split boundary.

Read the modified files and enough content to understand each component:

```bash
git diff --name-status "$BASE"..HEAD
git diff --stat "$BASE"..HEAD
git diff "$BASE"..HEAD -- path/to/relevant_file
```

For each changed file or obvious subsystem, inspect nearby source, tests, and docs so the split boundaries follow real components. Identify:

- promised behavior from the PR title/body
- components actually changed
- tests/docs/examples that support each component
- reviewer concerns that should influence split boundaries
- missing promised behavior
- behavior or cleanup outside the PR's stated scope

If the PR appears to under-deliver or overreach, report that before rewriting. Ask for direction when the mismatch changes what should be split, removed, or completed.

Create the backup branch before any reset or rebase:

```bash
BACKUP_BRANCH="backup/pr-${PR:-local}-before-split-$(date +%Y%m%d-%H%M%S)"
git branch "$BACKUP_BRANCH"
```

## Plan the Split

Create a local report:

```bash
mkdir -p _split_report
SPLIT_REPORT="_split_report/pr-split-$(date +%Y%m%d).md"
```

Use `assets/split-plan-template.md`. Record:

- backup branch
- base branch and merge base
- PR title/body summary
- review comment summary and comment-data source
- modified files grouped by component
- promised scope versus implemented behavior
- missing promised behavior and out-of-scope behavior
- original commits to split
- proposed new commit order and messages
- file/hunk ownership for each new commit
- validation plan
- risks, conflicts, or ambiguous boundaries

Do not rewrite history until the plan identifies which files or hunks belong to each new commit.

## Strategy A: Split Tip or Uncommitted Work

Use this when the oversized change is at the tip or still uncommitted.

For a tip commit:

```bash
ORIGINAL_SHA="$(git rev-parse HEAD)"
git reset --mixed HEAD^
```

For uncommitted work, skip the reset. Then repeat:

```bash
git status --short
git add path/to/file
git add -p path/to/file
git diff --cached --name-only
git diff --cached
git commit -m "focused commit message"
```

Use `git add -N path/to/new_file` before `git add -p` when interactively staging hunks from a new file.

## Strategy B: Split One Historical Commit

Use this when one commit inside the PR stack is too large.

Start one interactive rebase from the parent or merge base and mark the target commit as `edit`:

```bash
git rebase -i "$BASE"
```

If the environment cannot safely edit the todo list, print the target commits and ask the user to start the rebase manually.

At the edit stop:

```bash
ORIGINAL_SHA="$(git rev-parse HEAD)"
ORIGINAL_SUBJECT="$(git log -1 --format=%s)"
git reset --mixed HEAD^
```

Create the smaller commits with explicit staging:

```bash
git add path/to/relevant_file
git add -p path/to/another_file
git diff --cached --name-only
git diff --cached
git commit -m "new atomic subject"
```

Repeat until `git status --short` shows no leftover changes from the original commit, then continue:

```bash
GIT_EDITOR=true git rebase --continue
```

## Strategy C: Split Multiple Commits in the Stack

Use this when several commits need restructuring.

- Start one interactive rebase from `BASE`.
- Mark only commits that need splitting as `edit`.
- At each stop, use Strategy B for that commit.
- Keep later commits in their existing order unless the split plan explicitly requires a small local reorder.
- After each stop, continue with `GIT_EDITOR=true git rebase --continue`.

If later commits conflict because an earlier split changed boundaries, resolve by preserving the intended later-commit behavior. Avoid opportunistic cleanup during conflict resolution.

## Staging Rules

Prefer file-level staging when a whole file belongs to one atomic commit. Use hunk staging when a file contains multiple logical changes.

Before every commit:

```bash
git diff --cached --name-only
git diff --cached
```

If unrelated changes are staged:

```bash
git restore --staged path/to/unrelated_file
```

If a hunk cannot be staged cleanly, make a smaller working-tree edit, stage the exact file, commit it, then restore or continue with the remaining intended changes.

## Validation

Run the narrowest meaningful checks after risky splits when practical, then broader checks at the end. Use the repository's actual commands.

After the rewrite:

```bash
git status
git range-diff "$BACKUP_BRANCH"...HEAD
git log --oneline "$BASE"..HEAD
```

Regenerate or update the split report with final commit SHAs and validation results.

## Final Response

Report:

- backup branch name
- split report path
- original commit or PR range split
- final commit sequence
- validation commands run
- unresolved risks or manual-review points
- whether the rewrite completed
- that no push was performed
