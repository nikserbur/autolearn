"""GraphRAG without a server: the second index. Each chunk stores the entities and typed relations
found in it, so the graph stays current chunk by chunk with the vectors and is assembled in memory
only for the queries that need it: multi-hop walks ("what breaks if Redis is off") and communities
for questions about the whole base ("common problems across teams").
"""
from __future__ import annotations

import itertools
import re
from collections import Counter, defaultdict, deque

from .core import normalize, sentences, stem
from .lang import FOLD, GENERIC, RELATIONS, REVERSED_RELATIONS, STOP, UPPER

ALL_PHRASES = sorted({**RELATIONS, **REVERSED_RELATIONS}, key=len, reverse=True)
VERB = re.compile(r"(?<!\w)(" + "|".join(map(re.escape, ALL_PHRASES)) + r")(?!\w)|(->|\u2192|=>)", re.I)
ENTITY = re.compile("|".join([
    r"`[^`\n]{2,60}`",                                      # `code`
    r"\b[\w.-]+/[\w./-]+\b",                                # src/reports/daily.py
    rf"\b[{UPPER}]{{2,}}-\d+\b",                             # PAY-123
    r"\b(?=[\w.]*\d)(?=[\w.]*[^\W\d_])\w[\w.]*\w\b",        # E07c, h1.2, s3
    r"\b[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+\b",                # session-service, order_id
    rf"\b[{UPPER}][\w-]*(?:\s+[{UPPER}][\w-]*)*\b",          # Redis, Order Service, ERP
]))
MAX_PER_CHUNK = 12


def key_of(name: str) -> str:
    """Graph identity of an entity: stems, so 'Order Services' and 'order-service' are one node."""
    return " ".join(stem(w) for w in re.findall(r"\w+", normalize(name)))


def _clean(surface: str) -> str:
    return surface.strip("` .,:;()[]{}\"'«»")


def _is_sentence_noise(sent: str, m: re.Match, known_names: set) -> bool:
    """A capitalised first word is usually grammar, not a name, unless the document capitalises it mid-sentence
    too, or it is a Latin name inside a sentence in another alphabet."""
    surface = m.group(0)
    if sent[:m.start()].strip(" `*_\"'«(") or " " in surface or not surface[0].isupper() or surface.isupper():
        return False
    if surface.lower() in known_names or any(ch.isdigit() or ch in "-_/." for ch in surface):
        return False
    foreign = sum(ch.isalpha() and not ch.isascii() for ch in sent) > len(sent) / 3
    return not (foreign and surface[0].isascii())


def _trim(name: str, known_names: set) -> str:
    """'See PAY-123', 'The Order Service': a capitalised sentence start is not part of the name after it."""
    words_of = name.split()
    while len(words_of) > 1:
        rest = " ".join(words_of[1:])
        first = normalize(words_of[0])
        if first in STOP or (first not in known_names and re.search(rf"\d|-|_|^[{UPPER}]{{2,}}$", rest)):
            words_of = words_of[1:]
        else:
            break
    return " ".join(words_of)


def _sentence_entities(sent: str, known_names: set) -> list:
    out = []
    for m in ENTITY.finditer(sent):
        name = _clean(m.group(0))
        trimmed = _trim(name, known_names)
        if trimmed != name:
            out.append((m.end() - len(trimmed), m.end(), trimmed))
            continue
        if len(name) < 2 or normalize(name) in STOP or normalize(name) in GENERIC:
            continue
        if not _is_sentence_noise(sent, m, known_names):
            out.append((m.start(), m.end(), name))
    return out


def _subject(left: str, ents: list, subject: str) -> str:
    """The nearest name on the left, a Latin word opening the sentence right before the verb (`Redis is used
    by`), else what the document is about; an ordinary noun makes no relation."""
    near = [e for e in ents if e[1] <= len(left) and len(left) - e[1] <= 30]
    if near:
        return near[-1][2]
    opening = re.fullmatch(r"\s*([A-Z][\w-]+)(?:\s+(?:is|are|was|were))?\s*", left)
    if opening and normalize(opening.group(1)) not in STOP | GENERIC:
        return opening.group(1)
    return subject


def _objects(right: str, offset: int, ents: list) -> list:
    clause = re.split(r"[.;:!?]|\s[-–—]\s", right, maxsplit=1)[0]
    return [e[2] for e in ents if offset <= e[0] < offset + len(clause) and e[0] - offset <= 60]


def plain_sentences(text: str) -> list:
    """Sentences without Markdown scaffolding: a table cell or a bold label starts a new sentence."""
    return sentences(re.sub(r"\s*\|\s*|\*\*|^\s*(?:[-*+]|\d+[.)]|#+)\s+", ". ", text, flags=re.M))


def extract(text: str, subject: str, known_names: set) -> tuple:
    """(entities, relations [[a, rel, b]]) of a chunk; a generic subject ('the service') becomes the document's."""
    names, rels = [], []
    for raw in plain_sentences(text):
        sent = raw.translate(FOLD)
        ents = _sentence_entities(sent, known_names)
        names += [e[2] for e in ents]
        for m in VERB.finditer(sent):
            phrase = (m.group(1) or "").lower()
            rel = "flows_to" if m.group(2) else RELATIONS.get(phrase) or REVERSED_RELATIONS[phrase]
            a = _subject(sent[:m.start()], ents, subject)
            for b in _objects(sent[m.end():], m.end(), ents):
                if not a or not b or key_of(a) == key_of(b):
                    continue
                rels.append([b, rel, a] if phrase in REVERSED_RELATIONS else [a, rel, b])
                names += [a, b]
    return list(dict.fromkeys(names))[:MAX_PER_CHUNK * 2], rels


