#!/usr/bin/env python3
"""Self-routing retrieval (RAG) over the research-lab knowledge base and any research KB. Runs on the
standard library; a real embedding model, cross-encoder or small LLM is used when one is around.

    kb_rag.py search refund webhook stripe            # the pipeline picks the route; best sections with path:line
    kb_rag.py search --root ./research/x/KB "queue position markout"
    kb_rag.py verify < draft.md                       # is this answer grounded in the base? exit 1 if not
    kb_rag.py graph redis                             # what the entity graph knows about an entity
    kb_rag.py index [--root DIR ...]                  # (re)index explicitly; the hook does it on writes
    kb_rag.py events < changes.jsonl                  # apply upsert / delete events from a connector
    kb_rag.py watch [--interval 5]                    # follow changes made outside Claude Code
    kb_rag.py forget --root DIR                       # stop searching a folder indexed earlier
    kb_rag.py setup                                   # install an embedding model for all later runs (downloads)
    kb_rag.py status                                  # backend, techniques in use, store, last change

Nothing needs to be chosen by hand. Indexing cuts documents into sections (parents) and small chunks
(children), adds to each child what the cut took away (Contextual Retrieval), embeds it (late chunking
when the model allows), extracts entities and relations (GraphRAG) and re-embeds only what changed
(CDC). Search routes each query, fuses vector, BM25 and graph rankings (RRF), lifts children to
parents, reranks, and grades the result (CRAG): `CORRECT`, `AMBIGUOUS` or `INCORRECT`, with one
corrective pass and the next action spelled out. See docs/knowledge-router.md for when each one fires.

Each search appends `date, query, top chunk, hit|partial|miss` to `<KB>/.router-log.tsv`.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from ragkit.core import BUDGET, kb_dir, read_text, split_lines  # noqa: E402
from ragkit.enrich import enricher  # noqa: E402
from ragkit.graph import key_of  # noqa: E402
from ragkit.models import LATE_MIN_WINDOW, cross_encoder, embedder, installed  # noqa: E402
from ragkit.model_setup import continue_in_model_python, remembered_python, setup  # noqa: E402
from ragkit.report import to_json, to_text, verify_text  # noqa: E402
from ragkit.search import LOG_OUTCOME, Index, answer, rel_path  # noqa: E402
from ragkit.store import Store, apply_events, refresh  # noqa: E402
from ragkit.verify import verify  # noqa: E402


BUSY_WAIT = 3.0


def roots_of_args(a: argparse.Namespace) -> list:
    return [r for r in [a.kb, *a.root] if r.exists()]


def is_degraded(store: Store, emb) -> bool:
    """Never downgrade a model-built store because this interpreter lacks the model (e.g. the hook runs the
    system python while fastembed lives in a venv): skip updates, rank by words only."""
    built = store.meta["backend"]
    if os.environ.get("RESEARCH_LAB_EMBED", "auto").lower() != "auto" or not built:
        return False
    return not built.startswith("hash") and built != emb.id


def warn_degraded(store: Store) -> None:
    who = store.meta.get("python") or "the python that has the model"
    print(f"! store was built with {store.meta['backend']}, which this python cannot load: ranking by words only, and "
          f"new notes are not searchable until {who} runs. Set RESEARCH_LAB_PYTHON to it, or RESEARCH_LAB_EMBED=hash to rebuild "
          "without a model.", file=sys.stderr)


def cmd_status(store: Store) -> int:
    meta = store.meta
    parents = sum(len(v["parents"]) for v in meta["files"].values())
    chunks = sum(len(v["chunks"]) for v in meta["files"].values())
    print(f"backend {meta['backend']}, dim {meta['dim']}, {len(meta['files'])} files, {parents} sections, "
          f"{chunks} chunks -> {store.dir}")
    if meta.get("late"):
        print("late chunking: on (the model exposes token vectors)")
    else:
        print(f"late chunking: off (needs a model with token vectors and a {LATE_MIN_WINDOW}+ token window)")
    model = next((m for m in ("fastembed", "sentence_transformers") if installed(m)), None)
    print(f"reranker: {'cross-encoder via ' + model + ' once cached, else built-in' if model else 'built-in'}")
    want = os.environ.get("RESEARCH_LAB_LLM", "auto")
    print(f"llm context: RESEARCH_LAB_LLM={want} (ollama on this machine when running; otherwise heuristic context)")
    stats = Index(store, store.kb, has_vectors=False).graph.stats() if meta["files"] else {}
    if stats:
        print(f"graph: {stats['entities']} entities, {stats['relations']} typed relations, {stats['clusters']} clusters")
    last = store.last_change()
    if last:
        print(f"last change: {last['ts']} {last['op']} {pathlib.Path(last['path']).name} "
              f"(+{last['added']} -{last['removed']} ={last['kept']})")
    print(f"python: {sys.executable}" + (" (kb_rag.py setup)" if remembered_python() == sys.executable else ""))
    roots = [r for r in meta.get("roots", []) if r != str(store.kb.resolve())]
    if roots:
        print("other folders in every search (kb_rag.py forget --root DIR to drop one):")
        print("".join(f"  {r}\n" for r in roots), end="")
    for label, listed in (("not indexed, over the size limit", meta.get("skipped") or []),
                          ("not valid UTF-8, read in a legacy encoding", [k for k, v in meta["files"].items() if v.get("legacy")])):
        if listed:
            print(f"{label}:")
            print("".join(f"  {k}\n" for k in listed), end="")
    return 0


def reranker_ready(store: Store) -> bool:
    try:
        return (store.dir / "reranker-ready").read_text(encoding="utf-8") == os.environ.get("RESEARCH_LAB_RERANK_MODEL", "default")
    except OSError:
        return False


def prefetch_reranker(store: Store) -> None:
    """Fetch the cross-encoder here, in the background hook, so that a search never waits for a download."""
    if not reranker_ready(store) and cross_encoder(download=True):
        store.dir.mkdir(parents=True, exist_ok=True)
        (store.dir / "reranker-ready").write_text(os.environ.get("RESEARCH_LAB_RERANK_MODEL", "default"), encoding="utf-8")


def cmd_index(store: Store, a: argparse.Namespace, emb, degraded: bool) -> int:
    if degraded:  # saying "indexed" here would be a lie: new notes stay out until the right python runs
        warn_degraded(store)
        return 1
    prefetch_reranker(store)
    try:
        stats = refresh(store, roots_of_args(a), emb, enricher())
    except OSError as e:
        print(f"! could not update the index in {store.dir}: {e}", file=sys.stderr)
        return 1
    n = sum(len(v["chunks"]) for v in store.meta["files"].values())
    if stats is None:
        print("Another index is running; it will pick this change up.")
        return 0
    changed, embedded = stats["changed"], stats["embedded"]
    print(f"Indexed {len(store.meta['files'])} files, {n} chunks ({changed} files updated, {embedded} chunks embedded), "
          f"backend {store.meta['backend']} -> {store.dir}")
    return 0


def log_search(a: argparse.Namespace, query: str, top: str, outcome: str) -> None:
    if a.no_log:
        return
    try:
        with open(a.kb / ".router-log.tsv", "a", encoding="utf-8") as f:
            f.write(f"{datetime.date.today()}\t{query}\t{top}\t{outcome}\n")
    except OSError as e:
        print(f"! could not log the search: {e}", file=sys.stderr)


def refresh_waiting(store: Store, roots: list, emb) -> None:
    """Refresh, giving a running indexer (the hook starts one after each write) a moment to finish."""
    deadline = time.time() + BUSY_WAIT
    while refresh(store, roots, emb) is None:
        if time.time() >= deadline:
            store.load()
            print("! another index is still running; searching the last snapshot", file=sys.stderr)
            return
        time.sleep(0.1)


def build_in_background(a: argparse.Namespace) -> None:
    """A full build with a model takes minutes, far too long to hold a search. The next one finds it done."""
    cmd = [sys.executable, str(pathlib.Path(__file__).resolve()), "index"]
    for root in a.root:
        cmd += ["--root", str(root)]
    flags = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" else {"start_new_session": True}
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                     **flags)


def fresh_index(store: Store, a: argparse.Namespace, emb, degraded: bool) -> Index:
    """The store as of now: searches refresh it first, unless another process is on it for too long."""
    if degraded:
        warn_degraded(store)
    elif not emb.id.startswith("hash") and store.needs_rebuild(emb):
        build_in_background(a)
        store.building = True
        print(f"! building the index with {emb.id} in the background; this search uses the last snapshot, by words",
              file=sys.stderr)
    else:
        try:
            refresh_waiting(store, roots_of_args(a), emb)
        except Exception as e:  # a failing model must not take the last good snapshot down with it
            store.load()
            print(f"! could not refresh the store ({e}); searching the last snapshot", file=sys.stderr)
    scope = None if a.everywhere else [a.kb, *a.root]
    return Index(store, a.kb, has_vectors=not degraded and store.meta.get("backend") == emb.id, scope=scope,
                 retired=a.with_retired)


def cmd_search(store: Store, a: argparse.Namespace, emb, degraded: bool) -> int:
    index = fresh_index(store, a, emb, degraded)
    for path in (store.meta.get("skipped") or [])[:3]:
        print(f"! {path} is over the size limit and not searchable (RESEARCH_LAB_MAX_FILE_MB changes it)", file=sys.stderr)
    query = " ".join(" ".join(a.query).split())  # a line break must not forge a line of the log or the report
    if store.building and not index.n:
        print("The first index is being built in the background. Search again in a minute or two; until then use grep.")
        return 0
    ce = cross_encoder() if not degraded and reranker_ready(store) else None
    result = answer(index, query, emb, ce, a.mode, a.k)
    backend = "words only" if degraded else emb.id
    if a.json:
        print(json.dumps(to_json(index, result, a.budget, backend), ensure_ascii=False, indent=1))
    else:
        print(to_text(index, result, a.budget, backend))
    top = "-"
    if result.passages and result.verdict != "INCORRECT":
        top = f"{rel_path(index, result.passages[0].file)}:{result.passages[0].parent['s']}"
    if index.hidden and not a.json:
        print(f"({index.hidden} chunks marked invalid or superseded were left out; --with-retired lets them answer)")
    log_search(a, query, top, LOG_OUTCOME[result.verdict])
    return 0


def cmd_verify(store: Store, a: argparse.Namespace, emb, degraded: bool) -> int:
    text = a.answer if a.answer is not None else (read_text(a.file) if a.file else sys.stdin.read())
    index = fresh_index(store, a, emb, degraded)
    report = verify(index, text, None if degraded else emb)
    print(json.dumps(report, ensure_ascii=False, indent=1) if a.json else verify_text(report))
    return 0 if report["passed"] else 1


def cmd_graph(store: Store, a: argparse.Namespace, emb, degraded: bool) -> int:
    index = fresh_index(store, a, emb, degraded)
    graph, name = index.graph, " ".join(a.query)
    if not name:
        stats = graph.stats()
        print(f"{stats['entities']} entities, {stats['relations']} typed relations, {stats['clusters']} clusters")
        for c in graph.communities()[:10]:
            print(f"- {', '.join(graph.name(k) for k in c['keys'][:8])}  ({len(c['chunks'])} chunks)")
        return 0
    key = key_of(name)
    if key not in graph.names:
        print(f"No entity '{name}' in the graph.")
        return 0
    def where(i: int) -> str:
        return f"{rel_path(index, index.items[i][0])}:{index.items[i][1]['s']}"
    print(f"{graph.name(key)}: {len(graph.chunks[key])} chunks")
    for rel, other, i in graph.out[key]:
        print(f"  --{rel}--> {graph.name(other)}  ({where(i)})")
    for rel, other, i in graph.inc[key]:
        print(f"  <--{rel}-- {graph.name(other)}  ({where(i)})")
    related = ", ".join(graph.name(k) for k, _ in graph.co[key].most_common(8))
    if related:
        print(f"  appears next to: {related}")
    return 0


def cmd_events(store: Store, a: argparse.Namespace, emb, degraded: bool) -> int:
    applied, errors = apply_events(a.kb, split_lines(sys.stdin.read()))
    for e in errors:
        print(f"! {e}", file=sys.stderr)
    if degraded:
        warn_degraded(store)
    stats = None if degraded else refresh(store, roots_of_args(a), emb, enricher())
    embedded = stats["embedded"] if stats else 0
    print(f"Applied {applied} events, {len(errors)} rejected, {embedded} chunks embedded.")
    return 1 if errors else 0


def cmd_forget(store: Store, a: argparse.Namespace, emb, degraded: bool) -> int:
    if not a.root:
        print("! name the folder: kb_rag.py forget --root DIR", file=sys.stderr)
        return 1
    with store.lock() as mine:
        if not mine:
            print("! another index is running; try again in a moment", file=sys.stderr)
            return 1
        store.load()
        gone = sum(store.forget(root, emb) for root in a.root)
    print(f"Forgot {len(a.root)} folder(s), {gone} files. The files themselves are untouched.")
    return 0


def cmd_watch(store: Store, a: argparse.Namespace, emb, degraded: bool) -> int:
    if degraded:
        warn_degraded(store)
        return 1
    try:
        while True:
            stats = refresh(store, roots_of_args(a), emb, enricher())
            for ev in (stats or {}).get("events", []):
                print(f"{ev['op']} {ev['path']} (+{ev['added']} -{ev['removed']} ={ev['kept']})", flush=True)
            if a.once:
                return 0
            time.sleep(a.interval)
    except KeyboardInterrupt:
        return 0


def main() -> int:
    continue_in_model_python()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["search", "index", "status", "verify", "graph", "events", "watch", "forget", "setup"])
    ap.add_argument("query", nargs="*")
    ap.add_argument("--kb", type=pathlib.Path, default=kb_dir())
    ap.add_argument("--root", type=pathlib.Path, action="append", default=[],
                    help="extra folder to index and search, e.g. a research KB (repeatable)")
    ap.add_argument("--everywhere", action="store_true",
                    help="search every indexed folder, every research KB included (default: the knowledge base and --root)")
    ap.add_argument("--with-retired", action="store_true",
                    help="let sections marked invalid or superseded answer too")
    ap.add_argument("--k", type=int, default=0, help="sections to return (default: chosen by the route)")
    ap.add_argument("--budget", type=int, default=BUDGET)
    ap.add_argument("--mode", choices=["auto", "exact", "local", "graph", "global"], default="auto",
                    help="override the route; auto is almost always right")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--no-log", action="store_true")
    ap.add_argument("--answer", help="verify: the draft answer (default: stdin)")
    ap.add_argument("--file", type=pathlib.Path, help="verify: read the draft answer from a file")
    ap.add_argument("--interval", type=float, default=5.0, help="watch: seconds between scans")
    ap.add_argument("--once", action="store_true", help="watch: scan once and exit")
    ap.add_argument("--python", default="", help="setup: the interpreter to build the model's virtualenv with")
    a = ap.parse_intermixed_args()  # options may follow the query words (Python < 3.12 needs this)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # consoles without UTF-8 still get the text
        sys.stdin.reconfigure(errors="replace")

    if a.cmd == "setup":
        return setup(str(pathlib.Path(__file__).resolve()), a.python)
    a.kb.mkdir(parents=True, exist_ok=True)
    store = Store(a.kb)
    if a.cmd == "status":
        return cmd_status(store)
    if a.cmd == "index" and store.busy():  # leave a note before loading a model that would only wait
        store.mark_dirty(roots_of_args(a))
        print("Another index is running; it will pick this change up.")
        return 0
    emb = embedder(download=a.cmd not in ("search", "verify", "graph"))
    degraded = is_degraded(store, emb)
    handlers = {"index": cmd_index, "search": cmd_search, "verify": cmd_verify, "graph": cmd_graph,
                "events": cmd_events, "watch": cmd_watch, "forget": cmd_forget}
    return handlers[a.cmd](store, a, emb, degraded)


if __name__ == "__main__":
    sys.exit(main())
