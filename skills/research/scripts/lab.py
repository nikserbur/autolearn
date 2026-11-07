#!/usr/bin/env python3
"""Checks for the environments of a research loop. Standard library only, Python 3.9+.

    lab.py env KB/ENVIRONMENT.md                     requirements still unproven (exit 1 if any)
    lab.py trace results/E7/trace.tsv [--expect KB/expect/E7.md]
                                                     the decision trace is usable and looks only back (exit 1 if not)
    lab.py sample results/E7/trace.tsv [--n 3] [--metric outcome] [--seed 0]
                                                     decisions for the reviewer to read one by one
    lab.py holdout KB/HOLDOUT.tsv log --candidate C --result R [--by WHO]
    lab.py holdout KB/HOLDOUT.tsv status [--budget 1]
                                                     looks at the verification data (exit 1 if over budget)
    lab.py repro --out FILE [--out FILE ...] [--runs 2] -- COMMAND ...
                                                     the same command gives the same files (exit 1 if not)

A trace is a TSV with a header or JSON lines, one row per decision. Required: ts, stage, action.
Expected: inputs (what the decision looked at), reason, outcome. Optional: inputs_ts (the newest
timestamp among the inputs), stream (independent clocks, e.g. one per instrument), cost.
"""
from __future__ import annotations

import argparse
import collections
import datetime
import hashlib
import json
import pathlib
import random
import re
import subprocess
import sys

REQUIRED = ("ts", "stage", "action")
EXPECTED = ("inputs", "reason", "outcome")
PROVEN = {"✅", "ok", "yes", "pass", "done"}
NOT_APPLICABLE = {"n/a", "na", "-"}
HOLDOUT_HEADER = "date\tcandidate\tresult\tby\n"


def read_trace(path: pathlib.Path) -> list:
    text = path.read_text(encoding="utf-8")
    if path.suffix in (".jsonl", ".json"):
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    head = lines[0].split("\t")
    return [dict(zip(head, line.split("\t"))) for line in lines[1:]]


def when(value):
    """A timestamp as a comparable number (epoch seconds) or None."""
    value = str(value or "").strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        moment = datetime.datetime.fromisoformat(re.sub(r"Z$", "+00:00", value))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def table_rows(text: str, needs: tuple) -> list:
    """Data rows of the markdown tables whose header has all the `needs` columns, as dicts."""
    rows, head = [], None
    for line in text.splitlines():
        if not line.strip().startswith("|"):
            head = None
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if head is None:
            lowered = [c.lower() for c in cells]
            head = lowered if all(n in lowered for n in needs) else []
        elif head and not all(re.fullmatch(r":?-+:?", c) for c in cells if c):
            rows.append(dict(zip(head, cells)))
    return rows


def cmd_env(a) -> int:
    rows = table_rows(pathlib.Path(a.file).read_text(encoding="utf-8"), ("requirement", "status"))
    if not rows:
        print(f"! no requirement table (columns Requirement, Status, Evidence) in {a.file}")
        return 1
    open_ = []
    for r in rows:
        status, evidence = r.get("status", "").lower(), r.get("evidence", "")
        name = f"{r.get('#', '')} {r.get('requirement', '')[:70]}".strip()
        if status in PROVEN and not evidence:
            open_.append(f"{name}: marked done without evidence")
        elif status in NOT_APPLICABLE and not evidence:
            open_.append(f"{name}: n/a without a reason")
        elif status not in PROVEN | NOT_APPLICABLE:
            open_.append(f"{name}: {'not met' if status in ('❌', 'no', 'fail') else 'not checked'}")
    for line in open_:
        print(f"! {line}")
    print(f"{len(rows) - len(open_)} of {len(rows)} requirements proven")
    return 1 if open_ else 0


def expected_stages(path: pathlib.Path) -> list:
    return [r["stage"].strip("`* ").lower() for r in table_rows(path.read_text(encoding="utf-8"), ("stage",))
            if r.get("stage", "").strip("`* ")]


