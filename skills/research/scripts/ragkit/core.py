"""Shared constants and text primitives of the RAG engine. Standard library only."""
from __future__ import annotations

import hashlib
import os
import pathlib
import re

from .lang import FOLD, MONTHS, QUARTER_ORDINALS, QUARTER_PATTERNS, STOP, UPPER

SCHEMA = 2  # bump when the store layout or tokenisation changes: stores rebuild themselves
BUDGET = 8000
PARENT_MAX = 1800  # a parent is what the reader gets: a heading section, long ones cut at paragraphs
CHILD_MAX = 480  # a child is what gets searched: about the 128-token window of MiniLM models
CHILD_MIN = 160  # neighbours smaller than this merge, a lone short line has no meaning of its own
CONTEXT_MAX = 220
EMBED_CHARS = 2000
HASH_DIM = 512
STEM = 5  # stem length: 5 measured best on mixed Russian / English notes (6 splits endings)
SKIP_DIRS = {".git", ".rag", "__pycache__"}
SUFFIXES = (".md", ".tsv", ".txt")
PERIOD_RE = re.compile("|".join([
    r"\bq[1-4]\b", r"\b[1-4]q\b", r"\b20\d\d-(?:0[1-9]|1[0-2])(?:-\d\d)?\b", r"\b20\d\d\b", *QUARTER_PATTERNS]))


SENTENCE_END = re.compile(rf"(?<=[.!?\u2026])\s+(?=[\"'\u00ab(\[`]?[{UPPER}0-9])")


def al_home() -> pathlib.Path:
    env = os.environ.get("AUTOLEARN_HOME")
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "autolearn"


def kb_dir() -> pathlib.Path:
    return al_home() / "knowledge"


def normalize(text: str) -> str:
    return text.lower().translate(FOLD)


def slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.U)
    return re.sub(r"\s+", "-", text.strip())


def words(text: str) -> list:
    return [w for w in re.findall(r"\w+", normalize(text), flags=re.U) if len(w) > 1 and w not in STOP]


def is_identifier(word: str) -> bool:
    return "_" in word or any(ch.isdigit() for ch in word)


def stem(word: str) -> str:
    """Cut to STEM characters: a crude stemmer for any language. Identifiers stay whole (E99, AUTOLEARN_HOME)."""
    return word if is_identifier(word) else word[:STEM]


def tokens(text: str) -> list:
    return [stem(w) for w in words(text)]


def digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()[:12]


def read_text(path) -> str:
    """Text of a note. A BOM is not content; bytes that are not UTF-8 become U+FFFD, so that a caller can tell."""
    return pathlib.Path(path).read_bytes().decode("utf-8-sig", errors="replace")


def split_lines(text: str) -> list:
    """Lines as an editor and `grep -n` count them: str.splitlines also splits on form feeds and U+2028."""
    lines = re.split(r"\r\n|\r|\n", text)
    return lines[:-1] if lines and not lines[-1] else lines


def sentences(text: str) -> list:
    out = []
    for line in text.splitlines():
        out += [s.strip() for s in SENTENCE_END.split(line) if s.strip()]
    return out


def periods(text: str) -> set:
    """Time tags a text speaks about: {'q3', 'y2026'} from 'Q3 2026', 'third quarter', '2026-08-14', 'July'."""
    low, tags = normalize(text), set()
    for m in PERIOD_RE.finditer(low):
        got = m.group(0)
        if re.fullmatch(r"20\d\d", got):
            tags.add("y" + got)
        elif re.match(r"20\d\d-", got):
            tags |= {"y" + got[:4], f"q{(int(got[5:7]) - 1) // 3 + 1}"}
        elif got[0] in "qQ" or got[1:2] == "q":
            tags.add("q" + re.search(r"[1-4]", got).group(0))
        elif got[0] in "1234":
            tags.add("q" + got[0])
        else:
            tags.add(next(v for k, v in QUARTER_ORDINALS.items() if got.startswith(k)))
    for i, month in enumerate(MONTHS):
        if month.search(low):
            tags.add(f"q{i // 3 + 1}")
    return tags


def strip_periods(text: str) -> str:
    """The text without its time expressions, so they do not count as terms that must match."""
    low = PERIOD_RE.sub(" ", normalize(text))
    for month in MONTHS:
        low = month.sub(" ", low)
    return low
