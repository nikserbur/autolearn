---
name: git-handoff
description: >
  For team repositories where agents must not change git state: inspects the working tree read-only and prints the exact git commands for the user to run - task branch, explicit paths to stage, and commit messages in the repo's convention - one block per repo, in dependency order. Use for "give me the git commands", "how do I commit this", "git handoff", or at the end of /feature in a team repo. Never runs state-changing git.
argument-hint: "[ISSUE-KEY] [type]"
---

# git-handoff - the agent writes the commands, the human runs them

Arguments: `$ARGUMENTS`.

## Boundary (hard)
Read-only git only: `status`, `diff`, `log`, `rev-parse`, `branch --list`, `remote -v`. Never
`add`, `commit`, `switch`, `merge`, `rebase`, `push` - even if the user says "just commit it":
you emit commands, they run them.

## Procedure
1. **Repos with changes**: the current repo, plus sibling repos the task touched
   (`git -C <dir> status --porcelain`).
2. **Conventions**: GIT_CONVENTIONS.md, CONTRIBUTING.md, CLAUDE.md (branch pattern, commit
   format, allowed types, whether `Co-Authored-By` lines are allowed, issue key placement). No
   convention found -> Conventional Commits and say so.
3. **Group** the diff into commits: one logical change per commit; split only genuinely
   separate concerns.
4. **Branch**: on a permanent branch (`main`, `master`, `develop`) -> propose
   a branch per the convention (default: `<type>/<key>-<short-description>`); ask for the issue key if it is
   mandatory and unknown. On a task branch -> reuse it.
5. **Classify** type and scope from the changed paths. A breaking change to a public interface,
   config or behavior gets the convention's breaking marker. Ambiguous (fix vs breaking fix) ->
   state the fork and ask.
6. **Emit** one block per repo, dependency order first (the library before its consumer). Each
   block starts with `cd <repo>` so the user sees where they are; no `git -C`:

```bash
cd <repo>
git switch -c <type>/<key>-<desc>    # only when on a permanent branch
git add <explicit paths>             # never "git add -A" in a team repo
git commit -m "<type>(<scope>): <description>" -m "Refs: <KEY>"
# when reviewed and ready:
# git push -u origin <type>/<key>-<desc>
```

7. After the blocks: one line on what each commit contains, and what was deliberately left
   unstaged (scratch files, local config).
