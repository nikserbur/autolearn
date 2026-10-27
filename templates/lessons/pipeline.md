# Pipeline lessons (/feature)

The controller reads "Active pipeline rules" and "Extra reviewers" at the start of every run.
Both change only when the user approves a proposal in /learn or /retro; `al-lessons-keeper`
drafts proposals and never edits these two sections itself.

## Active pipeline rules
<!-- example: 1. Run the stage check with the toolchain CI uses; a different local toolchain passing is not "ok". (evidence: merge-gate round 1, 2026-01-12) -->

## Extra reviewers
Reviewers that run in /feature step 5 on every repo when their trigger matches the diff.
Trigger: comma-separated path globs, or "quoted keywords" searched in added lines.
| Agent | Trigger | Added | Evidence |
|---|---|---|---|

## Prompt proposals
(awaiting the user's decision; the full text lives in ../proposals/)
