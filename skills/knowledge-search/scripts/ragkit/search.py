"""Retrieval pipeline. Every query goes through the same steps and each step decides for itself:

    route   -> exact | local | graph | global, from the shape of the query and what the base holds
    fuse    -> reciprocal rank fusion of the vector, BM25 and (when routed) graph rankings over children
    lift    -> children to their parent sections
    rerank  -> cross-encoder if one is available and fits the language, else the built-in reranker
    grade   -> CRAG verdict CORRECT | AMBIGUOUS | INCORRECT; anything but CORRECT tries one correction
"""
from __future__ import annotations

import difflib
import math
import os
import pathlib
import re
import sys
from collections import Counter
from dataclasses import dataclass, field

from .core import (ID_RE, compound_ids, is_identifier, matched_text, normalize, periods, read_section, read_text, slug, stem,
                   strip_periods, tokens, words)
from .graph import Graph, key_of
from .lang import (AGGREGATE, AGGREGATE_WORDS, CONJUNCTIONS, IMPACT, LINK_QUESTIONS, LINK_WORDS, PERIOD_QUESTIONS, RELATION,
                   RELATION_NOISE,
                   UPSTREAM, WHOLE_BASE, WHOLE_BASE_SOFT, WHOLE_BASE_WEAK)

K1, B = 1.2, 0.75
RRF_K = 60
W_HEAD, W_CTX = 1.0, 0.5  # BM25 weight of a term in the heading path and in the contextual prefix
POOL = 50  # parents that reach the reranker
CE_TOP = 20
DEFAULT_K = {"exact": 5, "local": 5, "graph": 8, "global": 12}
CUT = 0.45  # passages below this share of the best score are noise; they stay as pointers
PER_DOC = 3  # most passages per document in a global answer
LITERAL = 7  # letters of a query word that must appear as written
# Built-in reranker: the features a cross-encoder would weigh. Chosen by a coarse grid on half of the
# golden questions of scripts/rag_eval.py and checked on the other half.
W_FUSION, W_NEAR, W_WHOLE, W_PROX, W_PHRASE, W_TITLE, W_LITERAL = 2.5, 0.6, 0.3, 0.4, 0.4, 0.3, 0.8
W_COMPLETE = 0.8  # the chunk and its context hold every term of the question
W_COS_MODEL, W_COS_HASH = 1.0, 0.5
W_SOFT = 0.6  # a term found only in a chunk's context (title, summary) says less than one in its text or heading
RARE_TERM = 0.8  # a missing term this close to the rarest in the query is what the question is about
W_CROSS = 1.0  # a cross-encoder's opinion against the built-in score, both scaled to about 1
FUZZY_WEIGHT = 0.6
HASH_FLOOR, MODEL_FLOOR = 0.2, 0.3
# How far a cosine stands above the noise floor of this model on this base, as a share of the room up to 1. Raw cosines
# mean different things under different models; this holds across the two measured. Hashing cannot vouch for a meaning.
CLOSE_OK, CLOSE_WEAK = 0.42, 0.2
UNKNOWN_SHARE = 0.5  # terms the base never saw hold this much of the query: it probably is not in the base
FOREIGN_K = 8  # across alphabets only vectors rank, and less precisely, so show more
OK_COVERAGE, WEAK_COVERAGE = 0.7, 0.45
WEAK_KINDS = {"expectation", "conjecture", "snippet"}  # never graded CORRECT on their own
CORRECT, AMBIGUOUS, INCORRECT = "CORRECT", "AMBIGUOUS", "INCORRECT"
ORDER = {INCORRECT: 0, AMBIGUOUS: 1, CORRECT: 2}
LOG_OUTCOME = {CORRECT: "hit", AMBIGUOUS: "partial", INCORRECT: "miss"}

class Query:
    """A query in the forms the pipeline needs: `qs` are the terms that must be answered, `all_qs`
    also holds time words (Q3, 2026), which help to find a chunk but say nothing about its subject."""

    def __init__(self, raw: str, drop: frozenset = frozenset()):
        self.raw = raw
        self.tags = periods(raw)
        whole_ids = compound_ids(raw)
        core = [w for w in words(strip_periods(ID_RE.sub(" ", normalize(raw)))) if w not in drop]
        self.terms = [stem(w) for w in core] + whole_ids  # pg-orders-3 is one term: its parts answer nothing
        self.surfaces = {**{stem(w): w for w in core}, **{w: w for w in whole_ids}}
        self.all_qs = set(tokens(raw)) - {stem(w) for w in drop}
        self.qs = set(self.terms) or self.all_qs
        self.ids = {t for t in self.qs if is_identifier(t) and (not t.isdigit() or len(t) >= 3)}
        self.fuzzy = {}  # corpus term -> the query term it stands in for


@dataclass
class Plan:
    route: str
    why: str
    seeds: list = field(default_factory=list)
    lex: float = 1.0
    vec: float = 1.0
    graph: float = 0.0


