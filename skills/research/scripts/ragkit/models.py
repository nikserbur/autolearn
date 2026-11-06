"""Embedding and cross-encoder backends, found at run time. The engine works without any of them:
embeddings fall back to stdlib hashing, the cross-encoder to the built-in reranker, late chunking to
plain contextual embeddings.

$AUTOLEARN_EMBED = auto | fastembed | sentence-transformers | hash, model in $AUTOLEARN_EMBED_MODEL.
$AUTOLEARN_RERANK = auto | fastembed | sentence-transformers | off, model in $AUTOLEARN_RERANK_MODEL.
$AUTOLEARN_LATE = auto | off: late chunking, used when a model exposes token vectors and sees at
least LATE_MIN_WINDOW tokens at once (a smaller window has no document context left to give).
"""
from __future__ import annotations

import bisect
import importlib.util
import math
import os
import re
import sys
import zlib

from .core import HASH_DIM, STEM, al_home, words

DEFAULT_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
FAST_RERANK = "Xenova/ms-marco-MiniLM-L-6-v2"
ST_RERANK = "cross-encoder/ms-marco-MiniLM-L-6-v2"
LATE_MIN_WINDOW = 256
# Hugging Face's Xet transfer hung forever behind a VPN in testing, with a 0-byte file and no error. Plain HTTP took 49 s.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")


def unit(v: list) -> list:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class HashEmbedder:
    """Stdlib fallback: signed feature hashing of stems and character trigrams, L2-normalised."""
    id, dim = f"hash-{HASH_DIM}", HASH_DIM
    late_window = 0

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
            out.append(unit(v))
        return out


class FastEmbedder:
    def __init__(self, model: str, download: bool = True):
        from fastembed import TextEmbedding  # optional dependency
        cache = al_home() / "models"  # the default temp cache can be wiped and re-downloaded
        kwargs = {} if download else {"local_files_only": True}
        self.m, self.id = TextEmbedding(model_name=model, cache_dir=str(cache), **kwargs), f"fastembed:{model}"
        self.dim = len(self.embed(["probe"])[0])
        try:
            self.late_window = int(self.m.model.tokenizer.truncation["max_length"])
        except (AttributeError, KeyError, TypeError, ValueError):  # another fastembed layout: no token vectors
            self.late_window = 0

    def embed(self, texts: list) -> list:
        return [unit([float(x) for x in v]) for v in self.m.embed(texts)]

    def token_vectors(self, text: str) -> tuple:
        offsets = self.m.model.tokenizer.encode(text).offsets
        return offsets, self.m.model.onnx_embed([text]).model_output[0]


class STEmbedder:
    def __init__(self, model: str, download: bool = True):
        from sentence_transformers import SentenceTransformer  # optional dependency
        kwargs = {} if download else {"local_files_only": True}
        self.m, self.id = SentenceTransformer(model, **kwargs), f"st:{model}"
        self.dim = len(self.embed(["probe"])[0])
        self.late_window = int(getattr(self.m, "max_seq_length", 0) or 0)

    def embed(self, texts: list) -> list:
        return [[float(x) for x in v] for v in self.m.encode(texts, normalize_embeddings=True)]

    def token_vectors(self, text: str) -> tuple:
        enc = self.m.tokenizer(text, return_offsets_mapping=True, truncation=True, max_length=self.late_window)
        vecs = self.m.encode(text, output_value="token_embeddings")
        return enc["offset_mapping"], vecs.cpu() if hasattr(vecs, "cpu") else vecs


def embedder(download: bool = True):
    """The best backend available. Only commands that build the index may download: a search must not hang on one."""
    want = os.environ.get("AUTOLEARN_EMBED", "auto").lower()
    model = os.environ.get("AUTOLEARN_EMBED_MODEL", DEFAULT_MODEL)
    for name, cls in (("fastembed", FastEmbedder), ("sentence-transformers", STEmbedder)):
        if want in ("auto", name):
            try:
                return cls(model, download)
            except Exception as e:  # not installed, no network for the first download, bad model
                if want == name:
                    print(f"! embedding backend {name} unavailable ({e}); using hash", file=sys.stderr)
    return HashEmbedder()


def late_enabled(emb) -> bool:
    return os.environ.get("AUTOLEARN_LATE", "auto").lower() != "off" and emb.late_window >= LATE_MIN_WINDOW


def late_pool(emb, text: str, spans: list) -> list:
    """Late chunking: token vectors of the whole document, windows overlapping by a quarter when it
    exceeds the model, mean-pooled over each child's character span. None for a span without tokens."""
    starts = [a for a, _ in spans]
    sums, counts = [None] * len(spans), [0] * len(spans)
    start = 0
    while start < len(text):
        offsets, vectors = emb.token_vectors(text[start:])
        real = [(a, b, v) for (a, b), v in zip(offsets, vectors) if b > a]
        if not real:
            break
        for a, b, vec in real:
            at = bisect.bisect_right(starts, start + (a + b) / 2) - 1
            if at < 0 or start + (a + b) / 2 >= spans[at][1]:
                continue
            row = [float(x) for x in vec]
            sums[at] = row if sums[at] is None else [x + y for x, y in zip(sums[at], row)]
            counts[at] += 1
        end = start + real[-1][1]
        if end >= len(text.rstrip()):
            break
        start += max(real[len(real) * 3 // 4][0], 1)
    return [unit([x / counts[i] for x in sums[i]]) if counts[i] else None for i in range(len(spans))]


def is_english_only(model: str) -> bool:
    return not re.search(r"multilingual|m3|mxbai", model, re.I)


class FastCrossEncoder:
    def __init__(self, model: str, download: bool):
        from fastembed.rerank.cross_encoder import TextCrossEncoder  # optional dependency
        kwargs = {} if download else {"local_files_only": True}  # a search never waits for a download
        self.m = TextCrossEncoder(model_name=model, cache_dir=str(al_home() / "models"), **kwargs)
        self.id, self.english_only = f"fastembed:{model}", is_english_only(model)

    def score(self, query: str, docs: list) -> list:
        return [float(x) for x in self.m.rerank(query, docs)]


class STCrossEncoder:
    def __init__(self, model: str, download: bool):
        from sentence_transformers import CrossEncoder  # optional dependency
        kwargs = {} if download else {"local_files_only": True}
        self.m = CrossEncoder(model, **kwargs)
        self.id, self.english_only = f"st:{model}", is_english_only(model)

    def score(self, query: str, docs: list) -> list:
        return [float(x) for x in self.m.predict([(query, d) for d in docs])]


def cross_encoder(download: bool = False):
    """The neural reranker if one can load, else None and the built-in reranker does the work.
    Only `index` passes download=True, in the background, so that the next search finds it cached."""
    want = os.environ.get("AUTOLEARN_RERANK", "auto").lower()
    model = os.environ.get("AUTOLEARN_RERANK_MODEL")
    for name, cls, default in (("fastembed", FastCrossEncoder, FAST_RERANK),
                               ("sentence-transformers", STCrossEncoder, ST_RERANK)):
        if want in ("auto", name):
            try:
                return cls(model or default, download)
            except Exception as e:  # not installed, or the model is not cached yet
                if want == name:
                    print(f"! reranker {name} unavailable ({e}); using the built-in one", file=sys.stderr)
    return None


def installed(module: str) -> bool:
    return importlib.util.find_spec(module) is not None
