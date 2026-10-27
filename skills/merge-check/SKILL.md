---
name: merge-check
description: >
  Pre-review of a merge/pull request before you send it: a fresh Opus at max effort with none of the current session's context answers "can this be merged?" - blockers, what to fix before review, what can stay. Use when the user asks "can we merge?", "is it ready for review?", "check the PR before review", "merge check", or before you claim a PR is ready. /merge-check [ISSUE-KEY] [repo path ...] [base].
argument-hint: "[ISSUE-KEY] [repo ...] [base-branch]"
---

# /merge-check - "can this be merged?" through the lead's eyes

Arguments: `$ARGUMENTS`.

1. Find the PR's repos: from the arguments, otherwise the current one
   (`git rev-parse --show-toplevel`). A task split into two PRs -> both. Base branch: from the
   argument, otherwise `main` or `master` (whichever exists), otherwise ask. Issue key: from
   the argument or the branch name (`feature/proj-123-...` -> PROJ-123).
2. Agent -> `al-merge-gate`, one per repo, in parallel. The brief is **minimal**: repo path, base,
   issue key, the second repo if any. Pass nothing from this session: no plan, no conclusions,
   no "already fixed". The value of the check is the clean look.
3. Show the user per PR: verdict, blockers, "fix before review", "can stay" (short), which
   checks ran.
4. Do not start fixing on your own: ask what to fix. After fixes, re-check with a **new**
   `al-merge-gate`, never a continuation of the old one.
5. Anything merge-gate found is a learning signal. Queue one line per blocker or "fix before
   review" item: `python3 "${CLAUDE_SKILL_DIR}/../learn/scripts/lessons.py" signal merge_gate "<finding, one line>"
   --project <repo>`, so the next /learn asks why the
   earlier stages missed it.
