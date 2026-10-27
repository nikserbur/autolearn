# The learning loop

autolearn learns on two time scales:

- **Inside a run** (`/feature`): the verify gate, the critic, review refutation and the merge
  gate produce *facts* about what went wrong. `al-lessons-keeper` turns them into lessons for the
  repo and for the pipeline itself.
- **Across sessions**: a hook captures your feedback as you type; `/learn` turns it into
  preferences, style rules and workflow patterns; `/retro` turns escaped bugs into guards;
  `/skill-radar` turns repeated manual work into new skills or MCP servers.

## Signals

| Signal | Where it comes from | Why it is trustworthy |
|---|---|---|
| Merge-gate finding | `review/merge-gate-N.md`, `lessons.py signal merge_gate` | Found by a fresh Opus that never saw the plan: it is what every earlier stage missed |
| Refuted review finding | `review/round-N.md` (mode B) | A reviewer's false positive, confirmed by a second reviewer trying to break it |
| Confirmed review finding | same | A real defect that reached review |
| Gate block | `.current-task.state.json` | The verify command actually failed; no opinion involved |
| Critic REVISE | critic verdicts | The plan disagreed with the code or skipped a checklist item |
| Correction / rule / approval | `capture_feedback.py` -> `inbox/feedback.jsonl` | Your own words |
| Escaped bug | you, `/retro`, `escaped_bug` signals | Reality |
| Router miss | `knowledge/.router-log.tsv` | The knowledge base had no answer |

## From signal to behavior

1. **Lesson.** Written automatically, shown to you after the run (a git commit in the data
   directory), built only from your own messages and run artifacts. One imperative line +
   evidence + addressee, in the right file:
   `lessons/<repo>.md` (project lessons, false positives, escaped bugs, extra reviewers),
   `lessons/global.md` (your preferences and workflow patterns), `lessons/pipeline.md` (how
   the pipeline itself should run), `style/messages.md` (your voice). No evidence, no lesson.
2. **Consolidation.** Repeats bump a counter instead of duplicating; contradicted lessons move
   to the archive; a repo file stays under 60 lines. Short files get read; long ones get skimmed.
3. **Proposal.** At x2 (x3 for workflow patterns) `al-lessons-keeper` writes
   `proposals/<date>-<slug>.md` with `Status: pending`, the evidence and the exact change.
4. **Decision.** `/learn` walks you through pending proposals one by one: apply / edit /
   reject / later. Rejections are remembered so they are not proposed again.
5. **Guard.** The applied change makes the next run behave differently, and the session brief
   points every new session at it.

## Choosing the guard

The cheapest guard that would really have caught the problem wins:

1. a **regression test** (deterministic, lives with the code),
2. a **hook** (deterministic, lives with the harness),
3. a **rule** in a `CLAUDE.md` (cheap, but only as good as the model's attention),
4. a **prompt patch** for the one agent whose stage had the information and the mandate,
5. a **new specialised reviewer agent** or **new skill** (most expensive; only for a recurring
   class of defects or a routine you asked for three times).

## "Missed a bug? Add a reviewer."

`/retro` asks `al-escape-analyst` to fill this table for the bug:

| Stage | Had the info? | Asked to look? | Looked? | Why it missed |
|---|---|---|---|---|
| spec | | | | |
| plan Review Focus | | | | |
| plan-critic | | | | |
| red test | | | | |
| code-reviewer | | | | |
| merge-gate | | | | |
| CI | | | | |

The first stage with both the information and the mandate is where the guard goes. When the
answer is a new reviewer, the proposal contains the full agent file and a trigger, and
applying it adds a row to the repo's "Extra reviewers" table:

```markdown
| Agent | Trigger | Added | Evidence |
|---|---|---|---|
| al-tz-reviewer | src/reports/**, "datetime" | 2026-01-20 | escaped: naive vs aware timestamps |
```

From then on, `/feature` step 5 runs `lessons.py reviewers round-N.patch`, which matches path
globs and quoted keywords in added lines, and launches `al-tz-reviewer` next to the general
reviewer on every matching diff.

## Tuning the pipeline from its own statistics

`lessons/runs.tsv` gets a row per run. `lessons.py stats --repo <repo>` reads the last five and
suggests: lower the critic's effort when it never blocks, tighten the reviewer threshold when
refutations outnumber confirmations, add a planner lesson when the gate keeps blocking for one
reason, run `/retro` when the merge gate keeps finding what everyone upstream missed.
