#!/usr/bin/env python3
"""Retrieval (RAG) with embeddings and a vector store over the autolearn knowledge base and any
research KB. Runs on the standard library; uses a real embedding model when one is installed.

    kb_rag.py search refund webhook stripe            # best chunks with path:line, within 8 KB
    kb_rag.py search --root ./research/x/KB "queue position markout"
    kb_rag.py index [--root DIR ...]                  # (re)index explicitly; the hook does it on writes
    kb_rag.py status                                  # backend, model, files, chunks

Chunks: Markdown is cut at its headings (long sections at paragraphs), .tsv files into one chunk
per row with the header attached. Every chunk gets an embedding, stored in `<KB>/.rag/`:
`meta.json` (files, chunks, backend) and `vectors.f32` (one float32 row per chunk). Re-indexing
re-embeds changed files only. Every root ever indexed stays searchable, so knowledge from all
research runs accumulates in one store.

Embedding backend ($AUTOLEARN_EMBED = auto | fastembed | sentence-transformers | hash):
auto takes fastembed, then sentence-transformers, then `hash` (stdlib signed hashing of words
and character trigrams). Model: $AUTOLEARN_EMBED_MODEL, default a multilingual MiniLM.
Changing the backend or model rebuilds the store.

Ranking is hybrid: reciprocal rank fusion of the embedding ranking and a BM25 ranking over words
(exact names, ids, numbers), times a boost for chunks holding the query's identifiers (tokens with digits: E99, PAY-123)
and for sections that a matching topic-map block points to. Each search
appends `date, query, top chunk, hit|partial|miss` to `<KB>/.router-log.tsv`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import pathlib
import re
import sys
import zlib
from array import array

BUDGET = 8000
CHUNK_MAX = 1800
EMBED_CHARS = 2000
HASH_DIM = 512
DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
K1, B = 1.2, 0.75
STEM = 5  # stem length: 5 measured best on mixed Russian / English notes (6 splits endings)
RRF_K = 60  # reciprocal rank fusion of the vector and BM25 rankings
STOP = set("a an and are as at be by for from has have how in is it its of on or that the this to "
           "was were what when where which who why will with we our you your not no do does".split())
SKIP_DIRS = {".git", ".rag", "__pycache__"}


def al_home() -> pathlib.Path:
    env = os.environ.get("AUTOLEARN_HOME")
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "autolearn"


def kb_dir() -> pathlib.Path:
    return al_home() / "knowledge"


def slug(text: str) -> str:
    text = re.sub(r"[^\w\s-]", "", text.lower(), flags=re.U)
    return re.sub(r"\s+", "-", text.strip())


def words(text: str) -> list:
    return [w for w in re.findall(r"\w+", text.lower(), flags=re.U) if len(w) > 1 and w not in STOP]


def tokens(text: str) -> list:
    """Word tokens cut to STEM characters: a crude stemmer that works for any language."""
    return [w[:STEM] for w in words(text)]


# ---------------------------------------------------------------- embeddings

class HashEmbedder:
    """Stdlib fallback: signed feature hashing of stems and character trigrams, L2-normalised."""
    id, dim = f"hash-{HASH_DIM}", HASH_DIM

    def embed(self, texts: list) -> list:
        out = []
        for text in texts:
            feats = {}
            for w in words(text):
                feats["w:" + w[:STEM]] = feats.get("w:" + w[:STEM], 0.0) + 1.0
                padded = f" {w} "
                for i in range(len(padded) - 2):
                    g = "c:" + padded[i:i + 3]
                    feats[g] = feats.get(g, 0.0) + 0.4
            v = [0.0] * HASH_DIM
            for f, c in feats.items():
                h = zlib.crc32(f.encode())
                v[h % HASH_DIM] += (1.0 if h & 0x80000000 else -1.0) * (1 + math.log(c))
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


class FastEmbedder:
    def __init__(self, model: str):
        from fastembed import TextEmbedding  # optional dependency
        cache = al_home() / "models"  # the default temp cache can be wiped and re-downloaded
        self.m, self.id = TextEmbedding(model_name=model, cache_dir=str(cache)), f"fastembed:{model}"
        self.dim = len(self.embed(["probe"])[0])

    def embed(self, texts: list) -> list:
        return [unit([float(x) for x in v]) for v in self.m.embed(texts)]


class STEmbedder:
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer  # optional dependency
        self.m, self.id = SentenceTransformer(model), f"st:{model}"
        self.dim = len(self.embed(["probe"])[0])

    def embed(self, texts: list) -> list:
        return [[float(x) for x in v] for v in self.m.encode(texts, normalize_embeddings=True)]


def unit(v: list) -> list:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embedder():
    want = os.environ.get("AUTOLEARN_EMBED", "auto").lower()
    model = os.environ.get("AUTOLEARN_EMBED_MODEL", DEFAULT_MODEL)
    for name, cls in (("fastembed", FastEmbedder), ("sentence-transformers", STEmbedder)):
        if want in ("auto", name):
            try:
                return cls(model)
            except Exception as e:  # not installed, no network for the first download, bad model
                if want == name:
                    print(f"! embedding backend {name} unavailable ({e}); using hash", file=sys.stderr)
    return HashEmbedder()


# ---------------------------------------------------------------- chunking

def chunk_md(lines: list) -> list:
    """[(start, end, heading_path, text)] with 1-based inclusive line numbers."""
    spans, path = [], []
    heads = [(i, len(m.group(1)), m.group(2).strip()) for i, l in enumerate(lines)
             for m in [re.match(r"^(#{1,6})\s+(.*)", l)] if m]
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
            if size + len(line) > CHUNK_MAX and piece and not line.strip():
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


def files(roots: list) -> list:
    out = []
    for root in roots:
        for f in sorted(root.rglob("*")):
            if f.is_file() and f.suffix in (".md", ".tsv", ".txt") and not SKIP_DIRS & set(f.parts) \
                    and not f.name.startswith("."):
                out.append(f)
    return out


# ---------------------------------------------------------------- vector store

class Store:
    def __init__(self, kb: pathlib.Path):
        self.dir = kb / ".rag"
        self.meta_path, self.vec_path = self.dir / "meta.json", self.dir / "vectors.f32"
        try:
            self.meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
            vec = array("f")
            with open(self.vec_path, "rb") as f:
                vec.frombytes(f.read())
            self.vec = vec
        except (OSError, ValueError):
            self.meta, self.vec = {"backend": None, "dim": 0, "files": {}}, array("f")

    def row(self, i: int):
        d = self.meta["dim"]
        return self.vec[i * d:(i + 1) * d]

    def update(self, roots: list, emb) -> int:
        if (self.meta["backend"], self.meta["dim"], self.meta.get("stem")) != (emb.id, emb.dim, STEM):
            self.meta, self.vec = {"backend": emb.id, "dim": emb.dim, "stem": STEM, "files": {}}, array("f")
        old_files, old_vec, d = self.meta["files"], self.vec, emb.dim
        live, changed = {}, 0
        root_keys = [str(r.resolve()) for r in roots]
        for f in files(roots):
            key = str(f.resolve())
            mtime = f.stat().st_mtime
            if old_files.get(key, {}).get("mtime") == mtime:
                live[key] = old_files[key]
                continue
            lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
            parts = chunk_tsv(lines) if f.suffix == ".tsv" else chunk_md(lines)
            vecs = emb.embed([(h + "\n" + t)[:EMBED_CHARS] for _, _, h, t in parts]) if parts else []
            live[key] = {"mtime": mtime, "chunks": [
                {"s": s, "e": e, "head": h, "tok": tokens(t), "htok": tokens(h), "new": v}
                for (s, e, h, t), v in zip(parts, vecs)]}
            changed += 1
        for key, entry in old_files.items():  # keep files of roots not passed this time
            if key not in live:
                if any(key.startswith(r) for r in root_keys) or not pathlib.Path(key).exists():
                    changed += 1
                else:
                    live[key] = entry
        if not changed:
            return 0
        vec, n = array("f"), 0
        for entry in live.values():
            for c in entry["chunks"]:
                vec.extend(c.pop("new") if "new" in c else old_vec[c["row"] * d:(c["row"] + 1) * d])
                c["row"] = n
                n += 1
        self.meta["files"], self.vec = live, vec
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp_m, tmp_v = self.meta_path.with_suffix(".tmp"), self.vec_path.with_suffix(".tmp")
        tmp_m.write_text(json.dumps(self.meta, ensure_ascii=False), encoding="utf-8")
        with open(tmp_v, "wb") as f:
            vec.tofile(f)
        os.replace(tmp_v, self.vec_path)
        os.replace(tmp_m, self.meta_path)
        return changed

    def cosines(self, q: list) -> list:
        d, n = self.meta["dim"], len(self.vec) // max(self.meta["dim"], 1)
        try:
            import numpy as np  # optional speed-up
            return list(np.frombuffer(self.vec, dtype=np.float32).reshape(n, d) @ np.asarray(q, np.float32))
        except ImportError:
            return [sum(a * b for a, b in zip(self.row(i), q)) for i in range(n)]


# ---------------------------------------------------------------- search

def map_boosts(kb: pathlib.Path, q: set) -> dict:
    """{(abs file, section slug or ''): boost} from topic-map blocks whose keywords match the query."""
    out = {}
    maps = [kb / "topic-map.md", *(sorted((kb / "map").glob("*.md")) if (kb / "map").exists() else [])]
    for m in (m for m in maps if m.exists()):
        for block in re.split(r"\n(?=## )", m.read_text(encoding="utf-8")):
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


def search(store: Store, emb, kb: pathlib.Path, query: str, k: int, use_vec: bool = True) -> tuple:
    qs = set(tokens(query))
    docs = [(f, c) for f, v in store.meta["files"].items() for c in v["chunks"]]
    if not docs or not query.strip():
        return [], qs
    cos = store.cosines(emb.embed([query])[0]) if use_vec else None
    n = len(docs)
    avg = sum(len(c["tok"]) for _, c in docs) / n or 1
    df = {t: sum(1 for _, c in docs if t in c["tok"] or t in c["htok"]) for t in qs}
    lex = []
    for f, c in docs:
        tf = {}
        for t in c["tok"] + c["htok"]:
            if t in qs:
                tf[t] = tf.get(t, 0) + 1
        dl = len(c["tok"]) or 1
        lex.append((sum(math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5)) * v * (K1 + 1)
                        / (v + K1 * (1 - B + B * dl / avg)) for t, v in tf.items()), len(tf)))
    cs_all = [float(cos[c["row"]]) if use_vec else 0.0 for _, c in docs]
    vec_rank = {i: r for r, i in enumerate(sorted(range(n), key=lambda i: -cs_all[i]), 1)} if use_vec else {}
    lex_rank = {i: r for r, i in enumerate(sorted((i for i in range(n) if lex[i][1]), key=lambda i: -lex[i][0]), 1)}
    boosts = map_boosts(kb, qs)
    ids = {t for t in qs if any(ch.isdigit() for ch in t)}  # E99, h7, pay-123: exact ids must win
    floor = 0.2 if store.meta["backend"].startswith("hash") else 0.3
    ranked = []
    for i, (f, c) in enumerate(docs):
        cs, cov = cs_all[i], lex[i][1]
        if cov == 0 and (cs < floor or not use_vec):
            continue
        last = slug(c["head"].split(" > ")[-1]) if c["head"] else ""
        boost = max(boosts.get((f, last), 1.0), boosts.get((f, ""), 1.0))
        rrf = (1 / (RRF_K + vec_rank[i]) if i in vec_rank else 0) + (1 / (RRF_K + lex_rank[i]) if i in lex_rank else 0)
        if ids:
            boost *= 1 + sum(t in c["tok"] or t in c["htok"] for t in ids) / len(ids)
        ranked.append((rrf * RRF_K * boost, f, c, cov, cs))
    ranked.sort(key=lambda r: -r[0])
    return ranked[:k], qs


def show(path: str, c: dict) -> str:
    try:
        lines = pathlib.Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return ""
    if path.endswith(".tsv") and c["s"] > 1:
        return lines[0] + "\n" + "\n".join(lines[c["s"] - 1:c["e"]])
    return "\n".join(lines[c["s"] - 1:c["e"]])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["search", "index", "status"])
    ap.add_argument("query", nargs="*")
    ap.add_argument("--kb", type=pathlib.Path, default=kb_dir())
    ap.add_argument("--root", type=pathlib.Path, action="append", default=[],
                    help="extra folder to index and search, e.g. a research KB (repeatable)")
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--budget", type=int, default=BUDGET)
    ap.add_argument("--no-log", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # consoles without UTF-8 still get the text

    a.kb.mkdir(parents=True, exist_ok=True)
    store = Store(a.kb)
    if a.cmd == "status":
        n = sum(len(v["chunks"]) for v in store.meta["files"].values())
        print(f"backend {store.meta['backend']}, dim {store.meta['dim']}, "
              f"{len(store.meta['files'])} files, {n} chunks -> {store.dir}")
        return 0
    emb = embedder()
    roots = [r for r in [a.kb, *a.root] if r.exists()]
    built = store.meta["backend"]
    # Never downgrade a model-built store because this interpreter lacks the model (e.g. the hook
    # runs the system python while fastembed lives in a venv): skip updates, rank by BM25 only.
    degraded = os.environ.get("AUTOLEARN_EMBED", "auto").lower() == "auto" and built         and not built.startswith("hash") and built != emb.id
    if degraded:
        print(f"! store was built with {built}, which this python cannot load; not re-indexing, "
              "ranking by words only. Run with the python that has the model (AUTOLEARN_PYTHON "
              "for the hook) or AUTOLEARN_EMBED=hash to rebuild.", file=sys.stderr)
    changed = 0 if degraded else store.update(roots, emb)
    if a.cmd == "index":
        n = sum(len(v["chunks"]) for v in store.meta["files"].values())
        print(f"Indexed {len(store.meta['files'])} files, {n} chunks ({changed} files updated), "
              f"backend {store.meta['backend']} -> {store.dir}")
        return 0

    query = " ".join(a.query)
    hits, qs = search(store, emb, a.kb, query, a.k, use_vec=not degraded)
    kb_abs = str(a.kb.resolve())
    rel = lambda p: pathlib.Path(p).relative_to(kb_abs).as_posix() if p.startswith(kb_abs) \
        else pathlib.Path(p).as_posix()
    outcome, top = "miss", "-"
    if not hits:
        print(f"No chunk matches '{query}'. Fall back to repo docs, doc tools, then the web, "
              "and write what you find into the KB.")
    else:
        used = 0
        top = f"{rel(hits[0][1])}:{hits[0][2]['s']}"
        best = hits[0]
        outcome = "hit" if best[3] * 2 >= max(len(qs), 1) or best[4] >= 0.5 else "partial"
        for sc, f, c, cov, cs in hits:
            loc = f"{rel(f)}:{c['s']}-{c['e']}"
            text = show(f, c)
            tag = f"[{c['head'] or '(top)'}]  score {sc:.2f} (cos {cs:.2f}, {cov}/{len(qs)} terms)"
            if used + len(text.encode()) <= a.budget:
                used += len(text.encode())
                print(f"=== {loc}  {tag}\n{text.rstrip()}\n")
            else:
                print(f"--- {loc}  {tag}  over budget -> read on demand or delegate to a subagent")
        print(f"Loaded {used / 1000:.1f} KB of {a.budget / 1000:.1f} KB budget "
              f"({'words only' if degraded else emb.id}). "
              "Cite chunks as path:line.")
    if not a.no_log:
        try:
            with open(a.kb / ".router-log.tsv", "a", encoding="utf-8") as f:
                f.write(f"{datetime.date.today()}\t{query}\t{top}\t{outcome}\n")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
