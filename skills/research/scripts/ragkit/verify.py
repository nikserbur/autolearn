"""Self-RAG's faithfulness check: is a drafted answer grounded in the knowledge base? Each claim is
looked up on its own and held to one place in the notes; numbers with their units and identifiers must
appear there, negation must agree, a name the notes turn down does not count, and cited `path:line` pointers
must exist. Deterministic and lexical: it catches wrong facts in the notes' own words, not every shade of meaning.
"""
from __future__ import annotations

import pathlib
import re

from .core import is_identifier, normalize, sentences, tokens, words
from .graph import extract, plain_sentences
from .lang import CONTRAST, NEGATIONS, UNITS
from .search import CLOSE_OK, CLOSE_WEAK, Index, Query, query_vectors, read_section, rel_path

CITATION = re.compile(r"([\w./-]+\.(?:md|tsv|txt)):(\d+)(?:-(\d+))?")
NUMBER = re.compile(r"(?<![\w.,-])\d+(?:[.,]\d+)?")
NUMBER_UNIT = re.compile(r"(?<![\w.,-])(\d+(?:[.,]\d+)?)(?:\s*([^\W\d_]+))?")
PAIR_DISCOUNT = 0.9
SUPPORTED_AT, PARTIAL_AT = 0.55, 0.4
PASS_SCORE = 0.8


def split_claims(answer: str) -> list:
    text = re.sub(r"```.*?```", " ", answer, flags=re.S)
    claims = []
    for line in text.splitlines():
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)]|#{1,6})\s+", "", line).strip()
        for sent in sentences(line):
            bare = CITATION.sub(" ", sent)
            if len(words(bare)) >= 2 or NUMBER.search(bare):
                claims.append(sent)
    return claims


def numbers_of(text: str) -> set:
    return {n.replace(",", ".") for n in NUMBER.findall(text)}


def _negated(text: str) -> bool:
    return bool(NEGATIONS & set(re.findall(r"\w+", normalize(text))))


def _evidence(index: Index, claim: str, query: Query, qvec, cited: list) -> list:
    """Sections to test the claim against: the ones it cites, then the best by coverage and cosine."""
    scores = index.lexical(query.all_qs)
    cos = index.store.cosines(qvec) if qvec is not None else None
    best, covered = {}, {}
    for i in set(scores) | set(sorted(range(index.n), key=lambda n: -float(cos[index.items[n][1]["row"]]))[:5]
                               if cos is not None else ()):
        f, c, parent = index.items[i]
        if (f, c["p"]) not in covered:
            whole = set().union(*(index.fields(n) for n in index.siblings[(f, c["p"])]))
            covered[(f, c["p"])] = index.coverage(whole, query)
        row = (covered[(f, c["p"])], float(cos[c["row"]]) if cos is not None else 0.0, scores.get(i, 0.0))
        if (f, c["p"]) not in best or row > best[(f, c["p"])][0]:
            best[(f, c["p"])] = (row, f, parent)
    ranked = sorted(best.values(), key=lambda r: r[0], reverse=True)[:5]
    pinned = [r for r in best.values() if any(_covers(index, r[1], r[2], path, line) for path, line in cited)]
    return pinned + [r for r in ranked if r not in pinned]


def _same_file(index: Index, file: str, path: str) -> bool:
    rel = rel_path(index, file)
    return rel == path or rel.endswith("/" + path)


def _covers(index: Index, file: str, parent: dict, path: str, line: int) -> bool:
    return _same_file(index, file, path) and parent["s"] <= line <= parent["e"]


def _citation_problem(index: Index, cited: list) -> str:
    for path, line in cited:
        match = [v for k, v in index.store.meta["files"].items() if _same_file(index, k, path)]
        if not match:
            return f"cites {path}, which the base does not have"
        if not any(p["s"] <= line <= p["e"] for v in match for p in v["parents"]):
            return f"cites {path}:{line}, no such section"
    return ""


def unit_of(word: str) -> str:
    return next((name for name, pattern in UNITS.items() if pattern.fullmatch(word.lower())), "")


def measures(text: str) -> list:
    """[(number, unit name or "")]: 15 minutes and 15 hours are different facts that share a number."""
    return [(m.group(1).replace(",", "."), unit_of(m.group(2) or "")) for m in NUMBER_UNIT.finditer(text)]


def counted_things(text: str) -> list:
    """[(number, the word after it)], for things no table of units knows: 1200 rps, 48 partitions."""
    return [(m.group(1).replace(",", "."), m.group(2).lower()) for m in NUMBER_UNIT.finditer(text) if m.group(2)]


def _assess(index: Index, query: Query, parent: dict, text: str) -> tuple:
    """(level, best sentence, sentences, level per sentence). A claim is held to one place in the notes, or two
    lines side by side: words scattered across a section prove nothing about how they relate."""
    context = set(parent.get("htok", [])) | set(parent.get("ctok", []))
    sents = plain_sentences(text) or [text]
    own = [set(tokens(sent)) for sent in sents]
    levels = [index.coverage(found | context, query, frozenset(context - found)) for found in own]
    best = max(range(len(sents)), key=lambda i: levels[i])
    level = levels[best]
    for i in range(len(sents) - 1):
        joined = own[i] | own[i + 1]
        level = max(level, PAIR_DISCOUNT * index.coverage(joined | context, query, frozenset(context - joined)))
    return level, sents[best], sents, levels


