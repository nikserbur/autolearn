---
name: feature
description: >
  Multi-agent delivery pipeline with model routing per role: spec -> plan -> critique -> red test and code under a verify gate -> fresh-context review -> merge gate -> lessons. Light mode by default (Opus plan, one critique round, Sonnet implementer, one Opus review); deep mode for risky work (strongest planner with experiments, several critique rounds, every finding cross-checked, mandatory lessons). Manual only: /feature PROJ-123, /feature "description", /feature deep PROJ-123.
argument-hint: "[deep|light] [ISSUE-KEY or task description]"
disable-model-invocation: true
---

# /feature - plan -> critique -> red test and code -> review -> merge gate -> lessons

You are the controller: you coordinate agents, keep PROGRESS.md and talk to the user.
**You do not write code, tests or docs yourself and you do not fix review findings.** Session
model: Opus recommended, Sonnet acceptable (then ask the user when in doubt).

Task: `$ARGUMENTS`.

Data directory: `~/.claude/autolearn/` (below: HOME).

## Modes

| | light (default) | deep (`deep` in the argument or Risk: high) |
|---|---|---|
| Recon | docs-scout + code-scout (Sonnet) | same |
| Planner | Opus, effort medium, read-only, <= 3 tasks, ~25 turns | strongest model (`fable`, falls back to `opus`), effort high, <= 3 experiments of 60 s without network, ~50 turns |
| Large task (2+ issues, > 6 files or > 6 tasks) | - | two-stage plan: stage skeleton -> stage details right before each stage |
| Plan critique | 1 round; planner fixes blockers, no re-critique | up to 3 rounds (5 at high risk, two PASS in a row) |
| Code review | 1 round, effort medium; cross-check findings only if > 3 | up to 3 rounds, effort high, every finding cross-checked |
| Extra reviewers (learned) | those whose trigger matches the diff | same |
| Implementer gate | max_blocks 2 | max_blocks 3 |
| Merge gate (Opus max) | mandatory; up to 2 rounds | mandatory; up to 3 rounds |
| Lessons | only on signals (step 7) | always |

A one-sentence task touching 1-2 files: say even light is overkill and offer to do it directly.

## 0. Preparation

1. Repo root: `git rev-parse --show-toplevel` = working folder. If the folder is a directory of
   repos, ask which one and ask the user to open the session there (otherwise its CLAUDE.md is
   not loaded). Stop here until then.
