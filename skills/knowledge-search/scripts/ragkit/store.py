"""Vector store with change data capture: `<KB>/.rag/meta.json` (files, sections, chunks, entities) and
`vectors.f32` (one float32 row per chunk). Every known root is scanned for changes on each refresh
and only chunks whose text or context changed are re-embedded; `changes.jsonl` journals every change.
One writer at a time holds a lock; others leave a note for it and readers keep the last snapshot.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import re
import statistics
import sys
import tempfile
import time
import zlib
from array import array

from .chunks import parse_file
from .core import (EMBED_CHARS, MAX_FILE_BYTES, SCHEMA, SKIP_DIRS, STEM, SUFFIXES, digest, is_utf8, read_text, slug,
                   split_lines, tokens)
from .graph import doc_subject, extract, mid_sentence_names
from .lang import PROBES
from .models import late_enabled, late_pool, unit

LOCK_STALE = 600  # the writer refreshes the lock file as it goes, so ten quiet minutes mean it is gone
JOURNAL_MAX = 1_000_000
LLM_MAX_CHUNKS = 60  # per run, so that a background index never turns into a long, costly batch
LLM_MAX_TRIES = 3  # on one version of a file whose answers cannot be parsed
EVENT_MAX_BYTES = 1_000_000


def max_file_bytes() -> int:
    try:
        return int(float(os.environ["RESEARCH_LAB_MAX_FILE_MB"]) * 1_000_000)
    except (KeyError, ValueError):
        return MAX_FILE_BYTES


def files(roots: list) -> list:
    out, seen = [], set()
    for root in roots:
        try:
            found = sorted(root.rglob("*"))
        except OSError as e:  # the folder is being changed under us: the next refresh sees it
            print(f"! cannot scan {root}: {e}", file=sys.stderr)
            continue
        for f in found:
            if f.is_file() and f.suffix in SUFFIXES and not SKIP_DIRS & set(f.relative_to(root).parts) \
                    and not f.name.startswith(".") and f not in seen:
                seen.add(f)
                out.append(f)
    return out


def _empty() -> dict:
    return {"backend": None, "dim": 0, "files": {}, "roots": []}


def _old_shape(meta: dict) -> dict:
    """Stores from before parent-child chunks: readable, one section per chunk, until they are rebuilt."""
    for entry in meta["files"].values():
        if "parents" in entry:
            continue
        entry["parents"] = [{"s": c["s"], "e": c["e"], "head": c.get("head", ""), "htok": c.get("htok", []),
                             "ctok": [], "per": []} for c in entry.get("chunks", [])]
        for n, c in enumerate(entry.get("chunks", [])):
            c["p"] = n
    return meta


def _readable(meta) -> dict:
    """The store's metadata if this code can use it. Anything else is treated as no store, and rebuilt, instead of
    crashing every command until someone deletes the folder by hand."""
    try:
        meta = _old_shape(meta)
        for entry in meta["files"].values():
            for c in entry["chunks"]:
                _ = (c["tok"], c["row"], c["s"], c["e"], entry["parents"][c["p"]]["htok"])
        meta.setdefault("backend", None)
        return meta if isinstance(meta.get("dim"), int) else None
    except (KeyError, TypeError, AttributeError, IndexError):
        return None


def roots_of(known: dict, kb: pathlib.Path) -> list:
    """Roots to keep when an old store without a root list is rebuilt: its KB folders, or the files' folders."""
    kb_abs, out = str(kb.resolve()), set()
    for key in known:
        path = pathlib.Path(key)
        if key.startswith(kb_abs + os.sep) or not path.exists():
            continue
        out.add(str(next((p for p in path.parents if p.name == "KB"), path.parent)))
    return sorted(out)


