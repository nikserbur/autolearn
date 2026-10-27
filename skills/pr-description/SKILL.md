---
name: pr-description
description: >
  Writes the title and description of a pull/merge request for the current branch: what changed and why, how it was verified, known limitations, and the issue link with the closing keyword only in the last PR of a multi-PR task. Follows the repo's PR template and CONTRIBUTING rules. Use for "describe the PR", "PR description", "MR description", "write the merge request text". Read-only: prints text to paste, never creates or edits a PR.
argument-hint: "[base-branch] [ISSUE-KEY] [last|intermediate]"
---

# pr-description - what and why, ready to paste

Arguments: `$ARGUMENTS`.

1. **Read-only git**: current branch, base (argument, else `main`/`master`),
   `git log --oneline <base>..HEAD`, `git diff --stat <base>...HEAD`, then the diff of the files
   that matter. Uncommitted changes -> mention that they are not in the PR yet.
2. **Repo rules**: `.github/pull_request_template.md` / `.gitlab/merge_request_templates/`,
   CONTRIBUTING.md, GIT_CONVENTIONS.md, CLAUDE.md. The template's headings win over the
   defaults below.
3. **Context**: the issue (tracker MCP if connected), PROGRESS.md of the matching run in
   `~/.claude/autolearn/runs/<repo>/` if one exists (verification results, merge-gate "can stay"
   items = known limitations).
4. **Write**:
   - **Title**: the repo's commit/PR convention (e.g. Conventional Commits `type(scope): summary`), else a
     plain imperative summary under 72 characters.
   - **Description** (default headings): *What* (behavior change, user-visible first) ·
     *Why* (the problem, link to the issue) · *How* (only the non-obvious design decisions) ·
     *Verification* (commands run and their result lines) · *Known limitations* · *Follow-ups*.
   - Issue link: closing keyword (`Closes KEY`) **only** when this is the last PR of the task;
     intermediate PRs reference the key without closing it. Unsure -> ask.
5. Print the title and the description in separate code blocks. Nothing is created or pushed.
