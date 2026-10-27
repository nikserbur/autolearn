#!/usr/bin/env python3
"""Lint the autolearn knowledge base so the router stays fast and correct. Standard library only.

    kb_lint.py            # check ~/.claude/autolearn/knowledge (or $AUTOLEARN_HOME/knowledge)
    kb_lint.py --kb DIR

Errors (exit 1): broken `read first` pointers, pages no topic block points to (unroutable),
topic blocks without `keywords:`. Warnings: size budgets from the kb-ingest skill.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import re
import sys

BUDGETS = {"block_lines": 8, "map_bytes": 25_000, "topic_bytes": 40_000, "project_bytes": 15_000}


def kb_dir() -> pathlib.Path:
    env = os.environ.get("AUTOLEARN_HOME")
    base = pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "autolearn"
    return base / "knowledge"


def slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.U)
    return re.sub(r"\s+", "-", text.strip())


def lint(kb: pathlib.Path) -> tuple:
    errors, warnings, referenced = [], [], set()
    maps = [kb / "topic-map.md"] + (sorted((kb / "map").glob("*.md")) if (kb / "map").exists() else [])
    maps = [m for m in maps if m.exists()]
    if not maps:
        return [f"no topic map at {kb / 'topic-map.md'}"], [], 0
    for m in maps:
        referenced.add(m.resolve())
        text = m.read_text(encoding="utf-8")
        if m.name == "topic-map.md" and len(text.encode()) > BUDGETS["map_bytes"]:
            warnings.append(f"{m.name}: {len(text.encode()) // 1000} KB > 25 KB, split into map/<area>.md")
        for block in re.split(r"(?m)^(?=## )", text):
            if not block.startswith("## "):
                continue
            head, *body = block.splitlines()
            body = [l for l in body if l.strip()]
            where = f"{m.name}: '{head[3:].strip()}'"
            if len(body) > BUDGETS["block_lines"]:
                warnings.append(f"{where}: {len(body)} lines > {BUDGETS['block_lines']}, split the topic")
            fields = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1] for l in body if ":" in l}
            if "keywords" not in fields and "read first" in fields:
                errors.append(f"{where}: has 'read first' but no 'keywords:' line, the router cannot find it")
            for ptr in re.split(r"\s*[·,;]\s*", fields.get("read first", "").strip()):
                if not ptr:
                    continue
                rel, _, anchor = ptr.partition("#")
                path = kb / rel.strip()
                if not path.exists():
                    errors.append(f"{where}: pointer '{ptr}' -> missing file")
                    continue
                referenced.add(path.resolve())
                if anchor:
                    heads = [slug(h) for h in re.findall(r"(?m)^#{1,6}\s+(.*)$", path.read_text(encoding="utf-8"))]
                    if not any(slug(anchor) == h or slug(anchor) in h for h in heads):
                        errors.append(f"{where}: pointer '{ptr}' -> no such section")
    pages = 0
    for sub, budget in (("topics", BUDGETS["topic_bytes"]), ("projects", BUDGETS["project_bytes"])):
        for page in sorted((kb / sub).glob("*.md")) if (kb / sub).exists() else []:
            pages += 1
            size = len(page.read_bytes())
            if size > budget:
                warnings.append(f"{sub}/{page.name}: {size // 1000} KB > {budget // 1000} KB, split it")
            if page.resolve() not in referenced:
                errors.append(f"{sub}/{page.name}: no topic block points here (unroutable page)")
    return errors, warnings, pages


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--kb", type=pathlib.Path, default=kb_dir())
    a = ap.parse_args()
    errors, warnings, pages = lint(a.kb)
    for e in errors:
        print(f"ERROR  {e}")
    for w in warnings:
        print(f"WARN   {w}")
    print(f"{pages} pages, {len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
