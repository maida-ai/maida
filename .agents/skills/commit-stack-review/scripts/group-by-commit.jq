def fail($message): error("get-review-comments: \($message)");

.[0].data.repository.pullRequest as $pr

| if ($pr.commits.totalCount // 0) > ($pr.commits.nodes | length) then
    fail("PR has \($pr.commits.totalCount) commits, but this helper fetched \($pr.commits.nodes | length); commit pagination is not yet supported")
  elif any($pr.reviewThreads.nodes[]?; ((.comments.totalCount // 0) > (.comments.nodes | length))) then
    fail("at least one review thread has more comments than fetched; nested comment pagination is not yet supported")
  else
    .
  end

# Current PR commits, in the same order GitHub shows them.
| (
    $pr.commits.nodes
    | to_entries
    | map({
        oid: .value.commit.oid,
        short: .value.commit.abbreviatedOid,
        subject: .value.commit.messageHeadline,
        order: .key
      })
  ) as $current_commits

| (
    $current_commits
    | sort_by(.subject)
    | group_by(.subject)
    | map(select(length > 1) | .[0].subject)
  ) as $duplicate_subjects

| if $allow_subject_mapping and ($duplicate_subjects | length) > 0 then
    fail("commit subjects are not unique: \($duplicate_subjects | join(", "))")
  else
    .
  end

# SHA -> current commit metadata.
| (
    $current_commits
    | map({
        key: .oid,
        value: .
      })
    | from_entries
  ) as $by_oid

# messageHeadline -> current commit metadata.
# Used only when subject mapping is explicitly enabled and subjects are unique.
| (
    $current_commits
    | map({
        key: .subject,
        value: .
      })
    | from_entries
  ) as $by_subject

# Flatten all review comments.
| [
    .[]
    | .data.repository.pullRequest.reviewThreads.nodes[]?
    | . as $thread
    | $thread.comments.nodes[]?
    | {
        original_commit: .originalCommit.oid,
        original_short_commit: .originalCommit.abbreviatedOid,
        original_subject: .originalCommit.messageHeadline,

        current_commit: .commit.oid,
        current_short_commit: .commit.abbreviatedOid,
        current_subject: .commit.messageHeadline,

        # Resolve to current PR-stack commit metadata.
        resolved_commit: (
          if $by_oid[.originalCommit.oid] != null then
            $by_oid[.originalCommit.oid]
          elif $by_oid[.commit.oid] != null then
            $by_oid[.commit.oid]
          elif $allow_subject_mapping and $by_subject[.originalCommit.messageHeadline] != null then
            $by_subject[.originalCommit.messageHeadline]
          elif $allow_subject_mapping and $by_subject[.commit.messageHeadline] != null then
            $by_subject[.commit.messageHeadline]
          else
            null
          end
        ),

        mapping_reason: (
          if $by_oid[.originalCommit.oid] != null then
            "original_sha"
          elif $by_oid[.commit.oid] != null then
            "current_sha"
          elif $allow_subject_mapping and $by_subject[.originalCommit.messageHeadline] != null then
            "original_subject"
          elif $allow_subject_mapping and $by_subject[.commit.messageHeadline] != null then
            "current_subject"
          else
            "unmatched"
          end
        ),

        thread_id: $thread.id,
        comment_id: .id,
        author: .author.login,
        createdAt,
        path: (.path // $thread.path),
        line: (.line // .originalLine // $thread.line),
        isResolved: $thread.isResolved,
        isOutdated: $thread.isOutdated,
        url,
        body: .bodyText
      }
  ]

# Add normalized report fields.
| map(
    . + {
      report_commit: (
        if .resolved_commit != null then
          .resolved_commit.oid
        else
          (.original_commit // .current_commit)
        end
      ),
      report_short_commit: (
        if .resolved_commit != null then
          .resolved_commit.short
        else
          (.original_short_commit // .current_short_commit)
        end
      ),
      report_subject: (
        if .resolved_commit != null then
          .resolved_commit.subject
        else
          (.original_subject // .current_subject)
        end
      ),
      commit_order: (
        if .resolved_commit != null then
          .resolved_commit.order
        else
          999999
        end
      ),
      is_current_commit: (.resolved_commit != null)
    }
  )

# group_by requires sorting by the grouping key first.
| sort_by(.report_commit, .path, (.line // 0), .createdAt)

# Group by resolved/current report commit.
| group_by(.report_commit)

# Build report groups.
| map({
    commit: .[0].report_commit,
    short_commit: .[0].report_short_commit,
    subject: .[0].report_subject,
    commit_order: .[0].commit_order,
    is_current_commit: .[0].is_current_commit,
    mapping_reasons: ([.[].mapping_reason] | unique),
    count: length,
    comments: .
  })

# Sort groups by GitHub PR commit order.
| sort_by(.commit_order, .short_commit)