def _number_problem(bare: str, sents: list, levels: list, best_level: float) -> str:
    for number, unit in measures(bare):
        holding = [(s, lv) for s, lv in zip(sents, levels) if number in numbers_of(s)]
        if not holding:
            found = sorted({n for s in sents for n in numbers_of(s)})[:4]
            return f"{number} not in the evidence" + (f" (it has {', '.join(found)})" if found else "")
        relevant = [s for s, lv in holding if lv >= best_level * 0.5]
        if not relevant:
            return f"{number} is in the section, but in a line about something else: {holding[0][0][:100]}"
        other = {u for s in relevant for n, u in measures(s) if n == number and u}
        if unit and other and unit not in other:
            return f"{number} {unit} against {number} {', '.join(sorted(other))} in the evidence: {relevant[0][:100]}"
    for number, word in counted_things(bare):
        for s in sents:  # "2000 rps" where the notes say "1200 rps ... and a burst of 2000"
            pairs = counted_things(s)
            if (number, word) not in pairs and any(w == word and n != number for n, w in pairs):
                return f"the evidence counts {word} differently: {s[:100]}"
    return ""


def _turned_down(bare: str, sents: list) -> str:
    """The note names it only to reject it: 'we chose PostgreSQL over MySQL' does not support 'we chose MySQL'."""
    if CONTRAST.search(normalize(bare)):
        return ""
    for name in extract(bare, "", set())[0]:
        low_name = normalize(name)
        mentions = [normalize(s) for s in sents if low_name in normalize(s)]
        if mentions and all(CONTRAST.search(m[max(m.index(low_name) - 40, 0):m.index(low_name)]) for m in mentions):
            return f"the notes turn {name} down: {mentions[0][:100]}"
    return ""


def check_claim(index: Index, claim: str, emb) -> dict:
    """{claim, verdict: supported|partial|unsupported, why, evidence, coverage} for one claim."""
    bare = CITATION.sub(" ", claim)
    cited = [(m.group(1), int(m.group(2))) for m in CITATION.finditer(claim)]
    query = Query(bare)
    qvec = query_vectors(emb)(bare) if emb is not None and index.has_vectors else None
    rows = _evidence(index, bare, query, qvec, cited)
    bad_citation = _citation_problem(index, cited)
    unknown = [t for t in query.ids if not index.df.get(t)]
    if not rows or unknown:
        why = bad_citation or (f"{', '.join(unknown)} is nowhere in the base" if unknown else "nothing in the base")
        return {"claim": claim, "verdict": "unsupported", "why": why, "evidence": "", "coverage": 0.0}
    readable = [(r, read_section(r[1], r[2])) for r in rows]
    if not readable[0][1]:
        file, parent = rows[0][1], rows[0][2]
        return {"claim": claim, "verdict": "partial", "why": "the section changed since it was indexed", "coverage": 0.0,
                "evidence": f"{rel_path(index, file)}:{parent['s']}"}
    pinned = len([r for r in rows if any(_covers(index, r[1], r[2], path, line) for path, line in cited)])
    assessed = [(_assess(index, query, r[2], text), r, text) for r, text in readable if text]
    chosen = assessed[0] if pinned else max(assessed, key=lambda x: x[0][0])
    (level, near, sents, levels), ((_, cs, _), file, parent), text = chosen
    close = index.closeness(cs)
    level = max(level, 0.65 if close >= CLOSE_OK else 0.45 if close >= CLOSE_WEAK else 0.0)
    where = f"{rel_path(index, file)}:{parent['s']}"
    verdict, why = "unsupported", "the base does not say this"
    if level >= SUPPORTED_AT:
        verdict, why = "supported", ""
    elif level >= PARTIAL_AT:
        verdict, why = "partial", "only part of the claim is in the base"
    in_section = set(tokens(text)) | set(parent.get("htok", [])) | set(parent.get("ctok", []))
    if verdict == "supported":
        place = set(tokens(f"{near} {parent.get('head', '')} {pathlib.Path(file).stem}"))
        strangers = [n for n in extract(bare, "", set())[0] if not is_identifier(normalize(n)) and not set(tokens(n)) <= place]
        if strangers:  # names are what a claim is about; an unfamiliar ordinary word is usually just phrasing
            verdict, why = "partial", f"{', '.join(strangers)} is not where the rest of this claim is: {near[:100]}"
    absent = [t for t in query.ids if t not in in_section]
    if verdict != "unsupported" and absent:
        verdict, why = "unsupported", f"{', '.join(absent)} is not in the evidence"
    number_problem = _number_problem(bare, sents + [parent.get("head", ""), pathlib.Path(file).stem],
                                     levels + [level, level], max(levels)) if verdict != "unsupported" else ""
    if number_problem:
        verdict, why = "unsupported", number_problem
    if verdict == "supported":
        if _negated(bare) != _negated(near):
            verdict, why = "partial", "negation differs from the evidence: " + near[:120]
        else:
            turned_down = _turned_down(bare, sents)
            if turned_down:
                verdict, why = "partial", turned_down
    if bad_citation:
        verdict, why = ("partial" if verdict == "supported" else verdict), bad_citation
    return {"claim": claim, "verdict": verdict, "why": why, "evidence": where, "coverage": round(level, 2)}


def verify(index: Index, answer: str, emb=None) -> dict:
    """{score, passed, claims}: score counts a partial claim as half; any unsupported claim fails the answer."""
    claims = [check_claim(index, c, emb) for c in split_claims(answer)]
    if not claims:
        return {"score": 0.0, "passed": False, "claims": []}
    value = {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}
    score = sum(value[c["verdict"]] for c in claims) / len(claims)
    passed = score >= PASS_SCORE and not any(c["verdict"] == "unsupported" for c in claims)
    return {"score": round(score, 2), "passed": passed, "claims": claims}
