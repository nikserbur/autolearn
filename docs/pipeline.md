# The /feature pipeline

`/feature [deep|light] <ISSUE-KEY or description>`. The session is the **controller**: it
writes the spec with you, launches agents, keeps `PROGRESS.md`, and never writes code itself.

## Why these roles

| Principle | How it shows up |
|---|---|
| Expensive models think, cheap models type | Opus plans, critiques and reviews; Sonnet scouts, implements and writes tests |
| Fresh context finds what anchored context misses | critic, reviewer, refuter and merge gate never see the author's reasoning |
| Claims are checked by running things | the verify gate runs the task's tests on every attempt to finish |
| Recon is cheaper than planning | `al-docs-scout` and `al-code-scout` hand the planner a map, so it spends turns on decisions |
| Findings are tried, not trusted | mode B reviewers try to refute each finding; refuted ones are logged, not fixed |
| The lead's view is the bar | `al-merge-gate` gets only the repo, base and issue key, the way a reviewer would |

## Steps

0. **Prepare**: repo root, team-repo detection (agents do no git writes there), artifacts
   folder outside the repo, BASE commit, lessons and extra reviewers loaded.
1. **docs-scout** -> `CONTEXT.md` (≤ 8 KB) from the topic map, required docs, repo docs, doc
   MCPs.
2. **Spec** with you (`SPEC.md`), then an automatic scale estimate: files, risk, open questions,
   shared modules, test coverage -> no pipeline / light / deep.
3. **code-scout** -> `CODEMAP.md` (≤ 10 KB).
4. **planner** -> `PLAN.md`. Deep: strongest model, falls back to Opus on a usage limit. Large
   tasks: skeleton of stages first, details right before each stage.
5. **plan-critic** -> PASS / REVISE. You approve the plan; no code before that.
6. **implementer** per task under the verify gate: `.current-task.json` names the command;
   `verify_gate.py` blocks the agent from finishing until it passes (or releases it as FAILED
   after `max_blocks`).
7. **Review**: `al-code-reviewer` mode A + learned extra reviewers whose triggers match the patch;
   refutation in mode B; confirmed findings go back to the implementer.
8. **test-writer**, **doc-writer**, full test suite.
9. **merge-gate**, fresh Opus at max effort: MERGE / AFTER FIXES / DO NOT MERGE. Fixes, then a
   *new* merge gate.
10. **lessons-keeper** (always in deep, on signals in light) -> lessons, proposals, stats.
11. **Wrap-up**: outcome in `PROGRESS.md`, new topic-map rows, `/git-handoff` or a final commit.

## Modes at a glance

| | light | deep |
|---|---|---|
| Planner | Opus, effort medium, read-only, ≤ 3 tasks | strongest, effort high, ≤ 3 experiments of 60 s, no network |
| Critique rounds | 1 | ≤ 3, or two PASS in a row at high risk (≤ 5) |
| Review rounds | 1; refutation if > 3 findings | ≤ 3; every finding refuted; third round by an Opus implementer |
| Gate `max_blocks` | 2 | 3 |
| Merge-gate rounds | ≤ 2 | ≤ 3 |
| Lessons | on signals | always |

## Artifacts

```text
~/.claude/autolearn/runs/<repo>/<YYYY-MM-DD>-<slug>/
├── SPEC.md  CONTEXT.md  CODEMAP.md  PLAN.md  PROGRESS.md
└── review/  round-N.patch  round-N.md  merge-gate-N.md
```

Lost the thread after a compaction? The controller re-reads `PROGRESS.md`.