@dataclass
class Passage:
    file: str
    p: int
    parent: dict
    child: dict
    score: float
    cos: float
    cov: float
    matched: int
    total: int
    hop: int = 0
    near: set = field(default_factory=set)


@dataclass
class Result:
    query: str
    route: str
    why: str
    verdict: str
    confidence: float
    passages: list = field(default_factory=list)
    pointers: list = field(default_factory=list)
    paths: list = field(default_factory=list)
    themes: list = field(default_factory=list)
    clusters: list = field(default_factory=list)
    missing: list = field(default_factory=list)
    corrected: str = ""
    reranker: str = "built-in"
    foreign: bool = False
    sample: bool = False  # a question about the whole base: what is shown can never be proved complete


class Index:
    """Search view of the store: (file, child, parent) per chunk, document frequencies, the graph on demand."""

    def __init__(self, store, kb: pathlib.Path, has_vectors: bool = True, scope: list = None, retired: bool = True):
        """`scope`: the folders whose files may answer (None: every indexed folder). `retired`: whether sections
        marked invalid or superseded may answer."""
        self.store, self.kb, self.has_vectors = store, kb, has_vectors
        prefixes = None if scope is None else tuple(str(pathlib.Path(r).resolve()) + os.sep for r in scope)
        self.hidden = 0
        self.items = []
        for f, e in store.meta["files"].items():
            if prefixes is not None and not f.startswith(prefixes):
                continue
            for c in e["chunks"]:
                parent = e["parents"][c["p"]]
                if not retired and parent.get("status", "valid") != "valid":
                    self.hidden += 1
                    continue
                self.items.append((f, c, parent))
        self.n = len(self.items)
        self.avg = sum(len(c["tok"]) for _, c, _ in self.items) / self.n if self.n else 1.0
        self.df = Counter()
        self.siblings = {}
        for i, (f, c, p) in enumerate(self.items):
            self.df.update(set(c["tok"]) | set(p["htok"]) | set(p["ctok"]) | set(c.get("ltok", [])))
            self.siblings.setdefault((f, c["p"]), []).append(i)
        backend = store.meta.get("backend") or ""
        self.is_model = bool(backend) and not backend.startswith("hash") and has_vectors
        self.docs = len(store.meta["files"])
        self.noise = float(store.meta.get("noise") or MODEL_FLOOR)
        self._graph = None

    @property
    def graph(self) -> Graph:
        if self._graph is None:
            self._graph = Graph(self.items)
        return self._graph

    def closeness(self, cos: float) -> float:
        """A cosine as a share of the way from this base's noise floor to 1; 0 without a model to trust."""
        return (cos - self.noise) / (1.0 - self.noise) if self.is_model and self.noise < 1.0 else 0.0

    def idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        return math.log(1 + (self.n - df + 0.5) / (df + 0.5))

    def coverage(self, found: set, query: Query, soft: frozenset = frozenset()) -> float:
        """Share of the query's information found, weighted by rarity; a term the base never saw weighs most.
        A term only in `soft` counts for less."""
        total = sum(self.idf(t) for t in query.qs)
        if not total:
            return 0.0
        hit = sum(self.idf(t) * (FUZZY_WEIGHT if t in query.fuzzy else 1.0) * (W_SOFT if t in soft else 1.0)
                  for t in query.qs & found)
        return hit / total

    def is_foreign(self, query: Query) -> bool:
        """The query is in another alphabet than nearly all the notes, so no word of it can match. Numbers and ids
        belong to no language, and one known word is enough to say the notes do speak this one."""
        terms = [t for t in query.qs if t.isalpha()]
        vocabulary = [t for t in self.df if t.isalpha()]
        if not terms or not vocabulary or any(self.df.get(t) for t in terms):
            return False
        latin = sum(t.isascii() for t in vocabulary) / len(vocabulary)
        return all(t.isascii() for t in terms) and latin <= 0.2 or not any(t.isascii() for t in terms) and latin >= 0.8

    def unknown_share(self, query: Query) -> float:
        total = sum(self.idf(t) for t in query.qs)
        return sum(self.idf(t) for t in query.qs if not self.df.get(t)) / total if total else 0.0

    def fields(self, i: int) -> set:
        _, c, p = self.items[i]
        return set(c["tok"]) | set(p["htok"]) | set(p["ctok"]) | set(c.get("ltok", []))

    def text_fields(self, i: int) -> set:
        """What the chunk itself says, without the context added to it."""
        _, c, p = self.items[i]
        return set(c["tok"]) | set(p["htok"])

    def lexical(self, qs: set) -> dict:
        """BM25 with field weights over children: {item: score}."""
        idf = {t: self.idf(t) for t in qs}
        out = {}
        for i, (_, c, p) in enumerate(self.items):
            tf = Counter()
            for group, weight in ((c["tok"], 1.0), (p["htok"], W_HEAD), (p["ctok"], W_CTX), (c.get("ltok", []), W_CTX)):
                for t in group:
                    if t in qs:
                        tf[t] += weight
            if tf:
                dl = len(c["tok"]) or 1
                out[i] = sum(idf[t] * v * (K1 + 1) / (v + K1 * (1 - B + B * dl / self.avg)) for t, v in tf.items())
        return out


