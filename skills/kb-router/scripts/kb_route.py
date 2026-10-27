#!/usr/bin/env python3
"""Deterministic router over the autolearn knowledge base. Standard library only.

    kb_route.py refund webhook stripe            # route keywords, log the result
    kb_route.py --no-log --budget 12000 refunds  # dry look, bigger budget

Prints the best matching topic blocks from topic-map.md (and area maps in map/), resolves each
`read first: file#section` pointer to an exact line range with its size, and gives ready-to-run
`sed -n` commands that stay within the byte budget. Appends hit/partial/miss to .router-log.tsv.
"""
from __future__ import annotations

import argparse
import datetime
import os
import pathlib
import re
import shlex
import sys

BUDGET = 8000


def kb_dir() -> pathlib.Path:
    env = os.environ.get("AUTOLEARN_HOME")
    base = pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "autolearn"
    return base / "knowledge"


def slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.U)
    return re.sub(r"\s+", "-", text.strip())


def blocks(map_file: pathlib.Path) -> list:
    """[(heading, body_lines, start_line)] for every '## ' block."""
    out, cur = [], None
    for n, line in enumerate(map_file.read_text(encoding="utf-8").splitlines(), 1):
        if line.startswith("## "):
            cur = [line[3:].strip(), [], n]
            out.append(cur)
        elif cur is not None:
            cur[1].append(line)
    return out


def field(body: list, name: str) -> str:
    for line in body:
        if line.lower().startswith(name + ":"):
            return line.split(":", 1)[1].strip()
    return ""


def score(heading: str, body: list, words: list) -> int:
    hay_kw = (field(body, "keywords") + " " + heading).lower()
    hay_all = (heading + " " + " ".join(body)).lower()
    return sum(3 if w in hay_kw else 1 if w in hay_all else 0 for w in words)


def section_range(path: pathlib.Path, anchor: str):
    """(start, end, bytes) of the section whose heading slug matches anchor; whole file if none."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not anchor:
        return 1, len(lines), sum(len(l) + 1 for l in lines)
    heads = [(i, len(m.group(1)), slug(m.group(2))) for i, l in enumerate(lines)
             for m in [re.match(r"^(#{1,6})\s+(.*)", l)] if m]
    want = slug(anchor)
    for k, (i, level, s) in enumerate(heads):
        if s == want or want in s:
            end = next((j for j, lv, _ in heads[k + 1:] if lv <= level), len(lines))
            return i + 1, end, sum(len(l) + 1 for l in lines[i:end])
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("keywords", nargs="+")
    ap.add_argument("--kb", type=pathlib.Path, default=kb_dir())
    ap.add_argument("--budget", type=int, default=BUDGET)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--no-log", action="store_true")
    a = ap.parse_args()

    words = [w.lower() for w in a.keywords if len(w) > 1]
    areas = sorted((a.kb / "map").glob("*.md")) if (a.kb / "map").exists() else []
    maps = [m for m in [a.kb / "topic-map.md", *areas] if m.exists()]
    if not maps:
        print(f"No topic map at {a.kb / 'topic-map.md'}. Build one with the kb-ingest skill.")
        return 0

    ranked = sorted(((score(h, b, words), h, b, n, m) for m in maps for h, b, n in blocks(m)),
                    key=lambda t: -t[0])
    hits = [r for r in ranked if r[0] > 0][: a.top]
    outcome, topic = "miss", "-"
    if not hits:
        print("No topic matches. Headings available:")
        for _, h, _, n, m in ranked[:60]:
            print(f"  {m.name}:{n}  {h}")
    else:
        topic = hits[0][1]
        used, resolved_any = 0, False
        for sc, heading, body, n, m in hits:
            print(f"## {heading}   (score {sc}, {m.name}:{n})")
            for line in body:
                if line.strip():
                    print(f"   {line}")
            for ptr in re.split(r"\s*[·,;]\s*", field(body, "read first")):
                if not ptr:
                    continue
                rel, _, anchor = ptr.partition("#")
                path = a.kb / rel.strip()
                if not path.exists():
                    print(f"   ! {ptr}: file missing (fix with kb-ingest)")
                    continue
                rng = section_range(path, anchor.strip())
                if rng is None:
                    print(f"   ! {ptr}: section not found (fix with kb-ingest)")
                    continue
                resolved_any = True
                s, e, size = rng
                fits = used + size <= a.budget
                used += size if fits else 0
                verdict = "load" if fits else "over budget -> delegate to a subagent"
                print(f"   -> {ptr}: lines {s}-{e}, {size / 1000:.1f} KB, {verdict}")
                if fits:
                    print(f"      sed -n '{s},{e}p' {shlex.quote(str(path))}")
            print()
        print(f"Planned load: {used / 1000:.1f} KB of {a.budget / 1000:.1f} KB budget.")
        outcome = "hit" if resolved_any else "partial"

    if not a.no_log:
        try:
            with open(a.kb / ".router-log.tsv", "a", encoding="utf-8") as f:
                f.write(f"{datetime.date.today()}\t{' '.join(words)}\t{topic}\t{outcome}\n")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
