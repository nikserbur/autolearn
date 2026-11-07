#!/usr/bin/env python3
"""SessionStart: inject a tiny routing brief (pointers and counts, never content).

The point is to keep the context window lean: the session learns *where* the knowledge base is
and loads sections only when a task needs them.

    session_brief.py [--seed <templates-dir>]

With --seed (plugin mode), a missing data directory is created from the templates first, so
the plugin works without install.sh. Typical output (< 400 chars):

  Knowledge base: 14 topics in knowledge/topic-map.md - route domain questions through knowledge-search first.
  Research mm: 12 runs, 1 unfinished (E9), 2 awaiting review, best E7 - research/mm/STATUS.md.
  No knowledge page for api-server yet - offer knowledge-ingest to build one from its docs.

Suggestions (a missing project page, a missing embedding model) appear at most once a week each.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import os
import pathlib
import re
import sys

from _common import emit, home, read_payload, repo_key

NUDGE_EVERY_DAYS = 7


def embeddings_missing(h: pathlib.Path) -> bool:
    """True when RAG would run on the stdlib hashing fallback instead of a real model."""
    try:
        backend = json.loads((h / "knowledge" / ".rag" / "meta.json").read_text()).get("backend") or ""
    except (OSError, ValueError):
        backend = ""
    if backend:
        return backend.startswith("hash")
    if os.environ.get("RESEARCH_LAB_PYTHON") or (h / "model-python").exists() or os.environ.get("RESEARCH_LAB_EMBED", "auto") == "hash":
        return False
    return not any(importlib.util.find_spec(m) for m in ("fastembed", "sentence_transformers"))


EMBED_HINT = ("RAG has no embedding model (stdlib hashing: weaker, no cross-language search) - offer once "
              "to install one: `kb_rag.py setup` from the knowledge-search skill (a private venv, about 370 MB of downloads); "
              "the next kb_rag.py search rebuilds the store.")


def active_research(root: pathlib.Path) -> list:
    """One line per research folder of this project (the three touched last): runs, unfinished runs,
    experiments waiting for the reviewer, the best one. Read from its ledger; content never enters."""
    try:
        ledgers = sorted(root.glob("research/*/LEDGER.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    except OSError:
        return []
    out = []
    for path in ledgers:
        entries = []
        for line in read(path).split("\n"):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        starts = [e for e in entries if e.get("kind") == "run_start"]
        open_runs = {}
        for e in entries:
            if e.get("kind") in ("run_start", "run_end"):
                open_runs[e.get("id")] = open_runs.get(e.get("id"), 0) + (1 if e["kind"] == "run_start" else -1)
        unfinished = [i for i, n in open_runs.items() if n > 0]
        latest = {e.get("id"): e.get("seq") for e in starts}
        judged = {e.get("id") for e in entries if e.get("kind") == "judge" and e.get("run_seq") == latest.get(e.get("id"))}
        reviewed = {e.get("id") for e in entries if e.get("kind") == "review" and e.get("run_seq") == latest.get(e.get("id"))}
        best = next((e.get("id") for e in reversed(entries) if e.get("kind") == "best"), None)
        bits = [f"{len(starts)} runs"]
        if unfinished:
            bits.append(f"{len(unfinished)} unfinished ({', '.join(map(str, unfinished[:3]))})")
        if judged - reviewed:
            bits.append(f"{len(judged - reviewed)} awaiting review")
        if best:
            bits.append(f"best {best}")
        out.append(f"Research {path.parent.name}: {', '.join(bits)} - {path.parent.relative_to(root)}/STATUS.md "
                   "(`lab.py status` refreshes it); resume /research from there.")
    return out


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
    first = moved = False
    if "--seed" in sys.argv:
        from _seed import FILES, ensure_ignored, migrate, old_home, seed  # idempotent; never overwrites
        moved = migrate(h, old_home())
        first = not (h / "knowledge" / "topic-map.md").exists()
        if first or any(not (h / dst).exists() for dst in FILES):
            seed(h, pathlib.Path(sys.argv[sys.argv.index("--seed") + 1]))
        else:
            ensure_ignored(h)
    if first:
        return emit({"hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": f"research-lab: created its data directory at {h} (the knowledge base, under "
                                 "git). Mention once that the user can run /knowledge-ingest repo to give this "
                                 "repo a knowledge page."
                                 + ("\n" + EMBED_HINT if embeddings_missing(h) else ""),
        }})
    if not h.exists():
        return 0
    lines, nudges = [], Nudges(h / ".brief-state.json")
    if moved:
        lines.append(f"research-lab: moved the data directory of autolearn (its old name) to {h}; mention it once.")

    topics = len(re.findall(r"^## ", read(h / "knowledge" / "topic-map.md"), re.M))
    if topics:
        lines.append(f"Knowledge base: {topics} topics in knowledge/topic-map.md - route domain "
                     "questions through the knowledge-search skill before searching elsewhere.")
    repo = repo_key(payload)
    root = pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd())
    lines += active_research(root)
    page_key = f"kbpage:{repo}"
    if (root / ".git").exists() and not (h / "knowledge" / "projects" / f"{repo}.md").exists() \
            and nudges.due(page_key):
        lines.append(f"No knowledge page for {repo} yet - offer knowledge-ingest to build one from its "
                     "docs and register it in the topic map.")
        nudges.shown(page_key)

    if nudges.due("embed") and embeddings_missing(h):
        lines.append(EMBED_HINT)
        nudges.shown("embed")

    if not lines:
        return 0
    return emit({"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "\n".join(lines) + f"\n(paths are relative to {h})",
    }})


if __name__ == "__main__":
    sys.exit(main())
