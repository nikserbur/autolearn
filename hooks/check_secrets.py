#!/usr/bin/env python3
"""PreToolUse(Bash): deny a `git commit` when what it would commit looks like it contains a secret.

The hook fires before the command runs, so it scans what the commit WILL contain:
  - the staged diff (`git diff --cached`),
  - plus files a preceding `git add <paths>` / `git add -A` in the same command will stage
    (tracked changes and untracked files under those paths),
  - plus all tracked changes when the commit uses `-a` / `--all` / `-am`.
Handles `cd X &&`, `git -C X`, `~`, and commands split over several lines.
"""
from __future__ import annotations

import os
import subprocess
import sys

from _common import SECRET_PATTERNS, emit, git_call, read_payload, segments

MAX_FILE_BYTES = 1_000_000


def git(repo: str, *args) -> str:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True).stdout


def plan(command: str, cwd: str) -> dict:
    """{repo: {"paths": [...pathspecs that `git add` will stage], "all": bool}} for repos committed to."""
    repos, pending, cur = {}, {}, cwd
    for tok in segments(command):
        if tok[0] == "cd":
            target = os.path.expanduser(tok[1]) if len(tok) > 1 else os.path.expanduser("~")
            cur = os.path.normpath(os.path.join(cur, target))  # relative to where we are now
            continue
        repo, args = git_call(tok, cur)
        if not args:
            continue
        if args[0] == "add":
            flags = [a for a in args[1:] if a.startswith("-")]
            paths = [a for a in args[1:] if not a.startswith("-")]
            if {"-A", "--all", "-u", "--update"} & set(flags) or not paths:
                paths = ["."]
            pending.setdefault(repo, []).extend(paths)
        elif args[0] == "commit":
            commit_all = any(a in ("-a", "--all") or (a.startswith("-") and not a.startswith("--")
                                                      and "a" in a[1:]) for a in args[1:])
            entry = repos.setdefault(repo, {"paths": [], "all": False})
            entry["paths"] += pending.pop(repo, [])
            entry["all"] = entry["all"] or commit_all
    return repos


def added_lines(repo: str, paths: list, commit_all: bool):
    """Yield (file, line) for every line the commit would add."""
    diffs = [git(repo, "diff", "--cached", "--unified=0")]
    if commit_all:
        diffs.append(git(repo, "diff", "--unified=0"))
    elif paths:
        diffs.append(git(repo, "diff", "--unified=0", "--", *paths))
    for diff in diffs:
        current = "?"
        for line in diff.splitlines():
            if line.startswith("+++ "):
                current = line[6:] if line.startswith("+++ b/") else line[4:]
            elif line.startswith("+") and not line.startswith("+++"):
                yield current, line[1:]
    if paths:
        for rel in git(repo, "ls-files", "--others", "--exclude-standard", "-z", "--", *paths).split("\0"):
            path = os.path.join(repo, rel)
            if not rel or not os.path.isfile(path) or os.path.getsize(path) > MAX_FILE_BYTES:
                continue
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        yield rel, line
            except (UnicodeDecodeError, OSError):
                continue  # binary or unreadable


def main() -> int:
    data = read_payload()
    command = (data.get("tool_input") or {}).get("command", "")
    if "commit" not in command:
        return 0
    findings = set()
    for repo, spec in plan(command, data.get("cwd") or os.getcwd()).items():
        for path, line in added_lines(repo, spec["paths"], spec["all"]):
            for name, rx in SECRET_PATTERNS.items():
                if rx.search(line):
                    findings.add(f"{repo}: {path}: {name}")
    if not findings:
        return 0
    reason = "Commit blocked: possible secrets in what this commit would add:\n" + "\n".join(sorted(findings))
    return emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason + "\nRemove them (or exclude the file) and retry. Never commit keys.",
    }})


if __name__ == "__main__":
    sys.exit(main())
