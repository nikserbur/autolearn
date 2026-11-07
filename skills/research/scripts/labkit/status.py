"""Where a research stands, from its files alone, so a new session (or the user) can pick it up:
`lab.py jobs` lists every run and whether it is still going, and `lab.py status` writes STATUS.md
next to the research KB (outside it, so it is never retrieved as evidence).
"""
from __future__ import annotations

import collections
import datetime
import os
import pathlib
import re

from . import tables
from .ledger import Ledger
from .protocol import current_best, journal

KNOWLEDGE_CAP = 30_000  # bytes of KNOWLEDGE.md before older sections go to KB/archive/


def alive(pid) -> bool:
    if not pid:
        return False
    if os.name == "nt":  # no cheap liveness check: say "unknown" rather than kill-probe
        return True
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except (OSError, ValueError):
        return True
    return True


def jobs(ledger: Ledger) -> list:
    """Every run in ledger order: id, start, state (running, interrupted, done, crashed), exit, seconds.
    A run that started and never ended is running while its process lives, interrupted after."""
    waiting, out = collections.defaultdict(list), []
    for e in ledger.entries():
        if e.get("kind") == "run_start":
            job = {"id": e["id"], "seq": e["seq"], "start": e["ts"], "state": None, "exit": None, "seconds": None,
                   "pid": e.get("pid")}
            out.append(job)
            waiting[e["id"]].append(job)
        elif e.get("kind") == "run_end" and waiting[e["id"]]:
            job = waiting[e["id"]].pop(0)
            job.update(state="done" if e["exit"] == 0 else "crashed", exit=e["exit"], seconds=e["seconds"])
    for job in out:
        if job["state"] is None:
            job["state"] = "running" if alive(job["pid"]) else "interrupted"
    return out


def awaiting_review(ledger: Ledger) -> list:
    """Experiments whose latest run is judged but has no reviewer verdict yet."""
    out = []
    for id in dict.fromkeys(e["id"] for e in ledger.find("judge")):
        run = ledger.last("run_start", id)
        judged = ledger.last("judge", id)
        reviewed = [r for r in ledger.find("review", id) if run and r["run_seq"] == run["seq"]]
        if run and judged and judged.get("run_seq") == run["seq"] and not reviewed:
            out.append(id)
    return out


def summary(lab: pathlib.Path) -> list:
    ledger = Ledger(lab)
    rows = journal(lab)
    lines = [f"# Status: {lab.name}", "",
             f"Written by `lab.py status` on {datetime.datetime.now().isoformat(timespec='minutes')}; "
             "regenerate it, do not edit it.", ""]
    env = lab / "KB" / "ENVIRONMENT.md"
    if not env.exists():
        lines.append("- No ENVIRONMENT.md yet: write it before round 1")
    for stage, name in (("sim", "Simulation"), ("verify", "Verification")) if env.exists() else ():
        reqs = tables.requirements(env, stage)
        open_ = tables.unproven(reqs)
        lines.append(f"- {name} requirements: {len(reqs) - len(open_)} of {len(reqs)} proven"
                     + (f" (open: {', '.join(o.split(':')[0] for o in open_)})" if open_ else ""))
    rounds = sorted({r.get("round", "") for r in rows.values() if r.get("round", "").strip()},
                    key=lambda r: (len(r), r))
    decisions = collections.Counter((r.get("decision", "").strip() or "pending").upper() for r in rows.values())
    lines.append(f"- Rounds: {len(rounds)}" + (f" (latest {rounds[-1]})" if rounds else "")
                 + f"; experiments: {len(rows)} (" + ", ".join(f"{k} {v}" for k, v in sorted(decisions.items())) + ")")
    tests = ledger.find("stats")
    trials = len(ledger.find("run_start")) + sum(max(0, e.get("configs", 1) - 1) for e in tests)
    lines.append(f"- Trials: {trials}; tested runs: {len(tests)}; discoveries under LORD++: "
                 f"{sum(1 for e in tests if e.get('discovery'))}")
    best = current_best(ledger)
    if best:
        st = next((e for e in reversed(ledger.find("stats", best["id"])) if e["run_seq"] == best["run_seq"]), {})
        lines.append(f"- Best: {best['id']}" + (f", mean {st['mean']:.4g} per period (95% interval {st['lo']:.4g} to "
                                                f"{st['hi']:.4g}), deflated Sharpe {st['dsr']:.3f}" if st.get("sharpe") is not None else ""))
        history = [e["id"] for e in ledger.find("best")]
        if len(history) > 1:
            lines.append(f"- Best over time: {' -> '.join(history)}")
    else:
        lines.append("- Best: none yet")
    opens, closes = ledger.find("holdout_open"), ledger.find("holdout_close")
    budget = opens[0]["budget"] if opens else None
    lines.append(f"- Holdout: {len(opens)} look(s)" + (f" of {budget}" if budget else "")
                 + "".join(f"; {c['id']} {c['verdict']}" for c in closes))
    running = [j for j in jobs(ledger) if j["state"] in ("running", "interrupted")]
    review = awaiting_review(ledger)
    knowledge = lab / "KB" / "KNOWLEDGE.md"
    discrepancies = 0
    if knowledge.exists():
        m = re.search(r"(?ms)^## Discrepancies.*?$(.*?)(?=^## |\Z)", knowledge.read_text(encoding="utf-8"))
        discrepancies = len(re.findall(r"(?m)^\s*- (?!<)", m.group(1))) if m else 0
    lines.append(f"- Discrepancies recorded: {discrepancies}")
    lines += ["", "## Next"]
    nexts = [f"- {j['id']}: running since {j['start']} (pid {j['pid']}); `lab.py jobs` shows when it ends"
             for j in running if j["state"] == "running"]
    nexts += [f"- {j['id']}: interrupted (started {j['start']}, no end); rerun with `lab.py run --again`"
              for j in running if j["state"] == "interrupted"]
    nexts += [f"- {id}: judged, awaiting the reviewer's verdict" for id in review]
    for stage, reqs in (("simulation", tables.requirements(env, "sim")),):
        if reqs and tables.unproven(reqs):
            nexts.append(f"- prove the open {stage} requirements before the next round")
    if best and not any(o["id"] == best["id"] for o in opens):
        nexts.append(f"- {best['id']} is the best but has not been looked at on the holdout")
    if knowledge.exists() and knowledge.stat().st_size > KNOWLEDGE_CAP:
        nexts.append("- KNOWLEDGE.md is over 30 KB: move older sections to KB/archive/")
    lines += nexts or ["- nothing pending: plan the next round"]
    return lines


def cmd_jobs(a) -> int:
    found = jobs(Ledger(a.lab))
    if not found:
        print("no runs yet")
        return 0
    print("id\tstate\tstarted\texit\tseconds")
    for j in found:
        print(f"{j['id']}\t{j['state']}\t{j['start']}\t{'' if j['exit'] is None else j['exit']}\t"
              f"{'' if j['seconds'] is None else j['seconds']}")
    return 0


def cmd_status(a) -> int:
    lines = summary(a.lab)
    (a.lab / "STATUS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0
