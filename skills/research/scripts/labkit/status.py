"""Where a research stands, from its files alone, so a new session (or the user) can pick it up:
`lab.py jobs` lists every run and whether it is still going, and `lab.py status` writes STATUS.md
next to the research KB (outside it, so it is never retrieved as evidence).
"""
from __future__ import annotations

import collections
import datetime
import pathlib
import re

from . import tables
from .ledger import Ledger
from .protocol import KNOWLEDGE_CAP, current_best, fail, journal, live, runs, trial_count


def jobs(lab: pathlib.Path, ledger: Ledger) -> list:
    """Every run in ledger order: id, start, state (running, interrupted, done, crashed), exit, seconds."""
    out = []
    for job in runs(ledger):
        start, end = job["start"], job["end"]
        if end is None:
            state = "running" if live(lab, start) else "interrupted"
        elif end.get("interrupted"):
            state = "interrupted"
        elif end.get("timed_out"):
            state = "timed out"
        else:
            state = "done" if end.get("exit") == 0 else "crashed"
        out.append({"id": start["id"], "seq": start.get("seq"), "start": start.get("ts", "?"), "state": state,
                    "exit": end.get("exit") if end else None, "seconds": end.get("seconds") if end else None,
                    "pid": start.get("pid")})
    return out


def waiting(lab: pathlib.Path, ledger: Ledger) -> list:
    """What the research owes, experiment by experiment, in the order it should be done."""
    rows, out = journal(lab), []
    registered = {e.get("id") for e in ledger.find("register")}
    latest = {}
    for job in jobs(lab, ledger):
        latest[job["id"]] = job
    for id, job in latest.items():
        if job["state"] == "running":
            out.append(f"- {id}: running since {job['start']} (pid {job['pid']}); `lab.py jobs` shows when it ends")
        elif job["state"] == "interrupted":
            out.append(f"- {id}: interrupted (started {job['start']}); rerun it with `lab.py run --again` or drop it")
        else:
            judged = ledger.last("judge", id)
            if not judged or judged.get("run_seq") != job["seq"]:
                out.append(f"- {id}: {job['state']}, not judged yet: collect it (trace) and run `lab.py judge`")
            elif judged.get("verdict") == "PASS" and not any(r.get("run_seq") == job["seq"] for r in ledger.find("review", id)):
                out.append(f"- {id}: judged PASS, awaiting the reviewer's verdict")
    for id in sorted(i for i in registered - set(latest) if isinstance(i, str)):
        out.append(f"- {id}: registered, never run")
    for id, row in rows.items():
        if id not in registered and not row.get("result", "").strip():
            out.append(f"- {id}: in the journal but not registered (`lab.py register`)")
    return out