def section_lexical(index: Index, qs: set) -> dict:
    """BM25 over whole sections: {(file, parent): rank}. Finds what is spread over a section's paragraphs."""
    totals, sizes = {}, {}
    for key, kids in index.siblings.items():
        _, _, parent = index.items[kids[0]]
        tf = Counter(t for t in parent["htok"] + parent["ctok"] if t in qs)
        size = 0
        for i in kids:
            tokens_of_kid = index.items[i][1]["tok"]
            size += len(tokens_of_kid)
            tf.update(t for t in tokens_of_kid if t in qs)
        if tf:
            totals[key], sizes[key] = tf, size or 1
    avg = sum(sizes.values()) / len(sizes) if sizes else 1.0
    idf = {t: index.idf(t) for t in qs}
    scored = {key: sum(idf[t] * v * (K1 + 1) / (v + K1 * (1 - B + B * sizes[key] / avg)) for t, v in tf.items())
              for key, tf in totals.items()}
    return {key: r for r, key in enumerate(sorted(scored, key=lambda k: -scored[k]), 1)}


def map_boosts(kb: pathlib.Path, q: set) -> dict:
    """{(abs file, section slug or ''): boost} from topic-map blocks whose keywords match the query."""
    out = {}
    maps = [kb / "topic-map.md", *(sorted((kb / "map").glob("*.md")) if (kb / "map").exists() else [])]
    for m in (m for m in maps if m.exists()):
        for block in re.split(r"\n(?=## )", read_text(m)):
            kw = re.search(r"^keywords:(.*)$", block, re.M | re.I)
            rf = re.search(r"^read first:(.*)$", block, re.M | re.I)
            if not kw or not rf:
                continue
            hit = len(q & set(tokens(kw.group(1) + " " + block.split("\n", 1)[0])))
            if not hit:
                continue
            for ptr in re.split(r"\s*[·,;]\s*", rf.group(1).strip()):
                rel, _, anchor = ptr.partition("#")
                if rel:
                    out[(str((kb / rel.strip()).resolve()), slug(anchor))] = 1.0 + 0.25 * hit
    return out


def plan_query(index: Index, raw: str, forced: str = "auto") -> Plan:
    """Decide how to search from the query's shape and from what the base can answer."""
    low, query = normalize(raw), Query(raw)
    if forced != "auto":
        return _planned(index, forced, query, f"--mode {forced}")
    id_share = len(query.ids) / max(len(query.qs), 1)
    if query.ids and (id_share >= 0.5 or re.fullmatch(r"[\w.-]*\d[\w.-]*", raw.strip())):
        return _planned(index, "exact", query, "the query is an identifier")
    if index.docs >= 3:
        if WHOLE_BASE.search(low) or (WHOLE_BASE_WEAK.search(low) and AGGREGATE.search(low)):
            return _planned(index, "global", query, "the question is about the whole base")
        soft = WHOLE_BASE_SOFT.search(low) or (query.tags and PERIOD_QUESTIONS.search(low))
        named = query.ids or index.graph.seeds(query.qs) or names_something(raw, query)
        if soft and not named:
            return _planned(index, "global", query, "the question is about the whole base")
    if RELATION.search(low) or LINK_WORDS.search(low):
        linked = [s for s in index.graph.seeds(query.qs) if index.graph.has_links(s)]
        if len(linked) >= (1 if RELATION.search(low) else 2):
            plan = _planned(index, "graph", query, "the question follows links between entities")
            plan.seeds = linked
            return plan
    return _planned(index, "local", query, "a specific question")


def names_something(raw: str, query: Query) -> bool:
    """A capitalised word inside the question: "recurring retries with PayNord" is about PayNord, not the whole base."""
    return any(w[0].isupper() and stem(normalize(w)) in query.qs for w in re.findall(r"\w+", raw)[1:])


def _planned(index: Index, route: str, query: Query, why: str) -> Plan:
    plan = Plan(route, why)
    if route == "exact":
        plan.lex, plan.vec = 2.0, 0.5
    elif route == "graph":
        plan.seeds, plan.graph = index.graph.seeds(query.qs), 2.0
    return plan


