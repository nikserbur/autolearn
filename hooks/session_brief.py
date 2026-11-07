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


SAFE = re.compile(r"[^A-Za-z0-9._-]")


def plain(text) -> str:
    """A name from someone else's repo, reduced to a harmless token before it enters the context."""
    return SAFE.sub("", str(text))[:40] or "?"


def research_line(root: pathlib.Path, path: pathlib.Path) -> str:
    entries = []
    for line in read(path).split("\n"):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("id"), str):
            entries.append(entry)
    jobs, by_seq = [], {}
    for e in entries:  # the same pairing as lab.py: the end names its start, or the latest open start of its id
        if e.get("kind") == "run_start":
            job = {"id": e["id"], "seq": e.get("seq"), "end": None}
            jobs.append(job)
            by_seq[e.get("seq")] = job
        elif e.get("kind") == "run_end":
            job = by_seq.get(e.get("start_seq")) or next((j for j in reversed(jobs) if j["id"] == e["id"] and j["end"] is None), None)
            if job is not None and job["end"] is None:
                job["end"] = e
    unfinished = list(dict.fromkeys(j["id"] for j in jobs if j["end"] is None))
    latest = {j["id"]: j["seq"] for j in jobs}
    passed = {e["id"] for e in entries if e.get("kind") == "judge" and e.get("verdict") == "PASS"
              and e.get("run_seq") == latest.get(e["id"])}
    reviewed = {e["id"] for e in entries if e.get("kind") == "review" and e.get("run_seq") == latest.get(e["id"])}
    best = next((e["id"] for e in reversed(entries) if e.get("kind") == "best"), None)
    bits = [f"{len(jobs)} runs"]
    if unfinished:
        bits.append(f"{len(unfinished)} unfinished ({', '.join(plain(i) for i in unfinished[:3])})")
    if passed - reviewed:
        bits.append(f"{len(passed - reviewed)} awaiting review")
    if best:
        bits.append(f"best {plain(best)}")
    return (f"Research {plain(path.parent.name)}: {', '.join(bits)} - research/{plain(path.parent.name)}/STATUS.md "
            "(`lab.py status` writes it); resume /research from there.")


def active_research(root: pathlib.Path) -> list:
    """One line per research folder of this project (the three touched last): runs, unfinished runs,
    experiments waiting for the reviewer, the best one. Counts and sanitised ids only; never content."""
    found = []
    try:
        candidates = list(root.glob("research/*/LEDGER.jsonl"))
    except OSError:
        return []
    for path in candidates:
        try:
            found.append((path.stat().st_mtime, path))
        except OSError:  # a dangling link: skip that one, keep the others
            continue
    out = []
    for _, path in sorted(found, reverse=True)[:3]:
        try:
            out.append(research_line(root, path))
        except Exception:  # a broken ledger must not take the rest of the brief down
            out.append(f"Research {plain(path.parent.name)}: its LEDGER.jsonl could not be read - run `lab.py check`.")
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
