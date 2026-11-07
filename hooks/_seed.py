#!/usr/bin/env python3
"""Seed the research-lab data directory from templates. Never overwrites an existing file.

    python3 _seed.py <data-dir> <templates-dir> [<old-data-dir>]

Used by install.sh and, in plugin mode, by session_brief.py on the first session. Creates the
folder layout, copies the empty topic map, writes .gitignore and puts the directory under git
(a failed first commit, e.g. no git identity, is not an error). The data directory of the project's
previous name, autolearn, moves to the new place first.
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

DIRS = ["knowledge/topics", "knowledge/projects"]
FILES = {"knowledge/topic-map.md": "knowledge/topic-map.md"}
GITIGNORE = ".install-manifest\n.brief-state.json\n__pycache__/\nknowledge/.rag/\nknowledge/.stream/\n"


def old_home() -> pathlib.Path:
    """Where the data lived while the project was called autolearn (before 0.4.0)."""
    env = os.environ.get("AUTOLEARN_HOME")
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "autolearn"


def migrate(home: pathlib.Path, old: pathlib.Path) -> bool:
    """Move the old data directory to `home` once. The private model virtualenv moves with it, so the
    remembered python is pointed at its new place."""
    if home.exists() or not old.is_dir():
        return False
    home.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(old), str(home))
    pointer = home / "model-python"
    if pointer.exists():
        pointer.write_text(pointer.read_text(encoding="utf-8").replace(str(old), str(home)), encoding="utf-8")
    return True


def ensure_ignored(home: pathlib.Path) -> None:
    """An upgrade must also keep the index out of the data repo: it is large, binary and can be rebuilt."""
    ignore = home / ".gitignore"
    if not ignore.exists():
        return
    have = ignore.read_text().splitlines()
    missing = [line for line in GITIGNORE.splitlines() if line.startswith("knowledge/.") and line not in have]
    if not missing:
        return
    ignore.write_text("\n".join(have + missing) + "\n")
    if not shutil.which("git") or not (home / ".git").exists():
        return
    run = lambda *a: subprocess.run(["git", "-C", str(home), *a], capture_output=True, text=True)
    tracked = run("ls-files", "knowledge/.rag", "knowledge/.stream").stdout.split()
    if tracked:
        run("rm", "-r", "-q", "--cached", *tracked)
    run("add", ".gitignore")
    staged = set(run("diff", "--cached", "--name-only").stdout.split())
    if staged <= {".gitignore", *tracked}:  # someone's staged work is not ours to commit: then this waits for theirs
        run("commit", "-q", "-m", "research-lab: keep the rebuildable index out of git")


def seed(home: pathlib.Path, templates: pathlib.Path) -> list:
    created = []
    for d in DIRS:
        (home / d).mkdir(parents=True, exist_ok=True)
    for dst, src in FILES.items():
        target = home / dst
        if not target.exists() and (templates / src).exists():
            shutil.copyfile(templates / src, target)
            created.append(dst)
    if not (home / ".gitignore").exists():
        (home / ".gitignore").write_text(GITIGNORE)
        created.append(".gitignore")
    ensure_ignored(home)
    if shutil.which("git") and not (home / ".git").exists():
        run = lambda *a: subprocess.run(["git", "-C", str(home), *a], capture_output=True)
        run("init", "-q")
        run("add", "-A")
        run("commit", "-q", "-m", "research-lab: seed data directory")
    return created


if __name__ == "__main__":
    if len(sys.argv) not in (3, 4):
        sys.exit(__doc__.strip().splitlines()[2])
    if len(sys.argv) == 4 and migrate(pathlib.Path(sys.argv[1]).expanduser(), pathlib.Path(sys.argv[3]).expanduser()):
        print(f"  moved {sys.argv[3]} -> {sys.argv[1]}")
    for path in seed(pathlib.Path(sys.argv[1]).expanduser(), pathlib.Path(sys.argv[2])):
        print(f"  seeded {path}")
