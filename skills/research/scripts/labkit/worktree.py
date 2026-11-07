"""Separate working copies for experiments that change the project's code, so several executors can
run at once without editing each other's files: `git worktree` under `<lab>/worktrees/<id>`, which
the research folder's .gitignore keeps out of the project's own status.
"""
from __future__ import annotations

import pathlib
import subprocess

from .ledger import ID
from .protocol import fail


def git(cwd: pathlib.Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def cmd_worktree(a) -> int:
    lab = a.lab.resolve()
    top = git(lab, "rev-parse", "--show-toplevel")
    if top.returncode:
        return fail(f"{a.lab} is not inside a git repository: worktrees need one")
    base = lab / "worktrees"
    if a.action == "list":
        listed = git(lab, "worktree", "list", "--porcelain").stdout
        mine = [line.split(" ", 1)[1] for line in listed.splitlines()
                if line.startswith("worktree ") and pathlib.Path(line.split(" ", 1)[1]).parent == base]
        print("\n".join(mine) if mine else "no experiment worktrees")
        return 0
    if not a.id or not ID.match(a.id):
        return fail(f"worktree {a.action} needs an experiment id (letters, digits, '.', '_', '-')")
    path = base / a.id
    if a.action == "add":
        if path.exists():
            return fail(f"{path} exists already")
        base.mkdir(parents=True, exist_ok=True)
        ignore = lab / ".gitignore"
        lines = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
        if "worktrees/" not in lines:
            ignore.write_text("\n".join(lines + ["worktrees/"]) + "\n", encoding="utf-8")
        done = git(lab, "worktree", "add", "--detach", str(path), a.ref or "HEAD")
        if done.returncode:
            return fail(f"git worktree add: {done.stderr.strip()}")
        print(f"{path} at {a.ref or 'HEAD'}: change the code there and pass its files to `lab.py run --code`")
        return 0
    done = git(lab, "worktree", "remove", *(["--force"] if a.force else []), str(path))
    if done.returncode:
        return fail(f"git worktree remove: {done.stderr.strip()} (uncommitted changes need --force)")
    print(f"removed {path}")
    return 0
