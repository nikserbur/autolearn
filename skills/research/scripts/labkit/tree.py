"""The experiments as a tree, and which node to grow next.

Each journal row may name a `parent`: the experiment it changes. `lab.py tree` draws the tree with
what the record says about every node, and `lab.py next` proposes where to spend the next runs, in
the spirit of AIDE's draft / debug / improve and of best-first tree search: fix a crash while the
fix chain is short, grow the nodes with the best lower bound, do not pour every run into one
branch, and start a new idea when the best has stopped improving. It proposes; the controller
designs the experiment and registers it as a child.
"""
from __future__ import annotations

import math

from .ledger import Ledger
from .protocol import current_best, journal, runs

MAX_DEBUG = 2       # crashed fixes in a row before a branch is dropped
MAX_CHILDREN = 3    # children of a node that brought no improvement before it counts as worked out
PATIENCE = 5        # runs without a better best before a new idea is due
MIN_ROOTS = 2       # distinct ideas before the search narrows


def nodes(lab, ledger: Ledger) -> dict:
    """{id: node} for every registered experiment: parent, children, state of its latest run, its test."""
    rows = journal(lab)
    out = {}
    for reg in ledger.find("register"):
        id = reg.get("id")
        if not isinstance(id, str):
            continue
        parent = (reg.get("fields") or {}).get("parent", "") or ""
        out[id] = {"id": id, "parent": parent, "children": [], "state": "not run", "test": None,
                   "decision": (rows.get(id, {}).get("decision") or "").strip().upper(), "seq": reg.get("seq")}
    latest = {}
    for job in runs(ledger):
        latest[job["start"]["id"]] = job
    tests = {e.get("run_seq"): e for e in ledger.find("stats")}
    for id, node in out.items():
        job = latest.get(id)
        if job:
            end = job["end"]
            node["state"] = ("running" if end is None else "interrupted" if end.get("interrupted")
                             else "done" if end.get("exit") == 0 else "crashed")
            node["test"] = tests.get(job["start"]["seq"])
    for id, node in out.items():
        if node["parent"] in out:
            out[node["parent"]]["children"].append(id)
    return out


def lower(node):
    t = node["test"] or {}
    return t.get("lo") if t.get("sharpe") is not None else None


def usable(node) -> bool:
    return node["state"] == "done" and lower(node) is not None and not any(
        w in node["decision"] for w in ("INVALID", "DISCARD"))


def root_of(tree: dict, id: str) -> str:
    seen = set()
    while tree[id]["parent"] in tree and id not in seen:
        seen.add(id)
        id = tree[id]["parent"]
    return id


def debug_depth(tree: dict, id: str) -> int:
    depth, seen = 0, set()
    while id in tree and tree[id]["state"] == "crashed" and id not in seen:
        seen.add(id)
        depth += 1
        id = tree[id]["parent"]
    return depth


def label(node) -> str:
    t = node["test"] or {}
    bits = [node["decision"] or "-", node["state"]]
    if t.get("sharpe") is not None:
        bits.append(f"mean {t['mean']:.4g} ({t['lo']:.4g} to {t['hi']:.4g}) DSR {t['dsr']:.2f}"
                    + (" discovery" if t.get("discovery") else ""))
    elif t.get("note"):
        bits.append(t["note"][:60])
    return "  ".join(bits)


def cmd_tree(a) -> int:
    ledger = Ledger(a.lab)
    tree = nodes(a.lab, ledger)
    if not tree:
        print("no registered experiments yet")
        return 0
    best = (current_best(ledger) or {}).get("id")

    drawn = set()

    def draw(id, prefix, last, root):
        if id in drawn:
            return
        drawn.add(id)
        mark = " [BEST]" if id == best else ""
        print(f"{prefix}{'' if root else '└── ' if last else '├── '}{id}{mark}  {label(tree[id])}")
        inner = prefix + ("" if root else "    " if last else "│   ")
        kids = tree[id]["children"]
        for i, kid in enumerate(kids):
            draw(kid, inner, i == len(kids) - 1, False)

    for root in (id for id, n in tree.items() if n["parent"] not in tree):
        draw(root, "", True, True)
    return 0


def suggestions(lab, ledger: Ledger, k: int) -> list:
    tree = nodes(lab, ledger)
    out = []
    for id, node in tree.items():  # debug: a crash with a short fix chain and no fix yet
        if node["state"] == "crashed" and not node["children"] and debug_depth(tree, id) <= MAX_DEBUG:
            out.append((f"debug {id}", f"it crashed (fix chain {debug_depth(tree, id)} of {MAX_DEBUG}); a fix is a child of it"))
    good = sorted((n for n in tree.values() if usable(n)), key=lower, reverse=True)
    worked_out = {n["id"] for n in good if len(n["children"]) >= MAX_CHILDREN and all(
        (lower(tree[c]) if usable(tree[c]) else -math.inf) <= lower(n) for c in n["children"])}
    per_root, room = {}, max(1, math.ceil(k / 2))
    for n in good:
        if n["id"] in worked_out:
            continue
        r = root_of(tree, n["id"])
        if per_root.get(r, 0) >= room:
            continue  # leave room for other branches: one idea must not take every run
        per_root[r] = per_root.get(r, 0) + 1
        out.append((f"improve {n['id']}", f"lower bound {lower(n):.4g}, {len(n['children'])} children so far"))
    roots = {root_of(tree, id) for id in tree}
    last_best = max((e["seq"] for e in ledger.find("best")), default=0)
    since = sum(1 for s in ledger.find("run_start") if s["seq"] > last_best)
    if len(roots) < MIN_ROOTS:
        out.insert(0, ("draft a new root", f"only {len(roots)} idea{'s' if len(roots) != 1 else ''} so far; "
                                           "start a different one (an experiment without a parent)"))
    elif since >= PATIENCE:
        out.insert(0, ("draft a new root", f"the best has not changed in {since} runs: the search may be stuck "
                                           "on one idea; try a different one"))
    if worked_out:
        out.append(("leave " + ", ".join(sorted(worked_out)), f"{MAX_CHILDREN}+ children and none better"))
    return out[:k] if out else [("draft a new root", "nothing registered yet")]


def cmd_next(a) -> int:
    for action, why in suggestions(a.lab, Ledger(a.lab), a.k):
        print(f"{action}\t{why}")
    return 0
