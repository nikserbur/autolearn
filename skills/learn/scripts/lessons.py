#!/usr/bin/env python3
"""Bookkeeping CLI for autolearn lessons, inbox and proposals. Standard library only.

    lessons.py inbox [--limit 50]          unprocessed feedback signals, compact, plus counts
    lessons.py advance --to-end | N        move the inbox cursor after processing
    lessons.py proposals [--status pending]
    lessons.py reviewers PATCH [--repo R]  extra reviewers whose trigger matches the diff
    lessons.py stats [--repo R] [--last 5] recent runs.tsv rows with tuning hints
    lessons.py signal KIND TEXT [--project R]  queue a signal (e.g. merge_gate) for /learn

Data directory: $AUTOLEARN_HOME or ~/.claude/autolearn. The model calls this instead of
reading whole files into context.
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime
import fnmatch
import json
import os
import pathlib
import re
import sys


def home() -> pathlib.Path:
    env = os.environ.get("AUTOLEARN_HOME")
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "autolearn"


def cursor_file() -> pathlib.Path:
    return home() / "inbox" / ".cursor"


def read_cursor() -> int:
    try:
        return int(cursor_file().read_text().strip() or 0)
    except (OSError, ValueError):
        return 0


def inbox_lines() -> list:
    f = home() / "inbox" / "feedback.jsonl"
    return f.read_text(encoding="utf-8").splitlines() if f.exists() else []


def cmd_inbox(a) -> int:
    lines, start = inbox_lines(), read_cursor()
    fresh = lines[start:]
    kinds, projects = collections.Counter(), collections.Counter()
    for n, raw in enumerate(fresh[: a.limit], start + 1):
        try:
            r = json.loads(raw)
        except ValueError:
            continue
        kinds.update(r.get("kinds", []))
        projects[r.get("project", "?")] += 1
        print(f"#{n} {r.get('ts', '')[:16]} [{r.get('project', '?')}] {','.join(r.get('kinds', []))}: "
              f"{r.get('excerpt', '')} (session {r.get('session', '')[:8]})")
    more = len(fresh) - a.limit
    if more > 0:
        print(f"... {more} more (use --limit)")
    print(f"\n{len(fresh)} new of {len(lines)} total. kinds: {dict(kinds)} projects: {dict(projects)}")
    print(f"After processing: lessons.py advance {len(lines)}")
    return 0


def cmd_advance(a) -> int:
    total = len(inbox_lines())
    n = total if a.target == "--to-end" or a.target == "end" else min(int(a.target), total)
    cursor_file().parent.mkdir(parents=True, exist_ok=True)
    cursor_file().write_text(str(n))
    print(f"cursor -> {n} of {total}")
    return 0


def cmd_proposals(a) -> int:
    folder = home() / "proposals"
    rows = []
    for p in sorted(folder.glob("*.md")) if folder.exists() else []:
        text = p.read_text(encoding="utf-8")
        status = (re.search(r"(?m)^Status:\s*(.+)$", text) or [None, "?"])[1].strip()
        if a.status and not status.lower().startswith(a.status.lower()):
            continue
        title = (re.search(r"(?m)^#\s+(.+)$", text) or [None, p.stem])[1].strip()
        meta = (re.search(r"(?m)^Repo:.*$", text) or [""])[0]
        rows.append(f"{p.name}  [{status}]  {title}  {meta}")
    print("\n".join(rows) if rows else "no proposals")
    return 0


def reviewer_rows(path: pathlib.Path) -> list:
    """(agent, [triggers]) from the '## Extra reviewers' markdown table."""
    if not path.exists():
        return []
    m = re.search(r"(?ms)^## Extra reviewers.*?$(.*?)(?=^## |\Z)", path.read_text(encoding="utf-8"))
    rows = []
    for line in (m.group(1).splitlines() if m else []):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or not cells[0] or set(cells[0]) <= set("-: ") or cells[0].lower() == "agent":
            continue
        rows.append((cells[0], [t.strip() for t in cells[1].split(",") if t.strip()]))
    return rows


def parse_patch(text: str) -> tuple:
    files, added = set(), []
    for line in text.splitlines():
        if line.startswith("+++ b/"):
            files.add(line[6:])
        elif line.startswith("diff --git "):
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                files.add(parts[1])
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line[1:])
    return files, "\n".join(added)


def matches(trigger: str, files: set, added: str) -> bool:
    if len(trigger) >= 2 and trigger[0] in "\"'`" and trigger[-1] == trigger[0]:
        return trigger[1:-1].lower() in added.lower()  # quoted = keyword in added lines
    return any(fnmatch.fnmatch(f, trigger) or fnmatch.fnmatch(f, trigger.rstrip("/") + "/*")
               for f in files)


def cmd_reviewers(a) -> int:
    files, added = parse_patch(pathlib.Path(a.patch).read_text(encoding="utf-8", errors="replace"))
    repo = a.repo or pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).name
    chosen = []
    for src in (home() / "lessons" / f"{repo}.md", home() / "lessons" / "pipeline.md"):
        for agent, triggers in reviewer_rows(src):
            hit = next((t for t in triggers if matches(t, files, added)), None)
            if hit and agent not in [c[0] for c in chosen]:
                chosen.append((agent, hit, src.name))
    for agent, hit, src in chosen:
        print(f"{agent}\t(trigger {hit} from {src})")
    if not chosen:
        print("no extra reviewers match")
    return 0


def cmd_stats(a) -> int:
    f = home() / "lessons" / "runs.tsv"
    if not f.exists():
        print("no runs.tsv yet")
        return 0
    rows = list(csv.DictReader(f.open(encoding="utf-8"), delimiter="\t"))
    if a.repo:
        rows = [r for r in rows if r.get("repo") == a.repo]
    rows = rows[-a.last:]
    for r in rows:
        print("\t".join(r.get(k, "") or "" for k in ("date", "repo", "slug", "mode", "critic_blocking",
                                                     "findings_confirmed", "findings_refuted",
                                                     "gate_blocks", "merge_gate_findings", "outcome")))

    def num(r, k):
        try:
            return int(r.get(k) or 0)
        except ValueError:
            return 0
    hints = []
    if len(rows) >= 5 and all(num(r, "critic_blocking") == 0 for r in rows):
        hints.append("critic found nothing blocking in 5 runs -> consider effort: low for plan-critic")
    if rows and sum(num(r, "findings_refuted") for r in rows) > sum(num(r, "findings_confirmed") for r in rows):
        hints.append("refuted > confirmed review findings -> tighten the code-reviewer signal threshold")
    if sum(num(r, "gate_blocks") for r in rows) >= 3:
        hints.append("the gate keeps blocking -> look for a recurring cause and add a planner lesson")
    if sum(num(r, "merge_gate_findings") for r in rows) >= 3:
        hints.append("merge-gate keeps finding things upstream missed -> run /retro on the top class")
    print("\n".join(f"hint: {h}" for h in hints) or "no tuning hints")
    return 0


def cmd_signal(a) -> int:
    inbox = home() / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    project = a.project or pathlib.Path(os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).name
    record = {"ts": datetime.datetime.now().isoformat(timespec="seconds"), "session": "",
              "project": project, "cwd": os.getcwd(), "kinds": [a.kind],
              "excerpt": " ".join(a.text.split())[:280]}
    with open(inbox / "feedback.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"queued {a.kind} for {project}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inbox"); p.add_argument("--limit", type=int, default=50); p.set_defaults(fn=cmd_inbox)
    p = sub.add_parser("advance"); p.add_argument("target"); p.set_defaults(fn=cmd_advance)
    p = sub.add_parser("proposals"); p.add_argument("--status", default=""); p.set_defaults(fn=cmd_proposals)
    p = sub.add_parser("reviewers"); p.add_argument("patch"); p.add_argument("--repo"); p.set_defaults(fn=cmd_reviewers)
    p = sub.add_parser("stats"); p.add_argument("--repo"); p.add_argument("--last", type=int, default=5)
    p.set_defaults(fn=cmd_stats)
    p = sub.add_parser("signal"); p.add_argument("kind"); p.add_argument("text"); p.add_argument("--project")
    p.set_defaults(fn=cmd_signal)
    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