def summary(lab: pathlib.Path) -> list:
    ledger = Ledger(lab)
    rows = journal(lab)
    lines = [f"# Status: {lab.name}", "",
             f"Written by `lab.py status` on {datetime.datetime.now().isoformat(timespec='minutes')}; "
             "regenerate it, do not edit it.", ""]
    for problem in ledger.broken()[:3]:
        lines.append(f"- LEDGER PROBLEM: {problem} (`lab.py check`)")
    env = lab / "KB" / "ENVIRONMENT.md"
    open_sim = []
    if not env.exists():
        lines.append("- No ENVIRONMENT.md yet: write it before round 1")
    for stage, name in (("sim", "Simulation"), ("verify", "Verification")) if env.exists() else ():
        reqs = tables.requirements(env, stage)
        open_ = tables.unproven(reqs)
        open_sim = open_ if stage == "sim" else open_sim
        lines.append(f"- {name} requirements: " + (f"{len(reqs) - len(open_)} of {len(reqs)} proven" if reqs else
                     "no requirement table") + (f" (open: {', '.join(o.split(':')[0] for o in open_)})" if open_ else ""))
    rounds = sorted({r.get("round", "") for r in rows.values() if r.get("round", "").strip()}, key=lambda r: (len(r), r))
    decisions = collections.Counter((r.get("decision", "").strip() or "pending").upper() for r in rows.values())
    lines.append(f"- Rounds: {len(rounds)}" + (f" (latest {rounds[-1]})" if rounds else "")
                 + f"; experiments: {len(rows)} (" + ", ".join(f"{k} {v}" for k, v in sorted(decisions.items())) + ")")
    tests = ledger.find("stats")
    lines.append(f"- Trials: {trial_count(ledger)}; tested runs: {len(tests)}; discoveries under LORD++: "
                 f"{sum(1 for e in tests if e.get('discovery'))}")
    best = current_best(ledger)
    stale = False
    if best:
        st = next((e for e in reversed(ledger.find("stats", best.get("id"))) if e.get("run_seq") == best.get("run_seq")), {})
        latest = ledger.last("run_start", best.get("id")) or {}
        stale = latest.get("seq") != best.get("run_seq")
        numbers = (f", mean {st['mean']:.4g} per period (95% interval {st['lo']:.4g} to {st['hi']:.4g}), "
                   f"deflated Sharpe {st['dsr']:.3f}") if st.get("sharpe") is not None else ""
        lines.append(f"- Best: {best.get('id')}{numbers}" + (" (it has run again since: the best refers to an "
                                                             "earlier run and cannot go to the holdout)" if stale else ""))
        history = [e.get("id") for e in ledger.find("best")]
        if len(history) > 1:
            lines.append(f"- Best over time: {' -> '.join(map(str, history))}")
    else:
        lines.append("- Best: none yet")
    opens, closes = ledger.find("holdout_open"), ledger.find("holdout_close")
    budget = opens[0].get("budget") if opens else None
    lines.append(f"- Holdout: {len(opens)} look(s)" + (f" of {budget}" if budget else "")
                 + "".join(f"; {c.get('id')} {c.get('verdict')}" for c in closes))
    knowledge = lab / "KB" / "KNOWLEDGE.md"
    discrepancies = 0
    if knowledge.exists():
        m = re.search(r"(?ms)^## Discrepancies.*?$(.*?)(?=^## |\Z)", knowledge.read_text(encoding="utf-8", errors="replace"))
        discrepancies = len(re.findall(r"(?m)^\s*- (?!<)", m.group(1))) if m else 0
    lines.append(f"- Discrepancies recorded: {discrepancies}")
    nexts = waiting(lab, ledger)
    if open_sim:
        nexts.append("- prove the open simulation requirements before the next round")
    if best and not stale and not any(o.get("id") == best.get("id") for o in opens):
        nexts.append(f"- {best.get('id')} is the best but has not been looked at on the holdout")
    if knowledge.exists() and knowledge.stat().st_size > KNOWLEDGE_CAP:
        nexts.append(f"- KNOWLEDGE.md is over {KNOWLEDGE_CAP // 1000} KB: move older sections to KB/archive/")
    return lines + ["", "## Next"] + (nexts or ["- nothing pending: plan the next round"])


def missing_lab(lab: pathlib.Path) -> int:
    if not (lab / "KB").is_dir():
        return fail(f"{lab} is not a research folder (no KB/ in it): --lab names research/<slug>")
    return 0


def cmd_jobs(a) -> int:
    if missing_lab(a.lab):
        return 1
    found = jobs(a.lab, Ledger(a.lab))
    if not found:
        print("no runs yet")
        return 0
    print("id\tstate\tstarted\texit\tseconds")
    for j in found:
        print(f"{j['id']}\t{j['state']}\t{j['start']}\t{'' if j['exit'] is None else j['exit']}\t"
              f"{'' if j['seconds'] is None else j['seconds']}")
    return 0


def cmd_status(a) -> int:
    if missing_lab(a.lab):
        return 1
    lines = summary(a.lab)
    (a.lab / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0
