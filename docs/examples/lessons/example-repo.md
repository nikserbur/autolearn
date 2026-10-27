# Lessons: example-repo

Per-repo memory, written by `al-lessons-keeper` and `al-escape-analyst`, read by every /feature agent
working in this repo (rename the file to the repo's folder name). Keep it under 60 lines.

## Project lessons
- Plugins register through `register()` in the package `__init__`; without it import tests
  fail. Evidence: run 2026-01-12 task 3, gate x2. Addressee: planner, implementer. (x2) -> candidate for CLAUDE.md
- The verify command for `src/api/` is `make test-api`, not `pytest` (needs the DB fixture).
  Evidence: code-scout miss, run 2026-01-15. Addressee: planner.

## False positives
- Do not flag `except Exception` in `src/cli/main.py`: it is the top-level crash reporter by
  design (DECISIONS.md 2025-11). Evidence: refuted twice, runs 2026-01-12, 2026-01-15.

## Escaped bugs
- Timezone-naive timestamps compared with aware ones in `src/reports/` -> wrong daily totals in
  prod. Root stage: code review (had the diff, no checklist item). Guard: `al-tz-reviewer` +
  regression test. 2026-01-20.

## Extra reviewers
| Agent | Trigger | Added | Evidence |
|---|---|---|---|
| al-tz-reviewer | src/reports/**, "datetime" | 2026-01-20 | escaped: naive vs aware timestamps |

## Candidates for CLAUDE.md
- Plugins register through `register()` in `__init__` (x2).

## Archive
