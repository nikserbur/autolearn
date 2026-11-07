"""Separate working copies for experiments that change the project's code, so several executors can
run at once without editing each other's files: `git worktree` under `<lab>/worktrees/<id>`, each on
its own branch `exp/<research>-<id>`, so commits made there outlive the copy. The folder is excluded
through the repository's own `.git/info/exclude`, so no tracked file changes.

`--lab` always names the main research folder, never a copy: a copy carries an old LEDGER.jsonl.
"""
from __future__ import annotations

import pathlib
import subprocess

from .ledger import ID
from .protocol import fail


def git(cwd: pathlib.Path, *args) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    except FileNotFoundError:
        return subprocess.CompletedProcess(args, 127, "", "git is not installed")


def exclude(repo: pathlib.Path, folder: pathlib.Path) -> None:
    """Keep the copies out of `git status` without touching a tracked file."""
    common = git(repo, "rev-parse", "--git-common-dir").stdout.strip()
    if not common:
        return
    info = (repo / common if not pathlib.Path(common).is_absolute() else pathlib.Path(common)) / "info"
    info.mkdir(parents=True, exist_ok=True)
    try:
        rel = folder.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError:
        return  # outside this repository: nothing to hide
    entry = f"/{rel}/"
    path = info / "exclude"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    if entry not in lines:
        path.write_text("\n".join(lines + [entry]) + "\n", encoding="utf-8")


def cmd_worktree(a) -> int:
    lab = a.lab.resolve()
    where = pathlib.Path(a.repo).resolve() if a.repo else lab
    top = git(where, "rev-parse", "--show-toplevel")
    if top.returncode:
        return fail(f"{where} is not inside a git repository ({top.stderr.strip()}); --repo names the project's code")
    repo = pathlib.Path(top.stdout.strip())
    base = lab / "worktrees"
    if a.action == "list":
        listed = git(repo, "worktree", "list", "--porcelain").stdout
        mine = [line.split(" ", 1)[1] for line in listed.splitlines()
                if line.startswith("worktree ") and pathlib.Path(line.split(" ", 1)[1]).parent == base]
        print("\n".join(mine) if mine else "no experiment worktrees")
        return 0
    if not a.id or not ID.match(a.id):
        return fail(f"worktree {a.action} needs an experiment id (letters, digits, '.', '_', '-')")
    path, branch = base / a.id, f"exp/{lab.name}-{a.id}"
    if a.action == "add":
        if path.exists():
            return fail(f"{path} exists already")
        base.mkdir(parents=True, exist_ok=True)
        done = git(repo, "worktree", "add", "-b", branch, str(path), a.ref or "HEAD")
        if done.returncode:
            return fail(f"git worktree add: {done.stderr.strip()}")
        exclude(repo, base)
        print(f"{path} on branch {branch} from {a.ref or 'HEAD'}: change the code there, commit it, and pass its "
              "files to `lab.py run --code`; keep the best candidate's copy until its holdout look is closed")
        return 0
    done = git(repo, "worktree", "remove", *(["--force"] if a.force else []), str(path))
    if done.returncode:
        return fail(f"git worktree remove: {done.stderr.strip()} (uncommitted changes need --force)")
    print(f"removed {path}; its commits stay on branch {branch}")
    return 0
