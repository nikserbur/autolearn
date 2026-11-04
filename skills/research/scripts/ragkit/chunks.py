"""Decoupled chunking and Contextual Retrieval.

A parent is what the reader gets: a Markdown heading section (long ones cut at paragraphs) or a TSV
row. Children are what the search sees: paragraphs, list items and sentences small enough for the
embedding window. A section that already fits stays one child, so splitting happens only when it
helps. Each child also gets the context that cutting it out of its document took away: the
document's title and summary, the heading path and the section's lead sentence.
"""
from __future__ import annotations

import pathlib
import re

from .core import CHILD_MAX, CHILD_MIN, CONTEXT_MAX, PARENT_MAX, digest, periods, sentences, split_lines, tokens
from .lang import SUMMARY_LABELS

HEADING = re.compile(r"^(#{1,6})\s+(.*)")
SUMMARY_LINE = re.compile(rf"^(?:{'|'.join(SUMMARY_LABELS)}):\s*(.+)$", re.I)


def headings(lines: list) -> list:
    """[(line index, level, title)]; a `# comment` inside a code fence is not a heading."""
    out, fence = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else HEADING.match(line)
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def chunk_md(lines: list) -> list:
    """Parents: [(start, end, heading_path, text)] with 1-based inclusive line numbers."""
    spans, path = [], []
    heads = headings(lines)
    bounds = [h[0] for h in heads] + [len(lines)]
    if not heads or heads[0][0] > 0:
        spans.append((0, bounds[0], ""))
    for k, (i, level, title) in enumerate(heads):
        path = [p for p in path if p[0] < level] + [(level, title)]
        spans.append((i, bounds[k + 1], " > ".join(t for _, t in path)))
    chunks = []
    for s, e, head in spans:
        body = lines[s:e]
        if not "".join(l for l in body if not l.startswith("#")).strip():
            continue
        piece, p_start, size = [], s, 0
        for j, line in enumerate(body):
            if size + len(line) > PARENT_MAX and piece and not line.strip():
                chunks.append((p_start + 1, s + j, head, "\n".join(piece)))
                piece, p_start, size = [], s + j, 0
            piece.append(line)
            size += len(line) + 1
        if "".join(piece).strip():
            chunks.append((p_start + 1, e, head, "\n".join(piece)))
    return chunks


def chunk_tsv(lines: list) -> list:
    if not lines:
        return []
    head = lines[0]
    return [(n + 1, n + 1, head.replace("\t", " | "), head + "\n" + line)
            for n, line in enumerate(lines) if n and line.strip()]


def _blocks(lines: list, first: int) -> list:
    """Paragraphs as lists of (line index, line); headings and blank lines separate, code fences do not."""
    blocks, cur, fence = [], [], False
    for j, line in enumerate(lines, first):
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and (not line.strip() or HEADING.match(line)):
            if cur:
                blocks.append(cur)
            cur = []
        else:
            cur.append((j, line))
    return blocks + [cur] if cur else blocks


def _split_line(j: int, line: str) -> list:
    """A line longer than a child: (line, line, col start, col end, text) per group of sentences."""
    out, start, end = [], None, 0
    pos = 0
    for sent in sentences(line):
        at = line.find(sent, pos)
        at = pos if at < 0 else at
        pos = at + len(sent)
        if start is not None and pos - start > CHILD_MAX:
            out.append((j, j, start, end, line[start:end]))
            start = None
        if start is None:
            start = at
        end = pos
    if start is not None:
        out.append((j, j, start, end, line[start:end]))
    return out


