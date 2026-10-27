#!/usr/bin/env python3
"""Seed the autolearn data directory from templates. Never overwrites an existing file.

    python3 _seed.py <data-dir> <templates-dir>

Used by install.sh and, in plugin mode, by session_brief.py on the first session. Creates the
folder layout, copies empty skeleton files, writes the runs.tsv header and .gitignore, and puts
the directory under git (a failed first commit, e.g. no git identity, is not an error).
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

DIRS = ["knowledge/topics", "knowledge/projects", "lessons", "runs", "inbox", "style", "radar", "proposals"]
FILES = {
    "knowledge/topic-map.md": "knowledge/topic-map.md",
    "knowledge/required-docs.md": "knowledge/required-docs.md",
    "lessons/pipeline.md": "lessons/pipeline.md",
    "lessons/global.md": "lessons/global.md",
    "style/messages.md": "style/messages.md",
    "feedback-patterns.json": "feedback-patterns.json",
}
RUNS_HEADER = ("date\trepo\tslug\tmode\ttasks\tcritic_rounds\tcritic_blocking\treview_rounds\t"
               "findings_confirmed\tfindings_refuted\tgate_blocks\tmerge_gate_findings\toutcome\n")
GITIGNORE = "runs/\ninbox/\n.install-manifest\n.brief-state.json\n__pycache__/\n"


def seed(home: pathlib.Path, templates: pathlib.Path) -> list:
    created = []
    for d in DIRS:
        (home / d).mkdir(parents=True, exist_ok=True)
    for dst, src in FILES.items():
        target = home / dst
        if not target.exists() and (templates / src).exists():
            shutil.copyfile(templates / src, target)
            created.append(dst)
    for rel, text in (("lessons/runs.tsv", RUNS_HEADER), (".gitignore", GITIGNORE)):
        if not (home / rel).exists():
            (home / rel).write_text(text)
            created.append(rel)
    if shutil.which("git") and not (home / ".git").exists():
        run = lambda *a: subprocess.run(["git", "-C", str(home), *a], capture_output=True)
        run("init", "-q")
        run("add", "-A")
        run("commit", "-q", "-m", "autolearn: seed data directory")
    return created


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__.strip().splitlines()[2])
    for path in seed(pathlib.Path(sys.argv[1]).expanduser(), pathlib.Path(sys.argv[2])):
        print(f"  seeded {path}")
