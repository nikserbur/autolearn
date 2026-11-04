"""Shared set-up for the RAG tests: a private KB, the engine as a subprocess, and stand-ins for the
optional dependencies that follow their real APIs, so the optional paths run without any download."""
import http.server
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / "skills" / "research" / "scripts" / "kb_rag.py"
CORPUS = ROOT / "tests" / "fixtures" / "rag" / "kb"
sys.path.insert(0, str(ENGINE.parent))

FAKE_FASTEMBED = '''
import os, pathlib, zlib

def _vec(text, dim=8):
    v = [0.0] * dim
    for w in text.lower().split():
        v[zlib.crc32(w.encode()) % dim] += 1.0
    return v

class _Encoding:
    def __init__(self, offsets):
        self.offsets = offsets

class _Tokenizer:
    def __init__(self, window):
        self.truncation = {"max_length": window}

    def encode(self, text):
        offsets, pos = [(0, 0)], 0
        for word in text.split():
            at = text.find(word, pos)
            pos = at + len(word)
            offsets.append((at, pos))
        return _Encoding(offsets[:self.truncation["max_length"] - 1] + [(0, 0)])

class _Output:
    def __init__(self, rows):
        self.model_output = [rows]

class _Onnx:
    def __init__(self, window):
        self.tokenizer = _Tokenizer(window)

    def onnx_embed(self, texts):
        offsets = self.tokenizer.encode(texts[0]).offsets
        whole = _vec(texts[0])  # every token vector carries its whole window: the point of late chunking
        return _Output([[a + 0.1 * b for a, b in zip(_vec(texts[0][s:e]), whole)] for s, e in offsets])

class TextEmbedding:
    def __init__(self, model_name, cache_dir=None):
        self.model = _Onnx(int(os.environ.get("FAKE_WINDOW", "128")))
        self.name = model_name

    def embed(self, texts):
        if os.environ.get("FAKE_EMBED_FAIL") and not texts[0] == "probe":
            raise RuntimeError("the model broke")
        for text in texts:
            yield _vec(text)
'''

FAKE_CROSS_ENCODER = '''
import os, pathlib

class TextCrossEncoder:
    def __init__(self, model_name, cache_dir=None, local_files_only=False):
        marker = pathlib.Path(os.environ["FAKE_CE_CACHE"])
        with open(os.environ["FAKE_CE_LOG"], "a") as f:
            f.write(f"load local_files_only={local_files_only}\\n")
        if local_files_only and not marker.exists():
            raise ValueError("model is not cached")
        marker.write_text("cached")

    def rerank(self, query, documents):
        if os.environ.get("FAKE_CE_FAIL"):
            raise RuntimeError("inference failed")
        prefer = os.environ.get("FAKE_CE_PREFER", "")
        return [10.0 if prefer and prefer in d else float(len(set(query.split()) & set(d.split()))) for d in documents]
'''


FAKE_SENTENCE_TRANSFORMERS = '''
import os

class _Tensor(list):
    def cpu(self):
        return self

class SentenceTransformer:
    def __init__(self, model):
        self.max_seq_length = int(os.environ.get("FAKE_WINDOW", "128"))

    def tokenizer(self, text, return_offsets_mapping, truncation, max_length):
        offsets, pos = [(0, 0)], 0
        for word in text.split():
            at = text.find(word, pos)
            pos = at + len(word)
            offsets.append((at, pos))
        return {"offset_mapping": offsets[:max_length - 1] + [(0, 0)]}

    def encode(self, texts, normalize_embeddings=False, output_value=None):
        if output_value == "token_embeddings":
            return _Tensor([[1.0, float(len(texts) % 7), 2.0] for _ in self.tokenizer(texts, True, True, self.max_seq_length)["offset_mapping"]])
        return [[1.0, float(len(t) % 5), 0.5] for t in texts]

class CrossEncoder:
    def __init__(self, model, local_files_only=False):
        marker = os.environ["FAKE_CE_CACHE"]
        if local_files_only and not os.path.exists(marker):
            raise ValueError("model is not cached")
        open(marker, "w").close()

    def predict(self, pairs):
        return [float(len(set(q.split()) & set(d.split()))) for q, d in pairs]
'''


class RagCase(unittest.TestCase):
    copy_corpus = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)
        self.home = self.dir / "autolearn"
        self.kb = self.home / "knowledge"
        if self.copy_corpus:
            shutil.copytree(CORPUS, self.kb)
        else:
            self.kb.mkdir(parents=True)
        self.env_extra = {}

    def tearDown(self):
        self.tmp.cleanup()

    def env(self, **extra):
        keep = {k: v for k, v in os.environ.items() if not k.startswith(("AUTOLEARN_", "OLLAMA_", "FAKE_"))}
        return {**keep, "AUTOLEARN_HOME": str(self.home), "AUTOLEARN_EMBED": "hash", "AUTOLEARN_LLM": "off",
                **self.env_extra, **extra}

    def run_engine(self, *args, expect=0, stdin=None, **env):
        proc = subprocess.run([sys.executable, str(ENGINE), *args], capture_output=True, text=True, input=stdin,
                              env=self.env(**env), timeout=120, cwd=self.tmp.name)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        self.stderr = proc.stderr
        return proc.stdout

    def search(self, *query, **env):
        return json.loads(self.run_engine("search", "--no-log", "--json", *query, **env))

    def write(self, rel, text):
        path = self.kb / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        return path

    def journal(self):
        path = self.kb / ".rag" / "changes.jsonl"
        return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []

    def meta(self):
        return json.loads((self.kb / ".rag" / "meta.json").read_text(encoding="utf-8"))

    def paths(self, result):
        return [r["path"] for r in result["results"]]

    def fake_models(self):
        """Stand-ins for fastembed importable by the engine subprocess."""
        site = self.dir / "site"
        (site / "fastembed" / "rerank").mkdir(parents=True)
        (site / "fastembed" / "__init__.py").write_text(FAKE_FASTEMBED)
        (site / "fastembed" / "rerank" / "__init__.py").write_text("")
        (site / "fastembed" / "rerank" / "cross_encoder.py").write_text(FAKE_CROSS_ENCODER)
        self.env_extra.update(PYTHONPATH=str(site), FAKE_CE_CACHE=str(self.dir / "ce-cached"),
                              FAKE_CE_LOG=str(self.dir / "ce.log"))


    def fake_sentence_transformers(self):
        site = self.dir / "site-st"
        site.mkdir()
        (site / "sentence_transformers.py").write_text(FAKE_SENTENCE_TRANSFORMERS)
        self.env_extra.update(PYTHONPATH=str(site), FAKE_CE_CACHE=str(self.dir / "st-cached"))


class StubOllama:
    """An Ollama server on a real local socket: /api/tags and /api/generate, answers set by the test."""

    def __init__(self, answer):
        outer = self
        self.prompts, self.answer, self.broken = [], answer, False

        class Handler(http.server.BaseHTTPRequestHandler):
            def _send(self, payload):
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                self._send({"models": [{"name": "nomic-embed-text", "size": 1}, {"name": "big:70b", "size": 900},
                                       {"name": "tiny:1b", "size": 100}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.prompts.append(body)
                self._send({} if outer.broken else {"response": outer.answer(body["prompt"])})

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.host = f"127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
