"""Language data from lang.json, joined across languages. Adding a language touches only that file."""
from __future__ import annotations

import json
import pathlib
import re

_BLOCKS = list(json.loads(pathlib.Path(__file__).with_name("lang.json").read_text(encoding="utf-8"))["languages"].values())
UPPER = "A-Z\u00c0-\u00de\u0391-\u03a9\u0400-\u042f"  # capitals of the Latin, Greek and Cyrillic alphabets


def _words(key: str) -> set:
    return {w for b in _BLOCKS for w in b[key].split()}


def _regex(key: str) -> re.Pattern:
    return re.compile("|".join(b[key] for b in _BLOCKS if b[key]))


FOLD = str.maketrans({**{k: v for b in _BLOCKS for k, v in b["fold"].items()},
                      **{k.upper(): v.upper() for b in _BLOCKS for k, v in b["fold"].items()}})
STOP = _words("stop_words")
GENERIC = _words("generic_subjects")
NEGATIONS = _words("negations")
AGGREGATE_WORDS = _words("aggregate_words")
SUMMARY_LABELS = [label for b in _BLOCKS for label in b["summary_labels"]]
CONJUNCTIONS = [c for b in _BLOCKS for c in b["conjunctions"]]
WHOLE_BASE = _regex("whole_base")
WHOLE_BASE_WEAK = re.compile(r"\b(?:" + "|".join(b["whole_base_weak"] for b in _BLOCKS) + r")\b")
AGGREGATE = _regex("aggregate_nouns")
RELATION = _regex("relation_signals")
IMPACT = _regex("impact_signals")
UPSTREAM = _regex("upstream_signals")
QUARTER_ORDINALS = {k: v for b in _BLOCKS for k, v in b["quarter_ordinals"]["values"].items()}
QUARTER_PATTERNS = [b["quarter_ordinals"]["pattern"] for b in _BLOCKS] + [b["quarter_digit"] for b in _BLOCKS if b["quarter_digit"]]
MONTHS = [re.compile(r"\b(?:" + "|".join(b["months"][i] for b in _BLOCKS if b["months"][i]) + r")\b") for i in range(12)]
PROBES = [q for b in _BLOCKS for q in b["probes"]]
RELATIONS = {phrase: rel for b in _BLOCKS for rel, phrases in b["relations"].items() for phrase in phrases}
REVERSED_RELATIONS = {phrase: rel for b in _BLOCKS for phrase, rel in b["reversed_relations"].items()}
