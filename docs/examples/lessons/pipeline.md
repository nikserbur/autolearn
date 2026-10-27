# Pipeline lessons (/feature)

Maintained by `al-lessons-keeper`. The controller reads "Active pipeline rules" and "Extra
reviewers" at the start of every run. Proposals wait for the user's decision.

## Active pipeline rules
1. Once the strongest planner model hits a usage limit in a run, launch every later planner of
   that run on Opus directly. (example evidence: run 2026-01-10, limit hit twice)
2. Run the stage check with the same toolchain CI uses (e.g. the formatter version pinned in the
   repo); a different local toolchain passing is not "ok". (example evidence: merge-gate round 1)

## Extra reviewers
Reviewers that run in /feature step 5 on every repo when their trigger matches the diff.
Trigger: comma-separated path globs, or "quoted keywords" searched in added lines.
| Agent | Trigger | Added | Evidence |
|---|---|---|---|

## Prompt proposals
(awaiting the user's decision; the full text lives in ../proposals/)