def fused(index: Index, query: Query, plan: Plan, qvec, graph_rank: dict) -> dict:
    """Reciprocal rank fusion over children: {item: (score, cosine)}."""
    lex = index.lexical(query.all_qs)
    cos = index.store.cosines(qvec) if qvec is not None else None
    cs_all = [float(cos[c["row"]]) if cos is not None else 0.0 for _, c, _ in index.items]
    vec_rank = {i: r for r, i in enumerate(sorted(range(index.n), key=lambda i: -cs_all[i]), 1)} if cos is not None else {}
    lex_rank = {i: r for r, i in enumerate(sorted(lex, key=lambda i: -lex[i]), 1)}
    floor = max(MODEL_FLOOR, index.noise) if index.is_model else HASH_FLOOR
    section_rank = {}
    if cos is not None and index.is_model:  # how a whole section sits to the query; hashed vectors would add only noise
        mean = {key: sum(cs_all[i] for i in kids) / len(kids) for key, kids in index.siblings.items()}
        section_rank = {key: r for r, key in enumerate(sorted(mean, key=lambda k: -mean[k]), 1)}
    rows = {}
    for i in range(index.n):
        if i not in lex and i not in graph_rank and (cos is None or cs_all[i] < floor):
            continue
        key = (index.items[i][0], index.items[i][1]["p"])
        rrf = (plan.vec / (RRF_K + vec_rank[i]) if i in vec_rank else 0.0) \
            + (plan.vec / (RRF_K + section_rank[key]) if key in section_rank else 0.0) \
            + (plan.lex / (RRF_K + lex_rank[i]) if i in lex_rank else 0.0) \
            + (plan.graph / (RRF_K + graph_rank[i]) if i in graph_rank else 0.0)
        rows[i] = (rrf * RRF_K, cs_all[i])
    return rows


def proximity(tok: list, qs: set) -> float:
    """1 when the query's terms stand side by side in the chunk, near 0 when they are far apart."""
    pos = [(j, t) for j, t in enumerate(tok) if t in qs]
    distinct = {t for _, t in pos}
    if len(distinct) < 2:
        return 0.0
    best, have, left = len(tok), Counter(), 0
    for right, (j, t) in enumerate(pos):
        have[t] += 1
        while len(have) == len(distinct):
            best = min(best, j - pos[left][0] + 1)
            have[pos[left][1]] -= 1
            if not have[pos[left][1]]:
                del have[pos[left][1]]
            left += 1
    return min(1.0, len(distinct) / best)


def phrase_share(tok: list, terms: list) -> float:
    pairs = list(zip(terms, terms[1:]))
    if not pairs:
        return 0.0
    have = set(zip(tok, tok[1:]))
    return sum(p in have for p in pairs) / len(pairs)


def reach(index: Index, key: tuple, best: int) -> set:
    """Terms within the matched child and its neighbours: a long section only "covers" a question if
    the answer's words stand near each other, not scattered across its paragraphs."""
    kids = index.siblings[key]
    at = kids.index(best)
    window = kids[max(at - 1, 0):at + 2]
    return set().union(*(index.fields(i) for i in window)), set().union(*(index.text_fields(i) for i in window))


def lift(index: Index, query: Query, rows: dict, lex_weight: float = 1.0) -> list:
    """Children to parents, scored by the built-in reranker: what a cross-encoder looks at, in plain features."""
    groups = {}
    for i, row in rows.items():
        f, c, _ = index.items[i]
        groups.setdefault((f, c["p"]), []).append((row[0], row[1], i))
    if not groups:
        return []
    sections = section_lexical(index, query.all_qs)
    fusion = {}
    for key, kids in groups.items():
        kids.sort(reverse=True)
        fusion[key] = kids[0][0] + 0.25 * sum(k[0] for k in kids[1:3]) \
            + (lex_weight * RRF_K / (RRF_K + sections[key]) if key in sections else 0.0)
    top = max(fusion.values()) or 1.0
    boosts = map_boosts(index.kb, query.qs)
    cos_weight = W_COS_MODEL if index.is_model else W_COS_HASH
    out = []
    for (f, p), kids in groups.items():
        best = kids[0][2]
        _, child, parent = index.items[best]
        cos = max(k[1] for k in kids)
        near, in_text = reach(index, (f, p), best)
        whole = set().union(*(index.fields(i) for i in index.siblings[(f, p)]))
        cov = index.coverage(near, query, frozenset(near - in_text))
        score = W_FUSION * fusion[(f, p)] / top + W_NEAR * cov + W_WHOLE * index.coverage(whole, query) \
            + (W_COMPLETE if query.qs and query.qs <= near else 0.0) \
            + W_PROX * proximity(child["tok"], query.qs) + W_PHRASE * phrase_share(child["tok"], query.terms) \
            + W_TITLE * index.coverage(set(parent["htok"]), query) + cos_weight * max(cos, 0.0)
        last = slug(parent["head"].split(" > ")[-1]) if parent["head"] else ""
        boost = max(boosts.get((f, last), 1.0), boosts.get((f, ""), 1.0))
        if query.ids:  # exact ids must win, and the document named after one wins over those that mention it
            named = set(parent["ctok"]) | set(parent["htok"])
            boost *= 1 + (sum(t in whole for t in query.ids) + 0.5 * sum(t in named for t in query.ids)) / len(query.ids)
        if query.tags and tags_match(parent, query.tags):
            boost *= 1.5
        out.append(Passage(f, p, parent, child, score * boost, cos, cov, len(query.qs & near), len(query.qs), near=near))
    out.sort(key=lambda r: -r.score)
    return out[:POOL]


