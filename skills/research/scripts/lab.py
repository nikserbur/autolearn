#!/usr/bin/env python3
"""The research loop's protocol and environment checks. Standard library only, Python 3.9+.

Protocol (state lives in <lab>/LEDGER.jsonl, append-only and hash-chained; <lab> is research/<slug>):
    lab.py register --lab DIR ID                     fix the journal row and expect/ID.md; parse the criterion
    lab.py run --lab DIR ID [--code F] [--config F] [--data F] [--again] -- COMMAND ...
                                                     run it, record the candidate hash and every attempt
    lab.py judge --lab DIR ID                        apply the registered criterion to results/ID/metrics.tsv
    lab.py check --lab DIR                           edits after registration, KEEP without PASS, broken chain
    lab.py holdout --lab DIR open|close|status [ID] [--metrics F] [--budget N]
                                                     one look budget per research, opened before the run
    lab.py brief --lab DIR BRIEF.md                  every number in the brief traces to the record

Environment:
    lab.py env KB/ENVIRONMENT.md [--stage sim|verify]
                                                     requirements still unproven (exit 1 if any)
    lab.py trace results/E7/trace.tsv [--expect KB/expect/E7.md] [--min-delay SECONDS]
                                                     the decision trace is usable and looks only back
    lab.py truncate FULL_TRACE CUT_TRACE --at T      decisions before T are the same with the data cut at T
    lab.py sample results/E7/trace.tsv [--n 3] [--metric outcome] [--seed 0]
                                                     decisions for the reviewer to read one by one
    lab.py repro --out FILE [--out FILE ...] [--runs 2] [--timeout S] -- COMMAND ...
                                                     the same command rewrites the same files

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

from labkit import protocol

REQUIRED = ("ts", "stage", "action")
EXPECTED = ("inputs", "reason", "outcome")
PROVEN = {"✅", "ok", "yes", "pass", "done"}
NOT_APPLICABLE = {"n/a", "na", "-"}
STAGE_PREFIX = {"sim": "S", "verify": "V"}


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
    if a.stage != "all":
        rows = [r for r in rows if r.get("#", "").upper().startswith(STAGE_PREFIX[a.stage])]
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
    last, bad_ts, back, ahead, same = {}, [], [], [], 0
    delay = a.min_delay or 0.0
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
        if seen is not None and (seen > t - delay if a.min_delay else seen > t):
            ahead.append(n)
        same += seen is not None and seen == t
    if bad_ts and "ts" in columns:
        errors.append(f"{len(bad_ts)} rows with an unreadable ts (first: line {bad_ts[0]})")
    if back:
        errors.append(f"time goes back within a stream on {len(back)} rows (first: line {back[0]})")
    if ahead:
        what = f"less than {delay:g} s before the decision" if a.min_delay else "newer than the decision"
        errors.append(f"look-ahead: inputs {what} on {len(ahead)} rows (first: line {ahead[0]})")
    if rows and "inputs_ts" not in columns:
        (errors if a.min_delay else warnings).append(
            "no inputs_ts column: look-ahead is not checked, so this trace is no evidence for S1")
    elif rows and same == len(rows) and not a.min_delay:
        warnings.append("inputs_ts equals ts on every row: self-reported, not evidence for S1; "
                        "prove it with `lab.py truncate` or give --min-delay")
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


def decisions_before(path: pathlib.Path, cut: float) -> list:
    keep = ("ts", "stream", "stage", "action", "inputs")
    return [tuple(str(r.get(k, "")) for k in keep) for r in read_trace(path) if (when(r.get("ts")) or cut) < cut]


def cmd_truncate(a) -> int:
    cut = when(a.at)
    if cut is None:
        print(f"! cannot read --at {a.at}")
        return 2
    full, short = decisions_before(pathlib.Path(a.full), cut), decisions_before(pathlib.Path(a.cut), cut)
    for n, (x, y) in enumerate(zip(full, short), 1):
        if x != y:
            print(f"! decision {n} before {a.at} differs: full data {x[2]} {x[3]} ({x[4]}) at {x[0]}, "
                  f"cut data {y[2]} {y[3]} ({y[4]}) at {y[0]}: something before the cut reads data after it")
            return 1
    if len(full) != len(short):
        print(f"! {len(full)} decisions before {a.at} with the full data, {len(short)} with the cut data")
        return 1
    print(f"same {len(full)} decisions before {a.at} with the data cut there: no look-ahead across the cut")
    return 0


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def cmd_repro(a) -> int:
    if not a.command or not a.out:
        print("! usage: lab.py repro --out FILE [--out FILE ...] -- COMMAND ...")
        return 2
    runs = []
    for n in range(a.runs):
        for f in a.out:  # each run must write its outputs itself; a file left from before proves nothing
            if pathlib.Path(f).exists():
                pathlib.Path(f).replace(f"{f}.repro-before")
        try:
            done = subprocess.run(a.command, timeout=a.timeout)
        except subprocess.TimeoutExpired:
            print(f"! run {n + 1} took longer than {a.timeout} s")
            return 1
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
    for f in a.out:
        before = pathlib.Path(f"{f}.repro-before")
        if before.exists():
            before.unlink()
    return 0 if same else 1


def with_lab(sub, name: str):
    s = sub.add_parser(name)
    s.add_argument("--lab", type=pathlib.Path, default=pathlib.Path("."), help="the research folder (research/<slug>)")
    return s


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = []
    if "--" in argv:
        command, argv = argv[argv.index("--") + 1:], argv[:argv.index("--")]
    p = argparse.ArgumentParser(prog="lab.py", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("register", "judge"):
        with_lab(sub, name).add_argument("id")
    s = with_lab(sub, "run")
    s.add_argument("id")
    for flag in ("--code", "--config", "--data"):
        s.add_argument(flag, action="append", default=[])
    s.add_argument("--again", action="store_true")
    with_lab(sub, "check")
    s = with_lab(sub, "holdout")
    s.add_argument("action", choices=("open", "close", "status"))
    s.add_argument("id", nargs="?")
    s.add_argument("--metrics")
    s.add_argument("--budget", type=int)
    s.add_argument("--by", default="rl-hypothesis-reviewer")
    s = with_lab(sub, "brief")
    s.add_argument("file")
    s = sub.add_parser("env")
    s.add_argument("file")
    s.add_argument("--stage", choices=("sim", "verify", "all"), default="all")
    s = sub.add_parser("trace")
    s.add_argument("file")
    s.add_argument("--expect")
    s.add_argument("--min-delay", type=float)
    s = sub.add_parser("truncate")
    s.add_argument("full")
    s.add_argument("cut")
    s.add_argument("--at", required=True)
    s = sub.add_parser("sample")
    s.add_argument("file")
    s.add_argument("--n", type=int, default=3)
    s.add_argument("--metric", default="outcome")
    s.add_argument("--seed", type=int, default=0)
    s = sub.add_parser("repro")
    s.add_argument("--out", action="append", default=[])
    s.add_argument("--runs", type=int, default=2)
    s.add_argument("--timeout", type=float)
    a = p.parse_args(argv)
    a.command = command
    return {"register": protocol.cmd_register, "run": protocol.cmd_run, "judge": protocol.cmd_judge,
            "check": protocol.cmd_check, "holdout": protocol.cmd_holdout, "brief": protocol.cmd_brief,
            "env": cmd_env, "trace": cmd_trace, "truncate": cmd_truncate, "sample": cmd_sample,
            "repro": cmd_repro}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
