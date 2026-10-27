#!/usr/bin/env python3
"""SessionStart: inject a tiny routing brief (pointers and counts, never content).

The point is to keep the context window lean: the session learns *where* the lessons,
knowledge and pending proposals are, and loads them only when a task needs them.

    session_brief.py [--seed <templates-dir>]

With --seed (plugin mode), a missing data directory is created from the templates first, so
the plugin works without install.sh. Typical output (< 700 chars):

  autolearn: 3 active pipeline rules (lessons/pipeline.md, read when running /feature).
  This repo (api-server): 7 lessons, 1 extra reviewer (lessons/api-server.md) - read before planning or reviewing here.
  Knowledge base: 14 topics in knowledge/topic-map.md - route domain questions through kb-router first.
  Feedback inbox: 5 new signals - offer /learn at a natural break.
  Proposals waiting for the user's decision: 2 (proposals/).

Suggestions (skill radar, a missing project page) appear at most once a week each.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import sys

from _common import emit, home, read_payload, repo_key

RADAR_STALE_DAYS = 30
NUDGE_EVERY_DAYS = 7


def section_items(text: str, heading: str) -> int:
    m = re.search(rf"^## {re.escape(heading)}.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return 0
    body = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S)
    return len(re.findall(r"^\s*(\d+\.|-|\|(?!\s*-)(?!\s*Agent))\s+\S", body, re.M))


def read(path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


class Nudges:
    """Remember when a suggestion was last shown so it does not repeat every session."""

    def __init__(self, path: pathlib.Path):
        self.path = path
        try:
            self.state = json.loads(path.read_text())
        except (OSError, ValueError):
            self.state = {}
        self.today = datetime.date.today()

    def due(self, key: str) -> bool:
        try:
            last = datetime.date.fromisoformat(self.state.get(key, ""))
        except ValueError:
            return True
        return (self.today - last).days >= NUDGE_EVERY_DAYS

    def shown(self, key: str) -> None:
        self.state[key] = self.today.isoformat()
        try:
            self.path.write_text(json.dumps(self.state))
        except OSError:
            pass


def main() -> int:
    payload = read_payload()
    h = home()
    if "--seed" in sys.argv:
        from _seed import FILES, seed  # idempotent; never overwrites
        first = not (h / "lessons" / "pipeline.md").exists()
        if first or any(not (h / dst).exists() for dst in FILES):
            seed(h, pathlib.Path(sys.argv[sys.argv.index("--seed") + 1]))
    if "--seed" in sys.argv and first:
        return emit({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"autolearn: created its data directory at {h} (knowledge base, "
                                 "lessons, style, proposals; under git). Mention once that the user can "
                                 "add team checklists to knowledge/required-docs.md and run /kb-ingest repo.",
        }})
    if not h.exists():
        return 0
    lines, nudges = [], Nudges(h / ".brief-state.json")

    rules = section_items(read(h / "lessons" / "pipeline.md"), "Active pipeline rules")
    if rules:
        lines.append(f"autolearn: {rules} active pipeline rules (lessons/pipeline.md, read when running /feature).")

    repo = repo_key(payload)
    repo_text = read(h / "lessons" / f"{repo}.md")
    if repo_text:
        n = section_items(repo_text, "Project lessons")
        r = section_items(repo_text, "Extra reviewers")
        extra = f", {r} extra reviewer{'s' if r != 1 else ''}" if r else ""
        lines.append(f"This repo ({repo}): {n} lessons{extra} (lessons/{repo}.md) - "
                     "read before planning or reviewing here.")

    topics = len(re.findall(r"^## ", read(h / "knowledge" / "topic-map.md"), re.M))
    if topics:
        lines.append(f"Knowledge base: {topics} topics in knowledge/topic-map.md - route domain "
                     "questions through the kb-router skill before searching elsewhere.")
    root = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    page_key = f"kbpage:{repo}"
    if (root / ".git").exists() and not (h / "knowledge" / "projects" / f"{repo}.md").exists() \
            and nudges.due(page_key):
        lines.append(f"No knowledge page for {repo} yet - offer kb-ingest to build one from its "
                     "docs and register it in the topic map.")
        nudges.shown(page_key)

    inbox = read(h / "inbox" / "feedback.jsonl").splitlines()
    try:
        cursor = int(read(h / "inbox" / ".cursor").strip() or 0)
    except ValueError:
        cursor = 0
    fresh = max(0, len(inbox) - cursor)
    if fresh:
        lines.append(f"Feedback inbox: {fresh} new signal{'s' if fresh != 1 else ''} - offer /learn at a natural break.")

    pending = [p for p in (h / "proposals").glob("*.md") if "Status: pending" in read(p)] \
        if (h / "proposals").exists() else []
    if pending:
        lines.append(f"Proposals waiting for the user's decision: {len(pending)} (proposals/).")

    reports = sorted((h / "radar").glob("????-??-??*.md")) if (h / "radar").exists() else []
    age = None
    if reports:
        try:
            age = (datetime.date.today() - datetime.date.fromisoformat(reports[-1].name[:10])).days
        except ValueError:
            age = None
    if (not reports or (age is not None and age > RADAR_STALE_DAYS)) and nudges.due("radar"):
        lines.append("Skill radar has never run - suggest /skill-radar once." if not reports else
                     f"Skill radar last ran {age} days ago - suggest /skill-radar.")
        nudges.shown("radar")

    if not lines:
        return 0
    return emit({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(lines) + f"\n(paths are relative to {h})",
    }})


if __name__ == "__main__":
    sys.exit(main())
