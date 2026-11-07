#!/usr/bin/env python3
"""Measure retrieval on a golden question set. Standard library only.

    rag_eval.py --corpus tests/fixtures/rag/kb --golden tests/fixtures/rag/golden_fixture.tsv
    rag_eval.py --corpus DIR --golden FILE --engine path/to/other/kb_rag.py --json out.json

Golden file (TSV with a header): kind, question, expected.
expected: `a.md|b.md` any of them must appear; `a.md&b.md` all of them must appear (share found is
scored); `-` nothing in the corpus answers it, so the engine must say "No chunk matches".
The engine runs as a subprocess on a private copy of the corpus, so any version can be compared.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
ENGINE = ROOT / "skills" / "knowledge-search" / "scripts" / "kb_rag.py"
HIT = re.compile(r"^=== (\S+?):\d+-\d+", re.M)


def run(engine: pathlib.Path, env: dict, *args: str) -> str:
    proc = subprocess.run([sys.executable, str(engine), *args], capture_output=True, text=True,
                          env=env, timeout=600)
    if proc.returncode:
        raise RuntimeError(f"{engine.name} {' '.join(args)} failed: {proc.stderr[-500:]}")
    return proc.stdout


def score(out: str, expected: str) -> dict:
    """{found, rank, recall} of one answer against its expectation."""
    hits = HIT.findall(out)
    if expected == "-":
        return {"found": "No chunk matches" in out, "rank": 0, "recall": 0.0}
    if "&" in expected:
        want = expected.split("&")
        got = [w for w in want if w in hits]
        return {"found": len(got) == len(want), "rank": 0, "recall": len(got) / len(want)}
    want = set(expected.split("|"))
    rank = next((i for i, h in enumerate(hits, 1) if h in want), 0)
    return {"found": bool(rank), "rank": rank, "recall": float(bool(rank))}


def evaluate(engine: pathlib.Path, corpus: pathlib.Path, golden: pathlib.Path, backend: str) -> list:
    rows = [line.split("\t") for line in golden.read_text(encoding="utf-8").splitlines()[1:] if line.strip()]
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        home = pathlib.Path(tmp) / "research-lab"
        shutil.copytree(corpus, home / "knowledge")
        models = pathlib.Path(os.environ.get("RESEARCH_LAB_HOME", pathlib.Path.home() / ".claude" / "research-lab")) / "models"
        if models.exists():  # a downloaded model is reused, not fetched again for every private copy
            (home / "models").symlink_to(models.resolve())
        env = dict(os.environ, RESEARCH_LAB_HOME=str(home), RESEARCH_LAB_EMBED=backend, RESEARCH_LAB_LLM="off")
        run(engine, env, "index")
        for kind, question, expected in rows:
            text = run(engine, env, "search", "--no-log", question)
            out.append({"kind": kind, "question": question, "expected": expected, **score(text, expected)})
    return out


def summary(results: list) -> dict:
    """Metrics per kind: top-1, top-3 and MRR for single answers, recall for all-of, accuracy for ood."""
    kinds = {}
    for r in results:
        kinds.setdefault(r["kind"], []).append(r)
    table = {}
    for kind, rows in kinds.items():
        n = len(rows)
        entry = {"n": n, "found": sum(r["found"] for r in rows) / n}
        if rows[0]["expected"] == "-":
            entry["correct_refusal"] = entry.pop("found")
        elif "&" in rows[0]["expected"]:
            entry["recall"] = sum(r["recall"] for r in rows) / n
        else:
            entry["top1"] = sum(r["rank"] == 1 for r in rows) / n
            entry["top3"] = sum(0 < r["rank"] <= 3 for r in rows) / n
            entry["mrr"] = sum(1 / r["rank"] for r in rows if r["rank"]) / n
        table[kind] = {k: round(v, 3) if isinstance(v, float) else v for k, v in entry.items()}
    return table


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--corpus", type=pathlib.Path, required=True)
    ap.add_argument("--golden", type=pathlib.Path, required=True)
    ap.add_argument("--engine", type=pathlib.Path, default=ENGINE)
    ap.add_argument("--backend", default="hash", help="RESEARCH_LAB_EMBED value for the run")
    ap.add_argument("--json", type=pathlib.Path, help="write per-question results here")
    ap.add_argument("--verbose", action="store_true", help="print every question that was not answered")
    a = ap.parse_args()
    results = evaluate(a.engine, a.corpus, a.golden, a.backend)
    for kind, entry in summary(results).items():
        print(kind.ljust(12), "  ".join(f"{k} {v}" for k, v in entry.items()))
    if a.verbose:
        for r in results:
            if not r["found"] or r["rank"] > 1:
                print(f"  [{r['kind']}] rank {r['rank']} recall {r['recall']:.2f}: {r['question']}")
    if a.json:
        a.json.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