def doc_subject(title: str, stem_name: str) -> str:
    """What a document is about when a sentence only says 'the service': a name in its title or file name."""
    for m in ENTITY.finditer(title):
        name = _clean(m.group(0))
        if normalize(name) not in STOP and not _is_sentence_noise(title, m, set()):
            return name
    if len(title.split()) <= 2 and key_of(title) == key_of(stem_name):
        return title
    return stem_name if re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+", stem_name) else ""


def mid_sentence_names(text: str) -> set:
    """Lower-cased words this document capitalises in the middle of a sentence: its names."""
    found = set()
    for sent in plain_sentences(text):
        found |= {w.lower() for w in re.findall(rf"(?<=\s)[{UPPER}][\w-]+", sent)}
    return found


class Graph:
    """The entity graph over the chunks of an index. `items` is the index's (file, child, parent) list."""

    def __init__(self, items: list):
        self.items = items
        self.names, self.chunks = defaultdict(Counter), defaultdict(set)
        self.out, self.inc = defaultdict(list), defaultdict(list)
        self.co = defaultdict(Counter)
        for i, (_, child, _) in enumerate(items):
            keys = []
            for name in child.get("ents", []):
                key = key_of(name)
                if key:
                    self.names[key][name] += 1
                    self.chunks[key].add(i)
                    keys.append(key)
            for a, b in itertools.combinations(list(dict.fromkeys(keys))[:MAX_PER_CHUNK], 2):
                self.co[a][b] += 1
                self.co[b][a] += 1
            for a, rel, b in child.get("rels", []):
                ka, kb = key_of(a), key_of(b)
                if ka and kb and ka != kb:
                    self.out[ka].append((rel, kb, i))
                    self.inc[kb].append((rel, ka, i))

    def name(self, key: str) -> str:
        return self.names[key].most_common(1)[0][0] if self.names[key] else key

    def stats(self) -> dict:
        return {"entities": len(self.names), "relations": sum(len(v) for v in self.out.values()),
                "clusters": len(self.communities())}

    def seeds(self, qs: set) -> list:
        """Named entities in the query, longest names first; a word that is everywhere says nothing."""
        limit = max(10, len(self.items) // 5)
        found = [k for k in self.names if set(k.split()) <= qs and len(self.chunks[k]) <= limit and self._is_named(k)]
        return sorted(found, key=lambda k: (-len(k.split()), len(self.chunks[k]), k))

    def _is_named(self, key: str) -> bool:
        """Written like a name, or takes part in a stated relation: not just a capitalised ordinary word."""
        return bool(self.out[key] or self.inc[key]) or any(
            n.isupper() or any(ch.isdigit() or ch in "-_/" for ch in n) for n in self.names[key])

    def walk(self, seeds: list, direction: str = "both", hops: int = 2) -> list:
        """Typed edges reachable from the seeds: [(hop, a, rel, b, chunk)]; `in` follows what depends on a seed."""
        edges, seen, queue = [], set(seeds), deque((s, 1) for s in seeds)
        while queue:
            node, hop = queue.popleft()
            if hop > hops:
                continue
            steps = []
            if direction in ("in", "both"):
                steps += [(other, rel, node, i, other) for rel, other, i in self.inc[node]]
            if direction in ("out", "both"):
                steps += [(node, rel, other, i, other) for rel, other, i in self.out[node]]
            for a, rel, b, i, nxt in steps:
                edges.append((hop, a, rel, b, i))
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, hop + 1))
        return edges

    def neighbours(self, seeds: list, limit: int = 5) -> list:
        """Fallback when nothing typed is known: chunks where the seed stands next to other entities."""
        edges = []
        for seed in seeds:
            for other, _ in self.co[seed].most_common(limit):
                shared = sorted(self.chunks[seed] & self.chunks[other])
                if shared:
                    edges.append((1, seed, "related", other, shared[0]))
        return edges

    def between(self, a: str, b: str) -> list:
        """The shortest typed path between two entities, as edges."""
        prev, queue = {a: None}, deque([a])
        while queue:
            node = queue.popleft()
            if node == b:
                break
            steps = [(rel, other, i, (node, rel, other)) for rel, other, i in self.out[node]] + \
                    [(rel, other, i, (other, rel, node)) for rel, other, i in self.inc[node]]
            for rel, other, i, edge in steps:
                if other not in prev:
                    prev[other] = (node, edge, i)
                    queue.append(other)
        if b not in prev:
            return []
        path, node = [], b
        while prev[node]:
            parent, edge, i = prev[node]
            path.append((len(path) + 1, edge[0], edge[1], edge[2], i))
            node = parent
        return path[::-1]

    def communities(self) -> list:
        """Label propagation over typed and co-occurrence links: [{keys, chunks}] with 2+ entities, biggest first."""
        weight = defaultdict(Counter)
        for a, row in self.co.items():
            for b, n in row.items():
                weight[a][b] += n
        for a, row in self.out.items():
            for _, b, _ in row:
                weight[a][b] += 3
                weight[b][a] += 3
        label = {k: k for k in sorted(weight)}
        for _ in range(15):
            moved = False
            for node in sorted(weight):
                votes = Counter()
                for other, w in weight[node].items():
                    votes[label[other]] += w
                best = min(votes, key=lambda lab: (-votes[lab], lab)) if votes else label[node]
                if best != label[node]:
                    label[node], moved = best, True
            if not moved:
                break
        groups = defaultdict(list)
        for node, lab in label.items():
            groups[lab].append(node)
        out = []
        for keys in groups.values():
            if len(keys) > 1:
                chunks = set().union(*(self.chunks[k] for k in keys))
                out.append({"keys": sorted(keys, key=lambda k: (-len(self.chunks[k]), k)), "chunks": chunks})
        return sorted(out, key=lambda c: (-len(c["chunks"]), c["keys"][0]))