def cmd_trace(a) -> int:
    rows = read_trace(pathlib.Path(a.file))
    errors, warnings = [], []
    columns = set().union(*(r.keys() for r in rows)) if rows else set()
    missing = [c for c in REQUIRED if c not in columns]
    if not rows:
        errors.append("the trace is empty")
    elif missing:
        errors.append(f"no {', '.join(missing)} column: a trace needs {', '.join(REQUIRED)}")
    for c in EXPECTED:
        if rows and c not in columns:
            warnings.append(f"no {c} column: the reviewer cannot see {'what decisions looked at' if c == 'inputs' else c}")
    last, bad_ts, back, ahead = {}, [], [], []
    for n, r in enumerate(rows, 2 if pathlib.Path(a.file).suffix not in (".jsonl", ".json") else 1):
        t = when(r.get("ts"))
        if t is None:
            bad_ts.append(n)
            continue
        stream = r.get("stream", "")
        if stream in last and t < last[stream]:
            back.append(n)
        last[stream] = max(t, last.get(stream, t))
        seen = when(r.get("inputs_ts"))
        if seen is not None and seen > t:
            ahead.append(n)
    if bad_ts and "ts" in columns:
        errors.append(f"{len(bad_ts)} rows with an unreadable ts (first: line {bad_ts[0]})")
    if back:
        errors.append(f"time goes back within a stream on {len(back)} rows (first: line {back[0]})")
    if ahead:
        errors.append(f"look-ahead: inputs newer than the decision on {len(ahead)} rows (first: line {ahead[0]})")
    if rows and "inputs" in columns:
        blind = sum(1 for r in rows if not str(r.get("inputs") or "").strip())
        if blind:
            warnings.append(f"{blind} decisions do not say what they looked at (empty inputs)")
    stages = collections.Counter(str(r.get("stage", "")).lower() for r in rows if r.get("stage"))
    if a.expect:
        want = expected_stages(pathlib.Path(a.expect))
        for s in want:
            if s not in stages:
                warnings.append(f"stage '{s}' is expected but never in the trace")
        for s in stages:
            if want and s not in want:
                warnings.append(f"stage '{s}' is in the trace but not in the expectation")
    print(f"{len(rows)} decisions; stages: " + ", ".join(f"{s} {n}" for s, n in stages.most_common()))
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    return 1 if errors else 0


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def cmd_sample(a) -> int:
    rows = read_trace(pathlib.Path(a.file))
    picked, chosen = [], set()

    def take(why, indexes):
        for i in indexes:
            if i not in chosen:
                chosen.add(i)
                picked.append((why, i))

    scored = sorted((number(r.get(a.metric)), i) for i, r in enumerate(rows) if number(r.get(a.metric)) is not None)
    take("best", [i for _, i in reversed(scored[-a.n:])])
    take("worst", [i for _, i in scored[:a.n]])
    shape = lambda r: (r.get("stage"), re.sub(r"[-+]?\d[\d.,:]*", "#", str(r.get("action", ""))))  # prices aside
    kinds = collections.Counter(shape(r) for r in rows)
    take("rare", [i for i, r in enumerate(rows) if kinds[shape(r)] <= 2][:a.n])
    rest = [i for i in range(len(rows)) if i not in chosen]
    take("random", sorted(random.Random(a.seed).sample(rest, min(a.n, len(rest)))))
    if not picked:
        print("! nothing to sample")
        return 1
    head = list(rows[0].keys())
    print("\t".join(["why", "row"] + head))
    for why, i in picked:
        print("\t".join([why, str(i + 1)] + [str(rows[i].get(c, "")) for c in head]))
    return 0


def cmd_holdout(a) -> int:
    path = pathlib.Path(a.file)
    if a.action == "log":
        if not a.candidate or a.result is None:
            print("! log needs --candidate and --result")
            return 2
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(HOLDOUT_HEADER, encoding="utf-8")
        clean = lambda s: re.sub(r"\s+", " ", s).strip()
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{datetime.date.today().isoformat()}\t{clean(a.candidate)}\t{clean(a.result)}\t{clean(a.by)}\n")
    looks = collections.OrderedDict()
    if path.exists():
        for row in read_trace(path):
            looks.setdefault(row.get("candidate", ""), []).append(row.get("result", ""))
    spoiled = [c for c, r in looks.items() if len(r) > a.budget]
    for c, results in looks.items():
        state = "SPOILED: over the look budget" if c in spoiled else "ok"
        print(f"{c}\t{len(results)} look(s)\tlast: {results[-1]}\t{state}")
    if not looks:
        print("no looks at the verification data yet")
    return 1 if spoiled else 0


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def cmd_repro(a) -> int:
    if not a.command or not a.out:
        print("! usage: lab.py repro --out FILE [--out FILE ...] -- COMMAND ...")
        return 2
    runs = []
    for n in range(a.runs):
        done = subprocess.run(a.command)
        if done.returncode:
            print(f"! run {n + 1} failed with exit code {done.returncode}")
            return 1
        runs.append([digest(pathlib.Path(f)) for f in a.out])
    same = True
    for i, f in enumerate(a.out):
        hashes = {r[i] for r in runs}
        if "" in hashes:
            print(f"! {f}: not written")
            same = False
        elif len(hashes) > 1:
            print(f"DIFFERENT  {f}")
            same = False
        else:
            print(f"same       {f}  sha256 {runs[0][i][:16]}")
    return 0 if same else 1


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if "--" in argv:
        command, argv = argv[argv.index("--") + 1:], argv[:argv.index("--")]
    p = argparse.ArgumentParser(prog="lab.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("env")
    s.add_argument("file")
    s = sub.add_parser("trace")
    s.add_argument("file")
    s.add_argument("--expect")
    s = sub.add_parser("sample")
    s.add_argument("file")
    s.add_argument("--n", type=int, default=3)
    s.add_argument("--metric", default="outcome")
    s.add_argument("--seed", type=int, default=0)
    s = sub.add_parser("holdout")
    s.add_argument("file")
    s.add_argument("action", choices=("log", "status"))
    s.add_argument("--candidate", default="")
    s.add_argument("--result")
    s.add_argument("--by", default="rl-hypothesis-reviewer")
    s.add_argument("--budget", type=int, default=1)
    s = sub.add_parser("repro")
    s.add_argument("--out", action="append", default=[])
    s.add_argument("--runs", type=int, default=2)
    a = p.parse_args(argv)
    a.command = command
    return {"env": cmd_env, "trace": cmd_trace, "sample": cmd_sample,
            "holdout": cmd_holdout, "repro": cmd_repro}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
