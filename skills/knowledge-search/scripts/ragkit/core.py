"""Shared constants and text primitives of the RAG engine. Standard library only."""
from __future__ import annotations

import hashlib
import os
import pathlib
import re

from .lang import FOLD, MONTHS, QUARTER_ORDINALS, QUARTER_PATTERNS, STOP, UPPER

SCHEMA = 4  # bump when the store layout or tokenisation changes: stores rebuild themselves
BUDGET = 8000
PARENT_MAX = 1800  # a parent is what the reader gets: a heading section, long ones cut at paragraphs
CHILD_MAX = 480  # a child is what gets searched: about the 128-token window of MiniLM models
CHILD_MIN = 160  # neighbours smaller than this merge, a lone short line has no meaning of its own
CONTEXT_MAX = 220
EMBED_CHARS = 2000
HASH_DIM = 512
STEM = 5  # stem length: 5 measured best on mixed Russian / English notes (6 splits endings)
SKIP_DIRS = {".git", ".rag", "__pycache__", "expect", "sealed"}  # predictions and sealed verdicts are read, never retrieved
SUFFIXES = (".md", ".tsv", ".txt")
MAX_FILE_BYTES = 2_000_000  # a dump this size would cost every later search far more than it could ever give back
# Identifiers that must stay one term: pg-orders-3, INC-2291, v0.3.0, orders.created, kb_rag.py. Without this a query
# for pg-orders-3 matches pg-orders-1 on its parts, KES-2291 matches INC-2291 on its number, and 1.9 matches 1.8.
ID_RE = re.compile(r"(?<![\w.])[^\W\d_]\w*(?:[-.]\w+)*[-.]\w*\d\w*(?:[-.]\w+)*"
                   r"|(?<![\w.-])[^\W\d_]\w*(?:\.[^\W\d_]\w*)+(?![\w-])"
                   r"|(?<![\w.-])\d+\.\d+(?![\w.-])")
PERIOD_RE = re.compile("|".join([
    r"\bq[1-4]\b", r"\b[1-4]q\b", r"\b20\d\d-(?:0[1-9]|1[0-2])(?:-\d\d)?\b", r"\b20\d\d\b", *QUARTER_PATTERNS]))


SENTENCE_END = re.compile(rf"(?<=[.!?\u2026])\s+(?=[\"'\u00ab(\[`]?[{UPPER}0-9])")


def lab_home() -> pathlib.Path:
    env = os.environ.get("RESEARCH_LAB_HOME")
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "research-lab"


def kb_dir() -> pathlib.Path:
    return lab_home() / "knowledge"


def normalize(text: str) -> str:
    return text.lower().translate(FOLD)


def slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.U)
    return re.sub(r"\s+", "-", text.strip())


def words(text: str) -> list:
    return [w for w in re.findall(r"\w+", normalize(text), flags=re.U) if len(w) > 1 and w not in STOP]


def is_identifier(word: str) -> bool:
    return "_" in word or "." in word or any(ch.isdigit() for ch in word)


def compound_ids(text: str) -> list:
    return [m.group(0) for m in ID_RE.finditer(normalize(text)) if len(m.group(0)) >= 4 or m.group(0)[0].isdigit()]


def stem(word: str) -> str:
    """Cut to STEM characters: a crude stemmer for any language. Identifiers stay whole (E99, RESEARCH_LAB_HOME)."""
    return word if is_identifier(word) else word[:STEM]


def tokens(text: str) -> list:
    return [stem(w) for w in words(text)] + compound_ids(text)


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:12]


def is_utf8(raw: bytes) -> bool:
    try:
        raw.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def read_text(path) -> str:
    """Text of a note. A BOM is not content. Older notes are often not UTF-8: runs of high bytes are a Cyrillic
    codepage, stray ones Latin-1, so that such a note can still be searched."""
    raw = pathlib.Path(path).read_bytes()
    if is_utf8(raw):
        return raw.decode("utf-8-sig")
    return raw.decode("cp1251" if re.search(rb"[\xc0-\xff]{3}", raw) else "latin-1", errors="replace")


def split_lines(text: str) -> list:
    """Lines as an editor and `grep -n` count them: str.splitlines also splits on form feeds and U+2028."""
    lines = re.split(r"\r\n|\r|\n", text)
    return lines[:-1] if lines and not lines[-1] else lines


def read_section(path: str, parent: dict) -> str:
    """The section as it was indexed, or "" if the file moved on since then: line numbers from an older index
    would otherwise show the wrong text under a confident verdict."""
    try:
        lines = split_lines(read_text(path))
    except OSError:
        return ""
    text = "\n".join(lines[parent["s"] - 1:parent["e"]])
    if parent.get("sha") and digest(text) != parent["sha"]:
        return ""
    if path.endswith(".tsv") and parent["s"] > 1:
        return lines[0] + "\n" + text
    return text


def matched_text(path: str, parent: dict, child: dict) -> str:
    """The child's lines within its section; a journal row is one whole."""
    text = read_section(path, parent)
    if path.endswith(".tsv"):
        return text
    return "\n".join(split_lines(text)[child["s"] - parent["s"]:child["e"] - parent["s"] + 1])


def sentences(text: str) -> list:
    out = []
    for line in text.splitlines():
        out += [s.strip() for s in SENTENCE_END.split(line) if s.strip()]
    return out


def periods(text: str) -> set:
    """Time tags a text speaks about: {'q3', 'y2026'} from 'Q3 2026', 'third quarter', and with 'm08' from '2026-08-14'
    or 'August'. Identifiers are not dates: PN-2025-0814 speaks about no year."""
    low, tags = ID_RE.sub(" ", normalize(text)), set()
    for m in PERIOD_RE.finditer(low):
        got = m.group(0)
        if re.fullmatch(r"20\d\d", got):
            tags.add("y" + got)
        elif re.match(r"20\d\d-", got):
            tags |= {"y" + got[:4], f"q{(int(got[5:7]) - 1) // 3 + 1}", f"m{int(got[5:7]):02d}"}
        elif got[0] in "qQ" or got[1:2] == "q":
            tags.add("q" + re.search(r"[1-4]", got).group(0))
        elif got[0] in "1234":
            tags.add("q" + got[0])
        else:
            tags.add(next(v for k, v in QUARTER_ORDINALS.items() if got.startswith(k)))
    for i, month in enumerate(MONTHS):
        if month.search(low):
            tags |= {f"q{i // 3 + 1}", f"m{i + 1:02d}"}
    return tags


def strip_periods(text: str) -> str:
    """The text without its time expressions, so they do not count as terms that must match."""
    low = PERIOD_RE.sub(" ", normalize(text))
    for month in MONTHS:
        low = month.sub(" ", low)
    return low
