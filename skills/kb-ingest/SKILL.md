---
name: kb-ingest
description: >
  Builds the local knowledge base and its router at the same time. Use when the user says "remember this", "add to the knowledge base", "build a page for this repo", after reading docs, wiki pages, code or tickets that will matter again, when kb-router reported a miss, or when docs-scout returned "New for the topic map". Distills sources into topic and project pages with a source on every fact, registers them in the topic map with keywords and section pointers, keeps every file inside its size budget, and commits.
argument-hint: "[repo | <url or path> | <topic>]"
---

# kb-ingest - every page it writes is routable from day one

Target: `~/.claude/autolearn/knowledge/` (below: KB). Layout and the topic-block format are
described in the kb-router skill. The rule of this skill: **no page without a router entry, no
router entry without a page or a source.**

Argument: `$ARGUMENTS`
- `repo` (or empty inside a git repo without a project page) -> build `KB/projects/<repo>.md`.
- a URL or path -> distill that source into the matching topic.
- a topic name -> collect what this conversation established about it.

## 1. Read (cheaply)
- Small source (< ~30 KB): read it directly.
- Large source, many pages, or a whole repo: delegate to a subagent (`Explore` for code,
  `al-docs-scout` for docs) with the question "what will someone need to know again?" and take back
  facts with citations. Do not pull the whole thing into the main context.
- Pages the map marks ⛔: do not crawl them; record the link only.

## 2. Distill into the page
Topic page `KB/topics/<slug>.md`, sections in this order (skip empty ones):
`## Summary` (3-5 lines) · `## Facts` · `## How to` · `## Pitfalls` · `## Contradictions` ·
`## Open questions` · `## Sources`.
- Every fact is one line ending with its source: `(src: <url or path:line> ✅)`.
- Do not copy long passages; restate in your own words, quote at most a short phrase.
- Contradicting sources: keep both, say which one wins and why (code > ADR > docs > wiki > chat).
- Never store secrets, credentials or personal data. Sensitive pages: store a pointer and the
  note "restricted, ask the owner".

Project page `KB/projects/<repo>.md` (from the repo's README, CLAUDE.md, CONTRIBUTING, docs/,
build files and CI config):
`## Purpose` · `## Layout` (entry points with paths) · `## Commands` (exact build / test / lint
lines) · `## Conventions` (with the source file) · `## Decisions` (ADRs, links) · `## Pitfalls`
(link `~/.claude/autolearn/lessons/<repo>.md`) · `## Sources`.

## 3. Register in the router (same step, not later)
In `KB/topic-map.md`, create or extend the topic block:
- `keywords:` everything a future question might contain: synonyms, the user's language, module
  and file names, issue key prefixes, abbreviations.
- `read first:` `file#section` pointers to the exact sections, most useful first.
- `sources:` with status marks ✅ / 🔎 / ⛔.
- `updated:` today.
A project page is also registered as a topic block (`## Project: <repo>`).

## 4. Budgets (keep the router fast)
| File | Budget | When exceeded |
|---|---|---|
| topic block in the map | <= 8 lines | split the topic |
| `topic-map.md` | <= 25 KB | move topics into `KB/map/<area>.md` and leave one block per area in the top map (hierarchical routing) |
| topic page | <= 40 KB | split into subtopic pages, each with its own block |
| project page | <= 15 KB | move details into topic pages, keep pointers |

## 5. Lint, commit, report
Run `python3 "${CLAUDE_SKILL_DIR}/scripts/kb_lint.py"`. Fix every ERROR
(broken pointer, unroutable page, block without keywords) before committing; act on WARN
budgets as the table above says.
If KB is a git repository: `git -C KB add -A && git -C KB commit -m "kb: <topic> (+N facts)"`.
Report in two lines: what was added (pages, facts, keywords) and the new router keywords.
If `.router-log.tsv` shows misses this ingest closes, say which.
