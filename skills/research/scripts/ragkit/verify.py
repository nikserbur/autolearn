"""Self-RAG's faithfulness check: is a drafted answer grounded in the knowledge base? Each claim is
looked up on its own; numbers and identifiers must appear in the evidence, negation must agree, and
cited `path:line` pointers must exist. Deterministic, so it can gate an answer before it is sent.
"""
from __future__ import annotations

import pathlib
import re

from .core import normalize, sentences, tokens, words
from .lang import NEGATIONS
from .search import CLOSE_OK, CLOSE_WEAK, Index, Query, read_section, rel_path

CITATION = re.compile(r"([\w./-]+\.(?:md|tsv|txt)):(\d+)(?:-(\d+))?")
NUMBER = re.compile(r"(?<![\w.,])\d+(?:[.,]\d+)?")
SUPPORTED_AT, PARTIAL_AT = 0.6, 0.4
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


def check_claim(index: Index, claim: str, emb) -> dict:
    """{claim, verdict: supported|partial|unsupported, why, evidence, coverage} for one claim."""
    bare = CITATION.sub(" ", claim)
    cited = [(m.group(1), int(m.group(2))) for m in CITATION.finditer(claim)]
    query = Query(bare)
    qvec = emb.embed([bare])[0] if emb is not None and index.has_vectors else None
    rows = _evidence(index, bare, query, qvec, cited)
    bad_citation = _citation_problem(index, cited)
    if not rows:
        return {"claim": claim, "verdict": "unsupported", "why": bad_citation or "nothing in the base", "evidence": "",
                "coverage": 0.0}
    (cov, cs, _), file, parent = rows[0]
    close = index.closeness(cs)
    level = max(cov, 0.65 if close >= CLOSE_OK else 0.45 if close >= CLOSE_WEAK else 0.0)
    text = read_section(file, parent)
    if not text:
        return {"claim": claim, "verdict": "partial", "why": "the section changed since it was indexed", "coverage": 0.0,
                "evidence": f"{rel_path(index, file)}:{parent['s']}"}
    title = f"{parent.get('head', '')} {pathlib.Path(file).stem}"
    missing_numbers = numbers_of(bare) - numbers_of(f"{title}\n{text}")
    where = f"{rel_path(index, file)}:{parent['s']}"
    verdict, why = "unsupported", "the base does not say this"
    if level >= SUPPORTED_AT:
        verdict, why = "supported", ""
    elif level >= PARTIAL_AT:
        verdict, why = "partial", "only part of the claim is in the base"
    if verdict != "unsupported" and missing_numbers:
        found = sorted(numbers_of(text))[:4]
        verdict = "unsupported"
        why = f"{', '.join(sorted(missing_numbers))} not in the evidence" + (f" (it has {', '.join(found)})" if found else "")
    if verdict == "supported":
        near = max(sentences(text) or [""], key=lambda s: len(set(tokens(s)) & query.qs))
        if _negated(bare) != _negated(near):
            verdict, why = "partial", "negation differs from the evidence: " + near[:120]
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