def _split_block(block: list) -> list:
    """Pieces (first line, last line, col start, col end, text) of one paragraph; tables keep their header."""
    text = "\n".join(l for _, l in block)
    if len(text) <= CHILD_MAX:
        return [(block[0][0], block[-1][0], 0, len(block[-1][1]), text)]
    header = block[0][1] if block[0][1].lstrip().startswith("|") else ""
    rows = block[2:] if header and len(block) > 2 and set(block[1][1]) <= set("|-: ") else block
    prefix = header + "\n" if rows is not block else ""
    out, cur, size = [], [], 0
    for j, line in rows:
        if cur and size + len(line) > CHILD_MAX:
            out.append((cur[0][0], cur[-1][0], 0, len(cur[-1][1]), prefix + "\n".join(l for _, l in cur)))
            cur, size = [], 0
        if len(line) > CHILD_MAX:
            out.extend(_split_line(j, line))
            continue
        cur.append((j, line))
        size += len(line) + 1
    if cur:
        out.append((cur[0][0], cur[-1][0], 0, len(cur[-1][1]), prefix + "\n".join(l for _, l in cur)))
    return out


def split_children(lines: list, first: int) -> list:
    """Children of a parent: (first line, last line, col start, col end, text), 0-based lines."""
    pieces = [p for block in _blocks(lines, first) for p in _split_block(block)]
    merged = []
    for piece in pieces:
        last = merged[-1] if merged else None
        if last and len(last[4]) < CHILD_MIN and len(last[4]) + len(piece[4]) < CHILD_MAX \
                and last[1] != piece[0]:
            merged[-1] = (last[0], piece[1], last[2], piece[3], last[4] + "\n" + piece[4])
        else:
            merged.append(piece)
    return merged


def doc_info(path: pathlib.Path, lines: list) -> tuple:
    """(title, summary): what a chunk cut out of this file can no longer say about itself."""
    title, summary = "", ""
    for line in lines[:30]:
        m = SUMMARY_LINE.match(line.strip())
        if m:
            summary = m.group(1).strip()
            break
    found = headings(lines)
    title = found[0][2] if found else ""
    if not summary:
        for block in _blocks(lines, 0):
            first = block[0][1].strip()
            if not first.startswith(("|", "-", "*", "```", ">", "---")) and ":" not in first[:20]:
                summary = sentences(first)[0] if sentences(first) else ""
                break
    return title or re.sub(r"[-_]+", " ", path.stem), summary[:120]


def context_of(title: str, summary: str, head: str, lead: str, name: str) -> str:
    parts, seen = [], ""
    for part in (title, name, summary, head, lead):
        if part and part.lower() not in seen:
            parts.append(part)
            seen += " " + part.lower()
    return " · ".join(parts)[:CONTEXT_MAX]


def parse_file(path: pathlib.Path, text: str) -> dict:
    """Document structure for the store: title, summary, parents, each with its children and context."""
    lines = split_lines(text)
    is_tsv = path.suffix == ".tsv"
    title, summary = (re.sub(r"[-_]+", " ", path.stem), "") if is_tsv else doc_info(path, lines)
    name = re.sub(r"[-_]+", " ", path.stem)
    offsets, pos = [], 0
    for line in lines:
        offsets.append(pos)
        pos += len(line) + 1
    doc_tags = periods(f"{title} {path.stem}")
    parents = []
    for s, e, head, body in (chunk_tsv(lines) if is_tsv else chunk_md(lines)):
        kids = [(s - 1, e - 1, 0, len(lines[e - 1]), body)] if is_tsv else split_children(lines[s - 1:e], s - 1)
        if not kids:
            continue
        sents = sentences("\n".join(k[4] for k in kids[:1]))
        lead = sents[0][:100] if sents else ""
        own = periods(f"{head} {body}")
        tags = own | {t for t in doc_tags if t[0] not in {o[0] for o in own}}
        children = []
        for n, (a, b, ca, cb, kid) in enumerate(kids):
            children.append({"s": a + 1, "e": b + 1, "cs": offsets[a] + ca, "ce": offsets[b] + cb, "text": kid,
                             "ctx": context_of(title, summary, head, lead if n else "", name)})
        parents.append({"s": s, "e": e, "head": head, "htok": tokens(head), "sha": digest("\n".join(lines[s - 1:e])),
                        "ctok": tokens(f"{title} {name} {summary} {lead}"), "per": sorted(tags), "kids": children})
    return {"title": title, "summary": summary, "text": "\n".join(lines), "parents": parents}