class Store:
    def __init__(self, kb: pathlib.Path):
        self.kb, self.dir = kb, kb / ".rag"
        self.building = False
        self.meta_path, self.vec_path = self.dir / "meta.json", self.dir / "vectors.f32"
        self.journal, self.lock_path, self.dirty_path = (self.dir / n for n in ("changes.jsonl", "lock", "dirty"))
        self.load()

    def load(self) -> None:
        """Read a consistent snapshot: the checksum catches a reader caught between the two file swaps."""
        for _ in range(10):
            try:
                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                raw = self.vec_path.read_bytes()
            except (OSError, ValueError):
                break
            usable = _readable(meta)
            if usable is None:
                print(f"! {self.meta_path} is damaged; the index will be rebuilt", file=sys.stderr)
                break
            if meta.get("crc") in (None, zlib.crc32(raw)):
                self.vec = array("f")
                self.vec.frombytes(raw[:len(raw) - len(raw) % 4])
                self.meta = usable
                return
            time.sleep(0.05)
        self.meta, self.vec = _empty(), array("f")

    def cosines(self, q: list) -> list:
        d, n = self.meta["dim"], len(self.vec) // max(self.meta["dim"], 1)
        try:
            import numpy as np  # optional speed-up
            return list(np.frombuffer(self.vec, dtype=np.float32).reshape(n, d) @ np.asarray(q, np.float32))
        except ImportError:
            rows = (self.vec[i * d:(i + 1) * d] for i in range(n))
            return [sum(a * b for a, b in zip(row, q)) for row in rows]

    @contextlib.contextmanager
    def lock(self):
        """Yields True for the single writer, False if another process is indexing right now. An unwritable
        folder raises OSError: "somebody else is on it" would be a false comfort."""
        mine = False
        self.dir.mkdir(parents=True, exist_ok=True)
        if self._lock_is_stale():
            with contextlib.suppress(OSError):
                self.lock_path.unlink()
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            mine = True
        except FileExistsError:
            pass
        try:
            yield mine
        finally:
            if mine:
                with contextlib.suppress(OSError):
                    self.lock_path.unlink()

    def _heartbeat(self) -> None:
        with contextlib.suppress(OSError):
            os.utime(self.lock_path)

    def _lock_is_stale(self) -> bool:
        """The lock's owner died (a crash, a reboot), or it has been held for longer than any index takes."""
        try:
            if time.time() - self.lock_path.stat().st_mtime > LOCK_STALE:
                return True
            owner = int(self.lock_path.read_text() or 0)
        except (OSError, ValueError):
            return False
        if not owner or os.name == "nt":  # os.kill(pid, 0) would interrupt the process on Windows
            return False
        try:
            os.kill(owner, 0)
        except ProcessLookupError:
            return True
        except OSError:  # alive, and someone else's
            pass
        return False

    def mark_dirty(self, roots: list) -> None:
        with contextlib.suppress(OSError):
            known = set(self.dirty_path.read_text(encoding="utf-8").splitlines()) if self.dirty_path.exists() else set()
            new = [str(r.resolve()) for r in roots]
            with open(self.dirty_path, "a", encoding="utf-8") as f:
                f.write("".join(f"{r}\n" for r in new if r not in known))

    def take_dirty(self):
        """Roots other processes asked to index meanwhile, or None if nobody asked."""
        try:
            text = self.dirty_path.read_text(encoding="utf-8")
            self.dirty_path.unlink()
        except OSError:
            return None
        return [pathlib.Path(p) for p in text.splitlines() if p.strip()]

    @staticmethod
    def _signature(emb) -> dict:
        # `stem` carries the schema so that an older engine, which compares it, rebuilds this store instead of crashing
        return {"backend": emb.id, "dim": emb.dim, "stem": f"{STEM}/{SCHEMA}", "schema": SCHEMA, "late": late_enabled(emb)}

    def needs_rebuild(self, emb) -> bool:
        return any(self.meta.get(k) != v for k, v in self._signature(emb).items())

    def update(self, roots: list, emb, enrich=None) -> dict:
        """Apply what changed on disk. Returns {changed, added, removed, kept, embedded, enriched, events}."""
        sig = self._signature(emb)
        if self.needs_rebuild(emb):
            kept_roots = self.meta.get("roots") or roots_of(self.meta["files"], self.kb)
            self.meta, self.vec = {**sig, "roots": kept_roots, "files": {}}, array("f")
        names = {str(r.resolve()) for r in roots} | set(self.meta.get("roots", []))
        live_roots = sorted(n for n in names if pathlib.Path(n).exists())
        old_files, old_vec, d = self.meta["files"], self.vec, emb.dim
        live, events, skipped = {}, [], []
        touched = False
        limit = max_file_bytes()
        for f in files([pathlib.Path(r) for r in live_roots]):
            key, prev = str(f.resolve()), old_files.get(str(f.resolve()))
            try:
                st = f.stat()
            except OSError:  # gone since the scan: the next refresh records that
                continue
            if st.st_size > limit:
                skipped.append(key)
                continue
            if prev and prev.get("mtime") == st.st_mtime and prev.get("size") == st.st_size:
                live[key] = prev
                continue
            self._heartbeat()
            try:
                text = read_text(f)
            except OSError as e:  # vanished or unreadable right now: the next refresh sees it
                print(f"! cannot read {f}: {e}", file=sys.stderr)
                if prev:
                    live[key] = prev
                continue
            if prev and prev.get("sha") == digest(text):
                live[key] = {**prev, "mtime": st.st_mtime, "size": st.st_size}  # touched, same content
                touched = True
                continue
            live[key], event = self._refresh(f, text, st, prev, emb)
            events.append(event)
        for key, entry in old_files.items():
            if key not in live:
                events.append({"op": "delete", "path": key, "added": 0, "removed": len(entry["chunks"]), "kept": 0,
                               "embedded": 0})
        for key in sorted(set(skipped) - set(self.meta.get("skipped", []))):
            print(f"! {key} is over {limit // 1_000_000} MB and was not indexed (RESEARCH_LAB_MAX_FILE_MB changes the limit)",
                  file=sys.stderr)
        touched = touched or skipped != self.meta.get("skipped", [])
        self.meta["skipped"] = skipped
        enriched, tried = self._enrich(live, enrich, emb) if enrich else (0, False)
        stats = {k: sum(e[k] for e in events) for k in ("added", "removed", "kept", "embedded")}
        stats.update(changed=len(events), enriched=enriched, events=events)
        if events or tried or touched or set(live_roots) != set(self.meta.get("roots", [])):
            self._save(live, live_roots, old_vec, d, events, emb)
        return stats

    def _refresh(self, path: pathlib.Path, text: str, st, prev: dict, emb) -> tuple:
        """The new store entry of a changed file: unchanged chunks keep their vectors."""
        doc = parse_file(path, text)
        legacy = not is_utf8(path.read_bytes())
        if legacy:
            print(f"! {path} is not valid UTF-8: read in a legacy encoding, some characters may be wrong", file=sys.stderr)
        late = late_enabled(emb)
        subject = doc_subject(doc["title"], path.stem)
        known = mid_sentence_names(text)
        by_vs, by_sha = {}, {}
        for c in (prev or {}).get("chunks", []):
            by_vs.setdefault(c.get("vs"), c)
            by_sha.setdefault(c.get("sha"), c)
        parents, chunks, todo = [], [], []
        for n, parent in enumerate(doc["parents"]):
            parents.append({k: parent[k] for k in ("s", "e", "head", "htok", "sha", "ctok", "per")})
            for kid in parent["kids"]:
                sha = digest(kid["text"])
                old = by_sha.get(sha)
                ctx = old["ctx"] if old and old.get("ctx") else kid["ctx"]
                vs = digest(ctx + "\n" + kid["text"])
                ents, rels = extract(kid["text"], subject, known)
                child = {"s": kid["s"], "e": kid["e"], "p": n, "cs": kid["cs"], "ce": kid["ce"], "sha": sha, "vs": vs,
                         "tok": tokens(kid["text"]), "ents": ents, "rels": rels}
                if old and old.get("ctx"):
                    child.update({k: old[k] for k in ("ctx", "ltok", "llm") if k in old})
                    child["rels"] = rels + [t for t in old.get("rels", []) if t not in rels]
                    child["ents"] = list(dict.fromkeys(ents + old.get("ents", [])))
                if not late and vs in by_vs:
                    child["row"] = by_vs[vs]["row"]
                else:
                    todo.append((child, (ctx + "\n" + kid["text"])[:EMBED_CHARS]))
                chunks.append(child)
        self._embed(todo, doc["text"], emb, late)
        kept = len(chunks) - len(todo)
        entry = {"mtime": st.st_mtime, "size": st.st_size, "sha": digest(text), "title": doc["title"],
                 "subject": subject, "legacy": legacy, "parents": parents, "chunks": chunks}
        event = {"op": "modify" if prev else "add", "path": str(path.resolve()), "added": len(todo),
                 "removed": max(len((prev or {}).get("chunks", [])) - kept, 0), "kept": kept, "embedded": len(todo)}
        return entry, event

    @staticmethod
    def _embed(todo: list, text: str, emb, late: bool) -> None:
        if not todo:
            return
        vectors = emb.embed([t for _, t in todo])
        pooled = late_pool(emb, text, [(c["cs"], c["ce"]) for c, _ in todo]) if late and len(todo) > 1 else None
        for i, (child, _) in enumerate(todo):
            vec = vectors[i]
            if pooled and pooled[i]:  # document-aware vector, kept next to the chunk's own meaning
                vec = unit([a + b for a, b in zip(vec, pooled[i])])
            child["new"] = vec

    def _enrich(self, live: dict, enrich, emb) -> int:
        """Replace heuristic context with the LLM's on up to LLM_MAX_CHUNKS chunks; later runs do the rest.
        Returns (chunks done, whether anything needs saving). Heuristic context stays wherever this fails."""
        done, tried = 0, False
        for key, entry in live.items():
            if done >= LLM_MAX_CHUNKS:
                break
            pending = [c for c in entry["chunks"] if c.get("llm") != enrich.id][:LLM_MAX_CHUNKS - done]
            if not pending or entry.get("llm_tries", 0) >= LLM_MAX_TRIES:
                continue
            self._heartbeat()
            try:
                text = "\n".join(split_lines(read_text(key)))
                answers = enrich.enrich(entry.get("title", ""), text, [text[c["cs"]:c["ce"]] for c in pending])
            except ValueError as e:  # this document's answer was unusable: the others may be fine
                entry["llm_tries"], tried = entry.get("llm_tries", 0) + 1, True
                print(f"! LLM enrichment skipped {key}: {e}", file=sys.stderr)
                continue
            except Exception as e:  # server gone: no point in trying the next file now
                print(f"! LLM enrichment stopped at {key}: {e}", file=sys.stderr)
                return done, tried or done > 0
            todo = []
            for c, answer in zip(pending, answers):
                c.update(ctx=answer["context"], ltok=tokens(answer["context"]), llm=enrich.id)
                c["rels"] = c.get("rels", []) + [t for t in answer["triples"] if t not in c.get("rels", [])]
                c["ents"] = list(dict.fromkeys(c.get("ents", []) + [x for t in answer["triples"] for x in (t[0], t[2])]))
                c["vs"] = digest(answer["context"] + "\n" + text[c["cs"]:c["ce"]])
                todo.append((c, (answer["context"] + "\n" + text[c["cs"]:c["ce"]])[:EMBED_CHARS]))
            self._embed(todo, text, emb, False)
            done += len(pending)
        return done, tried or done > 0

    def _noise_floor(self, emb) -> float:
        """How close everyday questions get to this base under this model: what "close" means here."""
        if emb.id.startswith("hash") or not len(self.vec):
            return 0.0
        return round(statistics.median(max(float(x) for x in self.cosines(v)) for v in emb.embed(PROBES)), 3)

    def _save(self, live: dict, live_roots: list, old_vec: array, d: int, events: list, emb) -> None:
        vec, n = array("f"), 0
        for entry in live.values():
            for c in entry["chunks"]:
                vec.extend(c.pop("new") if "new" in c else old_vec[c["row"] * d:(c["row"] + 1) * d])
                c["row"] = n
                n += 1
        raw = vec.tobytes()
        self.meta.update(files=live, roots=live_roots, crc=zlib.crc32(raw), python=sys.executable)
        self.vec = vec
        self.meta["noise"] = self._noise_floor(emb)
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp_m, tmp_v = (p.with_suffix(f".{os.getpid()}.tmp") for p in (self.meta_path, self.vec_path))
        tmp_m.write_text(json.dumps(self.meta, ensure_ascii=False), encoding="utf-8")
        tmp_v.write_bytes(raw)
        os.replace(tmp_v, self.vec_path)
        os.replace(tmp_m, self.meta_path)
        self._journal(events)

    def _journal(self, events: list) -> None:
        if not events:
            return
        now = datetime.datetime.now().isoformat(timespec="seconds")
        with contextlib.suppress(OSError):
            if self.journal.exists() and self.journal.stat().st_size > JOURNAL_MAX:
                lines = self.journal.read_text(encoding="utf-8").splitlines()
                self.journal.write_text("\n".join(lines[len(lines) // 2:]) + "\n", encoding="utf-8")
            with open(self.journal, "a", encoding="utf-8") as f:
                f.write("".join(json.dumps({"ts": now, **e}, ensure_ascii=False) + "\n" for e in events))

    def forget(self, root: pathlib.Path, emb) -> int:
        """Stop searching a folder: an indexed root otherwise stays in every search for good. Files are left alone."""
        target = str(root.resolve())
        live = {k: v for k, v in self.meta["files"].items() if not k.startswith(target + os.sep)}
        gone = len(self.meta["files"]) - len(live)
        roots = [r for r in self.meta.get("roots", []) if r != target]
        if gone or len(roots) != len(self.meta.get("roots", [])):
            events = [{"op": "delete", "path": k, "added": 0, "removed": len(v["chunks"]), "kept": 0, "embedded": 0}
                      for k, v in self.meta["files"].items() if k not in live]
            self._save(live, roots, self.vec, self.meta["dim"], events, emb)
        return gone

    def last_change(self) -> dict:
        with contextlib.suppress(OSError, ValueError, IndexError):
            return json.loads(self.journal.read_text(encoding="utf-8").splitlines()[-1])
        return {}


def refresh(store: Store, roots: list, emb, enrich=None):
    """Bring the store up to date, or leave a note for the indexer already running (returns None then)."""
    with store.lock() as mine:
        if not mine:
            store.mark_dirty(roots)
            return None
        store.load()
        stats = store.update(roots, emb, enrich)
        for _ in range(5):
            extra = store.take_dirty()
            if extra is None:
                break
            more = store.update(extra, emb, enrich)
            stats = {**{k: stats[k] + more[k] for k in ("changed", "added", "removed", "kept", "embedded", "enriched")},
                     "events": stats["events"] + more["events"]}
    return stats


def _write_atomic(path: pathlib.Path, data: bytes) -> None:
    """A failed or rejected write leaves the previous version whole, and no reader sees half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _one_line(value) -> str:
    return re.sub(r"\s+", " ", str(value)).strip()


def event_path(kb: pathlib.Path, ident: str) -> pathlib.Path:
    """A safe file for an external document id: slugged and hashed, it can never leave `.stream/`."""
    return kb / ".stream" / f"{slug(ident)[:60]}-{digest(ident)}.md"


def apply_events(kb: pathlib.Path, lines: list) -> tuple:
    """Streaming input for connectors: JSON lines {op: upsert, id, title?, url?, updated?, text} or
    {op: delete, id} become files under `<KB>/.stream/`. Returns (applied, [errors])."""
    applied, errors = 0, []
    for n, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
            op, ident = ev["op"], ev["id"]
            if not isinstance(ident, str) or not ident.strip():
                raise ValueError("id must be a non-empty string")
            path = event_path(kb, ident)
            if op == "delete":
                with contextlib.suppress(OSError):
                    path.unlink()
            elif op == "upsert":
                if not isinstance(ev.get("text"), str):
                    raise ValueError("upsert needs text")
                title = _one_line(ev.get("title") or ident)
                meta = [f"{k}: {_one_line(ev[k])}" for k in ("url", "updated") if ev.get(k)]
                body = "\n".join([f"# {title}", "", *meta, "", ev["text"], ""]).encode("utf-8")  # broken text raises here
                if len(body) > EVENT_MAX_BYTES:
                    raise ValueError(f"the document is over {EVENT_MAX_BYTES} bytes")
                _write_atomic(path, body)
            else:
                raise ValueError(f"unknown op {op!r}")
            applied += 1
        except (ValueError, KeyError, TypeError) as e:
            errors.append(f"line {n}: {e}")
        except RecursionError:
            errors.append(f"line {n}: nested too deeply")
    return applied, errors
