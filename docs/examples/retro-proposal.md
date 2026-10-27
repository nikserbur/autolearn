# al-tz-reviewer: catch naive vs tz-aware datetime mixing in reports
Status: pending
Repo: example-repo · Escaped: production (daily totals off by one day for UTC+ users) · Root stage: code review

<!-- Example of a proposal written by al-escape-analyst via /retro. Everything below is fictional. -->

## Evidence
- Fix commit `4f2c1e9` changes `src/reports/daily.py:88` from `datetime.now()` to `datetime.now(tz=UTC)`.
- Introduced in `b71d0aa` by run `runs/example-repo/2026-01-18-daily-report/`.
- `review/round-1.md`: al-code-reviewer PASS; `review/merge-gate-1.md`: MERGE. Neither mentions time zones.

## Stage analysis
| Stage | Had the info? | Asked to look? | Looked? | Why it missed |
|---|---|---|---|---|
| spec | no | - | - | "group by day" with no time-zone requirement |
| plan Review Focus | partly | no | no | five risks listed, none about time zones |
| al-plan-critic | yes | no | no | no checklist item for time handling |
| red test | yes | no | no | fixtures all at noon UTC, so the bug cannot show |
| al-code-reviewer | **yes** | **yes** ("wrong result on real inputs") | partly | general pass; mixing naive and aware datetimes is easy to miss |
| al-merge-gate | yes | yes | no | same blind spot |

First stage with information and mandate: **code review**. Upstream gap: the spec template has no time-zone line.

## Proposed guard(s)
1. **Regression test** `tests/reports/test_daily_tz.py`: a sale at 23:30 UTC-5 must count for that local day. Fails on `b71d0aa`, passes on `4f2c1e9`.
2. **New reviewer** `~/.claude/agents/al-tz-reviewer.md`:
   ```markdown
   ---
   name: al-tz-reviewer
   description: >
     Fresh-context reviewer for time handling in a diff: naive vs aware datetimes, implicit local time, day boundaries, DST. Called by /feature when its trigger matches.
   model: opus
   tools: Read, Grep, Glob, Bash
   ---
   Review only time handling in the diff. Flag: datetime.now()/utcnow() without tz, comparisons
   between naive and aware values, date() of an aware value in the wrong zone, day buckets
   computed in server time, fixtures that cannot expose a day-boundary bug. Same threshold and
   output format as al-code-reviewer mode A.
   ```
   Trigger: `src/reports/**, "datetime"`.

## How to apply
Add the test; create the agent file; add a row to "Extra reviewers" in `lessons/example-repo.md`:
`| al-tz-reviewer | src/reports/**, "datetime" | 2026-01-20 | escaped: naive vs aware timestamps |`

## How we will know it works
The regression test fails on `b71d0aa`. A dry run `lessons.py reviewers` on the `b71d0aa` patch lists `al-tz-reviewer`.