def literal_share(index: Index, query: Query, passage: Passage) -> float:
    """How much of the query the section's text holds as written, not just by stem: a 5-letter stem takes
    `contradicted` for `contributing`, seven letters do not."""
    text = normalize(read_section(passage.file, passage.parent))
    pairs = [(w, index.idf(t)) for t, w in query.surfaces.items() if t in query.qs]
    total = sum(weight for _, weight in pairs)
    if not total:
        return 0.0
    return sum(weight for w, weight in pairs if (w if is_identifier(w) else w[:LITERAL]) in text) / total


def literal_rerank(index: Index, query: Query, passages: list) -> list:
    """The expensive feature, for the head of the list only: it has to read the sections."""
    head = passages[:CE_TOP]
    for p in head:
        p.score += W_LITERAL * literal_share(index, query, p)
    return sorted(head, key=lambda r: -r.score) + passages[CE_TOP:]


def tags_match(parent: dict, tags: set) -> bool:
    """Quarters must match; a section that names no year or month at all can still be from the one asked about."""
    have = set(parent.get("per", []))
    return all(t in have or (t[0] in "ym" and not any(h[0] == t[0] for h in have)) for t in tags)


def is_latin(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return sum(ch.isascii() for ch in letters) >= 0.8 * len(letters)


def cross_rerank(passages: list, query: Query, ce) -> tuple:
    """Reorder the head of the list with a cross-encoder when it can read this language."""
    if not ce or len(passages) < 2:
        return passages, "built-in"
    head = passages[:CE_TOP]
    docs = [(p.parent["head"] + "\n" + matched_text(p.file, p.parent, p.child))[:800] for p in head]
    readable = list(range(len(head)))
    if ce.english_only:
        if not is_latin(query.raw):
            return passages, "built-in (the cross-encoder reads English only)"
        readable = [i for i in readable if is_latin(docs[i])]
    if len(readable) < 2:
        return passages, "built-in"
    try:
        marks = ce.score(query.raw, [docs[i] for i in readable])
    except Exception as e:  # a model failure must not lose the answer
        print(f"! cross-encoder failed ({e}); using the built-in reranker", file=sys.stderr)
        return passages, "built-in"
    low, high = min(marks), max(marks)
    span = (high - low) or 1.0
    top = head[0].score or 1.0
    for p in head:
        p.score /= top
    for i, mark in zip(readable, marks):
        head[i].score += W_CROSS * (mark - low) / span
    return sorted(head, key=lambda p: -p.score) + passages[CE_TOP:], ce.id


def cosine_level(index: Index, query: Query, top: Passage) -> float:
    """What a close vector can vouch for. Across alphabets it is all there is, and never proof. Otherwise, terms
    the base has never seen outweigh it: any machine-learning note sits near a question about gradient boosting.
    And a vector alone never makes a CORRECT: some of the question's words have to be there too."""
    close = index.closeness(top.cos)
    if index.is_foreign(query):
        return WEAK_COVERAGE if close >= CLOSE_WEAK else 0.0
    if index.unknown_share(query) >= UNKNOWN_SHARE:
        return WEAK_COVERAGE if close >= CLOSE_OK else 0.0
    if close >= CLOSE_OK and top.cov >= 0.3:
        return OK_COVERAGE
    return WEAK_COVERAGE if close >= CLOSE_WEAK else 0.0


def misses_key_term(index: Index, query: Query, found: set) -> bool:
    """The rarest words are what a question is about: "the guest Wi-Fi password" is not answered by a note on the
    guest Wi-Fi, however much of the question it covers."""
    if len(query.qs) < 2:
        return False
    rarest = max(index.idf(t) for t in query.qs)
    return any(index.idf(t) >= RARE_TERM * rarest for t in query.qs - found)


def grade(index: Index, query: Query, top: Passage) -> tuple:
    """CRAG: (verdict, confidence) from how much of the query the best passage answers."""
    if top is None:
        return INCORRECT, 0.0
    whole = set().union(*(index.fields(i) for i in index.siblings[(top.file, top.p)]))
    level = max(top.cov, cosine_level(index, query, top))
    if any(not index.df.get(t) for t in query.ids):
        level = min(level, WEAK_COVERAGE if (query.qs - query.ids) & whole else 0.0)
    elif query.ids and not query.ids & whole:
        level = min(level, OK_COVERAGE - 0.01)
    elif misses_key_term(index, query, top.near):
        level = min(level, OK_COVERAGE - 0.01)
    if top.parent.get("kind") in WEAK_KINDS or top.parent.get("status", "valid") != "valid":
        level = min(level, OK_COVERAGE - 0.01)  # a prediction or a guess can point the way, never settle a question
    if level >= OK_COVERAGE:
        return CORRECT, round(level, 2)
    return (AMBIGUOUS if level >= WEAK_COVERAGE else INCORRECT), round(level, 2)


def missing_terms(index: Index, query: Query, top: Passage) -> list:
    if top is None:
        return sorted(query.surfaces.values())
    whole = set().union(*(index.fields(i) for i in index.siblings[(top.file, top.p)]))
    return [query.surfaces.get(t, t) for t in query.terms if t not in whole]


def cut(passages: list, k: int) -> tuple:
    """Keep what is close to the best score; the rest stays as pointers. Graph evidence is never cut."""
    if not passages:
        return [], []
    limit = CUT * passages[0].score
    keep = ([p for p in passages if p.hop] + [p for p in passages if not p.hop and p.score >= limit])[:k]
    rest = [p for p in passages if p not in keep][:max(0, 8 - len(keep))]
    return keep, rest


def retrieve_local(index: Index, query: Query, plan: Plan, qvec, ce, k: int) -> Result:
    passages = literal_rerank(index, query, lift(index, query, fused(index, query, plan, qvec, {}), plan.lex))
    reranker = "built-in"
    if plan.route != "exact":
        passages, reranker = cross_rerank(passages, query, ce)
    keep, rest = cut(passages, k)
    verdict, confidence = grade(index, query, keep[0] if keep else None)
    return Result(query.raw, plan.route, plan.why, verdict, confidence, keep, rest,
                  missing=missing_terms(index, query, keep[0] if keep else None), reranker=reranker)


def graph_edges(index: Index, plan: Plan, low: str) -> list:
    graph, seeds = index.graph, plan.seeds
    if len(seeds) >= 2:
        path = graph.between(seeds[0], seeds[1])
        if path:
            return path
    direction = "out" if UPSTREAM.search(low) else "impact" if IMPACT.search(low) else "both"
    return graph.walk(seeds[:3], direction) or graph.neighbours(seeds[:3])


def render_edges(index: Index, edges: list) -> list:
    graph, lines = index.graph, []
    for _, a, rel, b, i in sorted(edges, key=lambda e: e[0])[:12]:
        f, c, _ = index.items[i]
        line = f"{graph.name(a)} --{rel}--> {graph.name(b)}  ({rel_path(index, f)}:{c['s']})"
        if line not in lines:
            lines.append(line)
    return lines


def rel_path(index: Index, path: str) -> str:
    kb_abs = str(index.kb.resolve())
    return pathlib.Path(path).relative_to(kb_abs).as_posix() if path.startswith(kb_abs + os.sep) else pathlib.Path(path).as_posix()


def residual_terms(query: Query, seeds: list) -> Query:
    """What a relation question asks beyond its entities and its relation words: "how does Kafka affect the
    office coffee machine" is about a coffee machine, and a link from Kafka to something else does not answer it."""
    names = {t for key in seeds for t in key.split()}
    plain = ID_RE.sub(" ", normalize(query.raw))
    for signal in (RELATION, IMPACT, UPSTREAM, LINK_QUESTIONS):
        plain = signal.sub(" ", plain)
    kept = [w for w in words(plain)
            if stem(w) not in names and w not in RELATION_NOISE
            and not (RELATION.search(w) or IMPACT.search(w) or UPSTREAM.search(w))]
    return Query(" ".join(kept))


def graph_verdict(index: Index, query: Query, plan: Plan, edges: list, keep: list) -> tuple:
    """A stated link answers a question about links, and only that: what else was asked must be in the evidence too.
    The links are real either way, so a gap makes this partial, not a refusal."""
    if not any(e[2] != "related" for e in edges):
        return (*grade(index, query, keep[0] if keep else None), [])
    rest = residual_terms(query, plan.seeds)
    if not rest.qs:
        return CORRECT, 0.8, []
    found = set().union(*(p.near for p in keep)) if keep else set()
    level = index.coverage(found, rest)
    missing = [rest.surfaces.get(t, t) for t in rest.terms if t not in found]
    if level >= OK_COVERAGE and not misses_key_term(index, rest, found):
        return CORRECT, 0.8, missing
    return AMBIGUOUS, round(level, 2), missing


def subject_chunks(index: Index, edges: list) -> dict:
    """{item: hop} for the documents about the entities a walk reached: "what does gateway depend on" should show
    what auth-svc is, not only the line of gateway's page that names it."""
    first = {}
    for f, entry in index.store.meta["files"].items():
        if entry.get("subject"):
            first.setdefault(key_of(entry["subject"]), f)
    out = {}
    for hop, a, _, b, _ in edges:
        for key in (a, b):
            f = first.get(key)
            kids = index.siblings.get((f, 0)) if f else None
            if kids:
                out.setdefault(kids[0], hop + 1)
    return out


def retrieve_graph(index: Index, query: Query, plan: Plan, qvec, ce, k: int) -> Result:
    edges = graph_edges(index, plan, normalize(query.raw))
    hops = {}
    for hop, *_rest, i in sorted(edges, key=lambda e: e[0]):
        hops.setdefault(i, hop)
    for i, hop in subject_chunks(index, edges).items():
        hops.setdefault(i, hop)
    rank = {i: r for r, i in enumerate(hops, 1)}
    passages = lift(index, query, fused(index, query, plan, qvec, rank))
    section_hop = {}
    for i, hop in hops.items():
        f, c, _ = index.items[i]
        section_hop.setdefault((f, c["p"]), hop)
    for p in passages:
        p.hop = section_hop.get((p.file, p.p), 0)
        if p.hop:
            p.score += 1.5 / p.hop
    passages.sort(key=lambda r: (-(1.0 / r.hop if r.hop else 0.0), -r.score))
    keep, rest = cut(passages, k)
    verdict, confidence, missing = graph_verdict(index, query, plan, edges, keep)
    named = plan.seeds[:2]
    if len(named) == 2 and LINK_QUESTIONS.search(normalize(query.raw)) and not index.graph.between(*named):
        verdict, confidence = AMBIGUOUS, 0.5
        missing = [f"a stated link between {index.graph.name(named[0])} and {index.graph.name(named[1])}", *missing]
    return Result(query.raw, "graph", plan.why, verdict, confidence, keep, rest, paths=render_edges(index, edges),
                  missing=missing)


def surfaces_of(text: str) -> dict:
    out = {}
    for w in words(text):
        out.setdefault(stem(w), Counter())[w] += 1
    return out


def themes_of(index: Index, query: Query, passages: list) -> list:
    """What recurs across documents in the selected sections: [{term, docs, of, evidence}], most widespread first."""
    per_term, surfaces = {}, {}
    for p in passages:
        text = read_section(p.file, p.parent)
        for term, forms in surfaces_of(text).items():
            surfaces.setdefault(term, Counter()).update(forms)
            if term not in query.qs and len(term) > 1 and not term.isdigit():
                per_term.setdefault(term, {}).setdefault(p.file, p)
    of = len({p.file for p in passages})
    ranked = sorted((t for t, d in per_term.items() if len(d) >= 2), key=lambda t: (-len(per_term[t]), -index.idf(t), t))
    return [{"term": surfaces[t].most_common(1)[0][0], "docs": len(per_term[t]), "of": of,
             "evidence": [f"{rel_path(index, f)}:{p.parent['s']}" for f, p in list(per_term[t].items())[:4]]}
            for t in ranked[:8]]


def clusters_of(index: Index, files: set) -> list:
    """Communities of entities that span the selected documents."""
    out = []
    for community in index.graph.communities():
        docs = {index.items[i][0] for i in community["chunks"]}
        shared = docs & files if files else docs
        if len(shared) >= 2:
            out.append({"entities": [index.graph.name(k) for k in community["keys"][:6]], "docs": len(shared)})
    return out[:4]


def overview_passages(index: Index, query: Query) -> list:
    """No anchor in the query ('main themes of the base'): one section from each large community."""
    out, seen = [], set()
    for community in index.graph.communities()[:6]:
        for i in sorted(community["chunks"], key=lambda n: -len(index.items[n][1].get("ents", []))):
            f, c, p = index.items[i]
            if (f, c["p"]) not in seen:
                seen.add((f, c["p"]))
                out.append(Passage(f, c["p"], p, c, 1.0, 0.0, 0.0, 0, len(query.qs)))
                break
    return out


def spread(passages: list, k: int) -> list:
    """Round robin over documents, so one chatty file cannot crowd the others out of the answer."""
    by_doc = {}
    for p in passages:
        by_doc.setdefault(p.file, []).append(p)
    out = []
    for depth in range(PER_DOC):
        out += [rows[depth] for rows in by_doc.values() if depth < len(rows)]
    return out[:k]


def absent_words(query: Query, passages: list) -> list:
    """Query words none of these sections holds as written. A 5-letter stem can take `blocked` for `blockchain`, and
    an honest "the base says nothing about blockchain" is worth more than a section on something else."""
    texts = [normalize(read_section(p.file, p.parent)) for p in passages]
    return [w for t, w in query.surfaces.items() if t in query.qs
            and not any((w if is_identifier(w) else w[:LITERAL]) in text for text in texts)]


def retrieve_global(index: Index, raw: str, plan: Plan, qvec, k: int) -> Result:
    """GraphRAG global search, map step: evidence from every document that has any, with what recurs across them.
    The reduce step belongs to the reader, who has the themes and the sections side by side. Such an answer is a
    sample of the base and nothing here can prove it complete, so it is never graded CORRECT."""
    query = Query(raw, drop=frozenset(AGGREGATE_WORDS))
    passages = literal_rerank(index, query, lift(index, query, fused(index, query, plan, qvec, {})))
    if query.tags:
        passages = [p for p in passages if tags_match(p.parent, query.tags)]
        if not passages:
            return Result(raw, "global", plan.why, INCORRECT, 0.0, missing=["anything about the stated period"])
    relevant = [p for p in passages if query.tags or p.cov >= 0.34 or index.closeness(p.cos) >= CLOSE_WEAK]
    selected = spread(relevant, k)
    note = ""
    if not selected:
        if query.qs:
            return Result(raw, "global", plan.why, INCORRECT, 0.0, passages[:2], missing=sorted(query.surfaces.values()))
        selected, note = overview_passages(index, query), " (overview by entity clusters)"
    if not selected:
        return Result(raw, "global", plan.why, INCORRECT, 0.0)
    absent = absent_words(query, selected)
    if absent and not query.tags and not index.is_model and index.coverage(set(query.qs) - {stem(w) for w in absent}, query) < WEAK_COVERAGE:
        return Result(raw, "global", plan.why, INCORRECT, 0.0, selected[:2], missing=absent)
    solid = sum(p.cov >= 0.5 or index.closeness(p.cos) >= CLOSE_OK for p in selected) / len(selected)
    return Result(raw, "global", plan.why + note, AMBIGUOUS, round(solid, 2), selected, [], missing=absent, sample=True,
                  themes=themes_of(index, query, relevant or selected), clusters=clusters_of(index, {p.file for p in selected}))


def split_parts(raw: str) -> list:
    parts = [p.strip() for p in re.split(rf"[;?]|\s(?:{'|'.join(CONJUNCTIONS)})\s", raw) if p.strip()]
    return parts if len(parts) >= 2 and all(tokens(p) for p in parts) else []


def fuzzy_query(index: Index, raw: str):
    """Terms the base never saw, replaced by the closest vocabulary term: an inflected form that the 5-letter stem
    split from its other forms, or a typo."""
    query = Query(raw)
    vocab = sorted(index.df)
    swapped = []
    for term in list(query.qs):
        if index.df.get(term) or len(term) < 4 or term in query.ids:
            continue
        near = [v for v in vocab if v[:4] == term[:4]] or difflib.get_close_matches(term, vocab, 1, 0.8)
        if near:
            best = max(near, key=lambda v: index.df[v])
            query.fuzzy[best] = term
            swapped.append((term, best))
    for term, best in swapped:
        query.qs.discard(term)
        query.all_qs.discard(term)
        query.qs.add(best)
        query.all_qs.add(best)
        query.terms = [best if t == term else t for t in query.terms]
        query.surfaces[best] = query.surfaces.get(term, term)
    return query if swapped else None


def query_vectors(emb):
    """A model that fails at query time costs the vector ranking, not the answer: words still work."""
    def embed(text: str):
        try:
            return emb.embed([text])[0]
        except Exception as e:
            print(f"! the embedding model failed ({e}); ranking by words only", file=sys.stderr)
            return None
    return embed


def answer(index: Index, raw: str, emb=None, ce=None, mode: str = "auto", k: int = 0) -> Result:
    """The whole pipeline for one query, with one corrective pass when the first grade is not CORRECT."""
    if not raw.strip() or not index.n:
        return Result(raw, "local", "nothing to search", INCORRECT, 0.0)
    plan = plan_query(index, raw, mode)
    embed = query_vectors(emb) if emb is not None and index.has_vectors else (lambda text: None)
    foreign = index.is_foreign(Query(raw))
    want = k or (FOREIGN_K if index.is_model and foreign else DEFAULT_K[plan.route])
    qvec = embed(raw)
    if plan.route == "global":
        result = retrieve_global(index, raw, plan, qvec, want)
    elif plan.route == "graph" and plan.seeds:
        result = retrieve_graph(index, Query(raw), plan, qvec, ce, want)
    else:
        result = retrieve_local(index, Query(raw), plan, qvec, ce, want)
        result = result if result.verdict == CORRECT else _corrected(index, raw, plan, embed, qvec, ce, want, result)
    result.foreign = foreign
    return result


def _corrected(index: Index, raw: str, plan: Plan, embed, qvec, ce, k: int, first: Result) -> Result:
    """CRAG correction inside the base: split a compound question, then repair terms the base never saw."""
    best = first
    parts = split_parts(raw)
    if parts:
        subs = [retrieve_local(index, Query(p), plan, embed(p), ce, k) for p in parts]
        if any(s.verdict != INCORRECT for s in subs):
            merged = _merge(raw, plan, subs, parts, k)
            if ORDER[merged.verdict] >= ORDER[best.verdict]:
                best = merged
    if best.verdict != CORRECT:
        repaired = fuzzy_query(index, raw)
        if repaired:
            alt = retrieve_local(index, repaired, plan, qvec, ce, k)
            if ORDER[alt.verdict] > ORDER[best.verdict]:
                alt.corrected = "repaired terms: " + ", ".join(f"{v} -> {t}" for t, v in repaired.fuzzy.items())
                best = alt
    return best


def _merge(raw: str, plan: Plan, subs: list, parts: list, k: int) -> Result:
    passages, seen = [], set()
    for row in zip(*(s.passages[:k] + [None] * (k - len(s.passages[:k])) for s in subs)):
        for p in row:
            if p and (p.file, p.p) not in seen:
                seen.add((p.file, p.p))
                passages.append(p)
    missing_parts = [part for part, s in zip(parts, subs) if s.verdict == INCORRECT]
    verdict = CORRECT if all(s.verdict == CORRECT for s in subs) else AMBIGUOUS
    return Result(raw, plan.route, plan.why, verdict, round(sum(s.confidence for s in subs) / len(subs), 2),
                  passages[:k], [], missing=missing_parts, corrected=f"split into {len(parts)} questions",
                  reranker=subs[0].reranker)
