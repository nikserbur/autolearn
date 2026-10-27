# Topic map - the knowledge-base router

Start here for any domain question. `kb-router` greps this file by keywords and loads only the
sections listed under `read first`. `kb-ingest` adds a block for every page it writes.
Block format (<= 8 lines): heading, `keywords:`, `read first:`, `sources:`, `updated:`.
Status marks: ✅ read and distilled · 🔎 seen in search only · ⛔ do not crawl, link only.

## Always: mandatory checklists
keywords: checklist, rules, definition of done, review rules
read first: required-docs.md
updated: 2026-01-01

## Example: release process
keywords: release, deploy, version bump, changelog, hotfix, tag
read first: topics/release-process.md#how-to · topics/release-process.md#pitfalls
sources: CONTRIBUTING.md ✅ · https://wiki.example.com/releases 🔎
updated: 2026-01-01