2. Git mode. **Team repo** (has GIT_CONVENTIONS.md, its CLAUDE.md forbids agent git writes, or
   it is listed under "Team repositories" in the user's global CLAUDE.md): agents run no
   state-changing git; at the end use `/git-handoff`. Otherwise: commit after each task.
3. Issue key -> read the issue through the tracker MCP if one is connected.
4. Artifacts live **outside the repo**: `RUN=HOME/runs/<repo-name>/<YYYY-MM-DD>-<slug>/` with
   `SPEC.md`, `PLAN.md`, `PROGRESS.md` ([PROGRESS-TEMPLATE.md](PROGRESS-TEMPLATE.md)), `review/`.
   Inside the repo the pipeline creates only code, tests and docs.
5. BASE: `git rev-parse HEAD` -> PROGRESS.md. Dirty working tree -> warn the user.
6. Lessons: `LESSONS=HOME/lessons/<repo-name>.md`, `PIPELINE=HOME/lessons/pipeline.md`,
   `GLOBAL=HOME/lessons/global.md`. Read "Active pipeline rules" from PIPELINE and the
   "Extra reviewers" table from LESSONS and PIPELINE, if present. Pass LESSONS to every agent.
7. The mode is decided after the spec (step 1), automatically.

## 1a. Documentary context (docs-scout, Sonnet)

Right after preparation, before the spec. Agent -> `al-docs-scout`. Brief: the task topic (issue
key, text, feature and module names), the path `HOME/knowledge/required-docs.md`, where to write
`$RUN/CONTEXT.md`, LESSONS. It routes through the topic map, the repo docs and connected doc
tools and writes the applicable items into CONTEXT.md. You read only CONTEXT.md. Both modes; in
light, one search iteration.

**CONTEXT.md limit: 8 KB.** Check `wc -c $RUN/CONTEXT.md`. Over -> SendMessage to the same
`al-docs-scout`: "compress to 8 KB: applicable items one line each plus links, no page retelling".
The planner and critic re-read this file every round; extra kilobytes are expensive there.

## 1. Specification (you + the user)

`SPEC.md` from [SPEC-TEMPLATE.md](SPEC-TEMPLATE.md), up to ~80 lines. Unclear -> AskUserQuestion,
at most four questions. Mandatory: "Out of scope", "Definition of done", **Risk** and **Sources**
(items of the mandatory docs from CONTEXT.md that apply).

### Automatic scale estimate (you, no agents, a couple of minutes at most)

`deep` or `light` in the argument wins; skip the estimate. Otherwise collect signals with quick
grep/glob, without planning the implementation:

| Signal | no pipeline | light | deep |
|---|---|---|---|
| Files to change (estimate from entry points) | <= 2 | 3-6 | > 6 or unclear |
| Risk from the spec | low | low | high |
| Open questions after the spec | 0 | <= 2 | > 2 |
| Touches a shared module (base classes, schemas, auth, money, migrations) | no | no | yes |
| Tests exist in the affected area | yes | yes | no |

Rule: any signal in the deep column -> deep; all in "no pipeline" -> offer to do it directly;
otherwise light. Write the mode and signals into PROGRESS.md in one line and tell the user:
"Light mode: 4 files, low risk, tests exist". The user may overrule; that is their call. Do not
ask separately: the mode question goes together with plan approval in step 3.

## 1b. Code map (code-scout, Sonnet)

After SPEC.md and the estimate, before the plan. Agent -> `al-code-scout`: paths to SPEC.md,
`$RUN/CODEMAP.md`, the repo root. It runs nothing and returns CODEMAP.md up to 10 KB: entry
points, signatures, tests, verify commands. The planner starts from it.

## 2. Plan

Before each planner launch reset the experiment counter: `rm -f HOME/runs/.planner-runs-*`.

Agent -> `al-planner`. Brief: mode (and stage), paths to SPEC.md, CODEMAP.md, CONTEXT.md, PLAN.md,
LESSONS, [PLAN-TEMPLATE.md](PLAN-TEMPLATE.md), the issue key. Do not add your own implementation
ideas.

**Deep model: strongest with automatic fallback.** Pass `model: fable`, `effort: high`. If the
agent returns a usage-limit message or ends without a single tool call and without PLAN.md,
relaunch immediately with `model: opus`, `effort: high`, without asking the user. Record
"fable unavailable -> opus" in PROGRESS.md. Once it happened in a run, use Opus for the rest.

**Large task -> two-stage plan.** Signals: two or more issues in the argument, estimate > 6 files,
or the planner returned > 6 tasks. Then:
- **Stage A.** Planner in `skeleton` mode: 2-4 stages, each with a goal, <= 5 one-line tasks,
  what it hands over, the stage check. One critique round (as light). The user approves.
- **Stage B, per stage in order.** A new `al-planner` in `detail N` mode (deep model, reset the
  counter first) appends stage N's tasks to PLAN.md. One critique round on that section. A
  one-line approval from the user. Then steps 4-5 for stage N's tasks only. Then the next stage.
- Steps 6-8 (tests, final gate, lessons, wrap-up) run once at the end.

If the planner ran out of `maxTurns` with an incomplete PLAN.md, do not restart it: SendMessage
to the same agent "finish PLAN.md from what you already know, no new reads", or switch to the
two-stage mode.

## 3. Plan critique

Agent -> `al-plan-critic` (deep: `effort: high`): mode, SPEC.md, CONTEXT.md, PLAN.md, LESSONS.
- **light:** REVISE -> blocking findings go to the planner (SendMessage to the same `al-planner`),
  it fixes them, no re-critique. Non-blocking ones only go to PROGRESS.md.
- **deep:** rounds until PASS; low risk <= 3 rounds, high risk two PASS in a row (the second by a
  new `al-plan-critic`), <= 5 rounds. Limit reached -> the rest goes to the user.

Show the user: mode and why, goal, number of tasks, files, open questions, verdict.
AskUserQuestion: approve / amend / change mode. **No code without approval.** If the plan came out
bigger than the estimate (more than three tasks or six files), switch to deep yourself and say so.

## 4. Implementation (implementer, Sonnet) - one task at a time, under the gate

1. Before launching, write `HOME/runs/<repo-name>/.current-task.json`:
   `{"task": "<title>", "verify": "<task verify command>", "max_blocks": 2, "timeout": 600,
   "created": <date +%s>}` (deep: 3). The verify command must be the one in the approved
   PLAN.md; the gate ignores task files older than 6 hours. The agent's hook runs `verify` on every attempt to finish and will not let go
   until it passes. `verify` = the task's tests, not the whole suite (that runs once in step 6).
2. A new `al-implementer` per task. Brief: the full task text, PLAN.md, LESSONS, git mode, notes from
   previous implementers. Never in parallel.
3. When it returns, read `.current-task.state.json`: `passed: false` -> FAILED, whatever the agent
   wrote. Delete both `.current-task*.json`.
4. DONE requires a red and a green run (or "TDD: no"). FAILED/BLOCKED -> do not fix it yourself:
   retry once with a sharper brief, split the task, or ask the user.
5. A PROGRESS.md row after each task. Commit if the git mode allows.

## 5. Code review

1. `git diff <BASE> > $RUN/review/round-N.patch`.
2. Agent -> `al-code-reviewer` mode A (deep: `effort: high`): patch, PLAN.md, SPEC.md, CONTEXT.md,
   LESSONS. Do not pass your own opinions.
3. **Extra reviewers (learned).** Run
   `python3 "${CLAUDE_SKILL_DIR}/../learn/scripts/lessons.py" reviewers $RUN/review/round-N.patch --repo <repo-name>`
  . It reads the "Extra reviewers" tables of LESSONS
   and PIPELINE and prints every agent whose trigger (path glob, or a quoted keyword in added
   lines) matches the patch. Launch each in parallel with the same inputs. These agents were added by `/retro` after a bug escaped; they
   exist to make sure it does not happen twice.
4. Cross-check with mode B ("refute it", a separate `al-code-reviewer` per finding, in parallel):
   deep always; light only if there are > 3 findings. Refuted findings are dropped with the
   reason in `$RUN/review/round-N.md`.
5. Confirmed findings -> the implementer (SendMessage to whoever did the task; write
   `.current-task.json` again). light: one round of fixes, no re-review unless a fix touched files
   outside the task. deep: up to 3 rounds; the third by a new `al-implementer` with `model: opus`.
6. A PROGRESS.md row per round. Silently dropping a finding is not allowed.

## 6. Tests, docs, final gate

1. Uncovered Review Focus items -> `al-test-writer` under the gate (`verify` = new tests + full
   suite). In light, only if Review Focus is not covered at all.
2. "Documentation" in PLAN.md is not "not required" -> `al-doc-writer`.
3. Final gate, yourself: the full test suite. Result -> PROGRESS.md. Red -> back to step 5.

## 6b. Fresh-eyes pre-review (merge-gate, Opus max) - mandatory

The same thing the lead will do: a new Opus with no context is asked "can this be merged?". We
do it ourselves before handing over for review.

1. Agent -> `al-merge-gate`. Brief **minimal, like the lead's**: repo path, base branch
   (`main`/`master` or BASE), issue key; a second repo if the task has two MRs. **Do not pass**
   PLAN.md, SPEC.md, PROGRESS.md, your opinions or past review results: the fresh look is
   valuable exactly because it does not know the intent.
2. Save the answer to `$RUN/review/merge-gate-N.md`.
3. VERDICT "MERGE" and no "Fix before review" items -> step 7.
4. Otherwise: blockers and "Fix before review" -> `al-implementer` under the gate (as review fixes
   in step 5, one item or one group per file). "Can stay" -> PROGRESS.md and the PR description
   as known limitations, no edits. Disagree with an item? Do not drop it silently: show the user
   with your argument.
5. After fixes: a **new** `al-merge-gate` (never SendMessage to the old one: it is anchored).
   light: up to 2 rounds, deep: up to 3.
6. Limit reached with blockers left -> list for the user. No review without their decision.
7. A PROGRESS.md row per round: verdict, blockers, "fix before review", fixed.

## 7. Lessons (lessons-keeper, Opus)

- Before deciding, `python3 "${CLAUDE_SKILL_DIR}/../learn/scripts/lessons.py" stats --repo <repo-name>` prints the
  last runs of this repo with tuning hints; pass them to lessons-keeper.
- **light:** only on signals: the gate blocked, findings were refuted or confirmed, the critic
  said REVISE, FAILED or BLOCKED. No signals -> one row in `HOME/lessons/runs.tsv` by hand.
- **deep:** always.

Merge-gate findings are the main signal: they are what the planner, critic and reviewer missed.
If merge-gate found anything, lessons run in light mode too.

Agent -> `al-lessons-keeper` in `run` mode: PROGRESS.md, review/ (including merge-gate-*.md),
SPEC.md, PLAN.md, the patch, LESSONS, PIPELINE, runs.tsv. Show the user what it wrote:
`git -C HOME show --stat <commit>` plus the added lesson lines (`git -C HOME show <commit> -- lessons`).
Whatever it proposes (CLAUDE.md candidates, prompt patches, controller rules, new reviewers,
settings) - list it, **do not apply it yourself**; it waits for `/learn`.
"Yes" to an item -> apply that change and mark the proposal `Status: applied`; a team repo's
CLAUDE.md only as text for an MR.

## 8. Wrap-up

**Review only after the merge-gate verdict "MERGE"** (or the user's explicit decision on the
remaining items). Fill "Outcome" in PROGRESS.md. If docs-scout returned "New for the topic map",
append those rows to the right topic in `HOME/knowledge/topic-map.md` and commit there. Project
git: team repo -> `/git-handoff`, otherwise a final commit. Report: mode, link to PROGRESS.md,
tasks, verdicts, final gate, what is still open. Do not retell the plan.

## Economy rules

- Each agent's model and effort live in its own file; override only where this skill says so (deep).
- Briefs carry paths and facts; never paste file contents.
- Lost the thread after compaction? Read PROGRESS.md.
