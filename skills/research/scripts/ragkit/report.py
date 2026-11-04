"""What `kb_rag.py` prints: passages with path:line within the budget, the decisions behind them and
the next action for the reader, as text or JSON."""
from __future__ import annotations

from .core import split_lines
from .search import AMBIGUOUS, CORRECT, Index, Result, matched_text, read_section, rel_path

PER_PASSAGE_MIN = 500
FOREIGN_HINT = "The notes are in another language: ask again in theirs before concluding anything."


def passage_text(path: str, passage, room: int) -> str:
    """The whole section, or just the matched child with its heading when the section does not fit."""
    text = read_section(path, passage.parent)
    if len(text.encode()) <= room or path.endswith(".tsv"):
        return text
    lines = split_lines(text)
    head = [lines[0]] if lines and lines[0].startswith("#") and passage.child["s"] > passage.parent["s"] else []
    return "\n".join(head + [matched_text(path, passage.parent, passage.child)])


def _tag(passage) -> str:
    return f"[{passage.parent['head'] or '(top)'}]  score {passage.score:.2f} " \
           f"(cos {passage.cos:.2f}, {passage.matched}/{passage.total} terms)"


def _matched(passage) -> str:
    c, p = passage.child, passage.parent
    return f"  matched {c['s']}-{c['e']}" if (c["s"], c["e"]) != (p["s"], p["e"]) else ""


def to_text(index: Index, result: Result, budget: int, backend: str) -> str:
    out = [f"[route {result.route}: {result.why} · rerank {result.reranker} · "
           f"verdict {result.verdict} {result.confidence:.2f}"
           + (f" · {result.corrected}" if result.corrected else "") + "]"]
    if result.paths:
        out += ["Links in the graph:"] + [f"  {line}" for line in result.paths] + [""]
    if result.themes:
        docs = result.themes[0]["of"]
        out.append(f"Recurring across {docs} documents:")
        out += [f"  {t['term']}: {t['docs']}/{t['of']} ({', '.join(t['evidence'])})" for t in result.themes]
        out.append("")
    if result.clusters:
        out.append("Entity clusters: " + "; ".join(f"{', '.join(c['entities'])} ({c['docs']} docs)" for c in result.clusters))
        out.append("")
    if result.verdict not in (CORRECT, AMBIGUOUS):
        if result.foreign:
            out.append(FOREIGN_HINT)
        out.append(f"No chunk matches '{result.query}'. Fall back to repo docs, doc tools, then the web, "
                   "and write what you find into the KB.")
        out += [f"--- {rel_path(index, p.file)}:{p.parent['s']}-{p.parent['e']}  {_tag(p)}  weak, unlikely to help"
                for p in result.passages[:3]]
        return "\n".join(out)
    used = 0
    room = budget if result.route != "global" else max(budget // max(len(result.passages), 1), PER_PASSAGE_MIN)
    for p in result.passages:
        loc = f"{rel_path(index, p.file)}:{p.parent['s']}-{p.parent['e']}"
        text = passage_text(p.file, p, room)
        if not text:
            out.append(f"--- {loc}  {_tag(p)}  changed since it was indexed -> search again or read the file")
        elif used + len(text.encode()) <= budget:
            used += len(text.encode())
            out.append(f"=== {loc}  {_tag(p)}{_matched(p)}\n{text.rstrip()}\n")
        else:
            out.append(f"--- {loc}  {_tag(p)}  over budget -> read on demand or delegate to a subagent")
    out += [f"--- {rel_path(index, p.file)}:{p.parent['s']}-{p.parent['e']}  {_tag(p)}  below the relevance cut"
            for p in result.pointers]
    out.append(f"Loaded {used / 1000:.1f} KB of {budget / 1000:.1f} KB budget ({backend}). Cite chunks as path:line.")
    out.append(_action(result))
    return "\n".join(out)


def _action(result: Result) -> str:
    if result.verdict == CORRECT:
        return "Next: answer from these chunks; if the draft has numbers or ids, run `kb_rag.py verify` on it."
    if result.foreign:
        return f"Next: this is a partial answer. {FOREIGN_HINT}"
    gap = f" Not found: {', '.join(result.missing)}." if result.missing else ""
    return f"Next: this is a partial answer.{gap} Rephrase once (synonyms, the language of the notes), " \
           "then fill the gap from repo docs, doc tools or the web and write it into the KB."


def to_json(index: Index, result: Result, budget: int, backend: str) -> dict:
    return {
        "query": result.query, "route": result.route, "why": result.why, "verdict": result.verdict,
        "confidence": result.confidence, "reranker": result.reranker, "corrected": result.corrected,
        "backend": backend, "budget": budget, "missing": result.missing, "paths": result.paths,
        "themes": result.themes, "clusters": result.clusters,
        "results": [{"path": rel_path(index, p.file), "start": p.parent["s"], "end": p.parent["e"],
                     "matched": [p.child["s"], p.child["e"]], "head": p.parent["head"], "score": round(p.score, 3),
                     "cos": round(p.cos, 3), "coverage": round(p.cov, 3), "hop": p.hop,
                     "text": passage_text(p.file, p, budget)} for p in result.passages],
    }


def verify_text(report: dict) -> str:
    marks = {"supported": "[ok]     ", "partial": "[partial]", "unsupported": "[no]     "}
    out = []
    for c in report["claims"]:
        line = f"{marks[c['verdict']]} {c['claim']}"
        if c["evidence"]:
            line += f"  <- {c['evidence']}"
        if c["why"]:
            line += f"  ({c['why']})"
        out.append(line)
    total = len(report["claims"])
    good = sum(c["verdict"] == "supported" for c in report["claims"])
    state = "FAITHFUL" if report["passed"] else "REVISE: fix or drop the claims above, or find a source and add it to the KB"
    out.append(f"Faithfulness {report['score']:.2f} ({good}/{total} supported): {state}")
    return "\n".join(out)
