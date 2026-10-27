#!/usr/bin/env python3
"""Mine local Claude Code transcripts for demand signals. Read-only, local, standard library only.

    scan_sessions.py --days 30 [--projects-dir ~/.claude/projects] [--top 15]

Reports, across all projects: the most repeated Bash command heads, tool and MCP usage, tools
that fail or get denied most, skills actually invoked, and request openings the user typed 3+
times. Prints counts and short redacted phrases only; never full prompts or tool outputs.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import shlex
import sys
import time

TWO_WORD = {"git", "uv", "npm", "pnpm", "yarn", "docker", "kubectl", "cargo", "go", "gh", "make",
            "python", "python3", "poetry", "terraform", "helm", "aws", "gcloud", "claude", "brew"}
DENIED = re.compile(r"(user (doesn't|does not) want to proceed|permission (was )?denied|"
                    r"denied by (a )?hook|was blocked)", re.I)
BASIC = {"grep", "rg", "sed", "awk", "cat", "head", "tail", "ls", "cd", "find", "echo", "wc", "pwd",
         "printf", "sort", "uniq", "cut", "tr", "xargs", "test", "true", "mkdir", "cp", "mv", "rm",
         "for", "while", "if", "set", "export", "source", "sleep"}
HARNESS = re.compile(r"^\s*(this session is being continued|another claude session|the coordinator sent|"
                     r"your response above was cut|\[request interrupted|caveat:|the user sent a new message)", re.I)
SECRET = re.compile(r"(\b(AKIA|ASIA)[0-9A-Z]{16}\b|\bsk-[A-Za-z0-9_-]{20,}|\bgh[pousr]_[A-Za-z0-9]{20,}|"
                    r"\bxox[abprs]-[A-Za-z0-9-]{10,})")


def head(command: str) -> str:
    first = re.split(r"&&|\|\||;|\|", command, maxsplit=1)[0].strip()
    try:
        tok = shlex.split(first)
    except ValueError:
        tok = first.split()
    tok = [t for t in tok if "=" not in t or t.startswith("-")]  # drop VAR=value prefixes
    if not tok:
        return ""
    if tok[0] == "cd" and ("&&" in command):
        return head(command.split("&&", 1)[1])
    base = pathlib.Path(tok[0]).name
    if base in TWO_WORD and len(tok) > 1 and not tok[1].startswith("-"):
        return f"{base} {tok[1]}"
    return base


def blocks(msg) -> list:
    content = (msg or {}).get("content") if isinstance(msg, dict) else None
    return content if isinstance(content, list) else []


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def opening(prompt: str) -> str:
    words = re.findall(r"[\w'-]+", SECRET.sub("[REDACTED]", prompt.lower()), re.U)
    return " ".join(words[:5])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--projects-dir", type=pathlib.Path, default=pathlib.Path.home() / ".claude" / "projects")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--all-commands", action="store_true", help="keep grep/sed/cat/ls... in the Bash table")
    a = ap.parse_args()

    since = time.time() - a.days * 86400
    files = [f for f in a.projects_dir.rglob("*.jsonl") if f.stat().st_mtime >= since] \
        if a.projects_dir.exists() else []
    bash, tools, errors, denied, skills, openings = (collections.Counter() for _ in range(6))
    projects, sessions = set(), 0
    for f in files:
        sessions += 1
        projects.add(f.relative_to(a.projects_dir).parts[0])
        names, seen = {}, set()  # an opening counts once per transcript (forks and retries repeat it)
        try:
            fh = f.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for raw in fh:
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                kind = rec.get("type")
                msg = rec.get("message")
                if kind == "assistant":
                    for b in blocks(msg):
                        if b.get("type") != "tool_use":
                            continue
                        name = b.get("name", "?")
                        names[b.get("id")] = name
                        tools[name] += 1
                        inp = b.get("input") or {}
                        if name == "Bash" and inp.get("command"):
                            h = head(inp["command"])
                            if h and (a.all_commands or h not in BASIC):
                                bash[h] += 1
                        elif name == "Skill" and inp.get("skill"):
                            skills[inp["skill"]] += 1
                elif kind == "user":
                    content = (msg or {}).get("content") if isinstance(msg, dict) else None
                    human = not (rec.get("isMeta") or rec.get("isSidechain") or rec.get("isCompactSummary"))
                    if human and isinstance(content, str) and not content.lstrip().startswith("<") \
                            and not HARNESS.search(content):
                        o = opening(content)
                        if len(o.split()) >= 3 and o not in seen:
                            seen.add(o)
                            openings[o] += 1
                    for b in blocks(msg):
                        if b.get("type") != "tool_result":
                            continue
                        name = names.get(b.get("tool_use_id"), "?")
                        body = text_of(b.get("content"))
                        if DENIED.search(body[:500]):
                            denied[name] += 1
                        elif b.get("is_error"):
                            errors[name] += 1

    def table(title, counter, n=a.top, min_count=1):
        rows = [(k, v) for k, v in counter.most_common(n) if v >= min_count]
        print(f"\n## {title}")
        print("\n".join(f"{v:6d}  {k}" for k, v in rows) if rows else "     -")

    print(f"# Session scan: last {a.days} days")
    print(f"{sessions} transcripts across {len(projects)} projects")
    table("Bash command heads, basic shell utilities excluded (candidates for a skill or a script)", bash)
    table("Tools and MCP calls", tools)
    table("Tools with errors (count)", errors)
    table("Tools denied by the user or a hook", denied)
    table("Skills invoked", skills)
    table("Request openings typed in 3+ sessions (candidate workflows)", openings, min_count=3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
