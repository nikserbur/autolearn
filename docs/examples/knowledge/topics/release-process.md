# Release process (example page - replace with your own)

## Summary
How a change gets from a merged PR to production in this (fictional) setup. Every fact ends
with its source, so the router can say what is verified.

## Facts
- Releases are cut from `main` by a tag `vX.Y.Z`; CI publishes on tag push. (src: CONTRIBUTING.md:40 ✅)
- Hotfixes branch from the last tag, not from `main`. (src: CONTRIBUTING.md:55 ✅)

## How to
1. Update `CHANGELOG.md` under "Unreleased", move it to the new version. (src: CONTRIBUTING.md:42 ✅)
2. Tag and push the tag; never push to `main` directly. (src: CONTRIBUTING.md:47 ✅)

## Pitfalls
- A tag on a commit that is not on `main` publishes anyway; check `git branch --contains`. (src: https://wiki.example.com/releases 🔎)

## Sources
- CONTRIBUTING.md ✅
- https://wiki.example.com/releases 🔎
