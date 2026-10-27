# Required docs - what every /feature run is checked against

`al-docs-scout` reads this file before every task, `al-plan-critic` treats an unclosed applicable
item as blocking, and `al-merge-gate` checks the result against it.

## Repository documents (always, when present)
`CLAUDE.md`, `CONTRIBUTING.md`, `GIT_CONVENTIONS.md`, `DECISIONS.md` / ADRs, `TECH_DEBT.md`,
`TESTING.md`, `docs/`, the README next to the changed code, the repo's `.claude/skills/*`.
Rules there are older and stronger than the general ones.

## Mandatory for every task
Not configured yet. Add one line per document: a link or path, and what exactly to check.
Agents skip this section while it says "Not configured".
<!--
- **Engineering checklist** - https://wiki.example.com/checklist - check every item against the
  plan; SPEC/PLAN state which items apply and how each is closed.
- **Domain rules** - docs/data-rules.md - validation, null handling, units, time zones; the plan
  cites rule numbers.
-->

## Where to search by topic
Connected doc tools (Notion, Confluence, Google Drive...) by the task's keywords; pages of past
issues on similar work carry review comments. Sources marked ⛔ in the topic map: link only.
