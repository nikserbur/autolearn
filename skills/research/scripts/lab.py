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

Selection (every run is tested as it ends on results/ID/daily.tsv: a `period` column and a value column):
    lab.py settings --lab DIR [--column pnl] [--block auto|N] [--alpha 0.05]
                                                     fixed for the whole research, before its first run
    lab.py stats --lab DIR ID                        the recorded test of the latest run: deflated Sharpe after N
                                                     trials, block-bootstrap p and interval, LORD++ decision
    lab.py compare --lab DIR NEW OLD                 paired on the same periods: better outside the interval? (once)
    lab.py review --lab DIR ID --verdict PASS|BLOCK --round R03
                                                     the reviewer's verdict on the latest run
    lab.py best --lab DIR ID                         PASS, reviewer PASS, a discovery, DSR >= 0.95, beats the best
    lab.py fdr --lab DIR                             every test with its LORD++ decision and Benjamini-Yekutieli by round

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
import math
import pathlib
import random
import re
import subprocess
import sys

from labkit import protocol, selection, tables

REQUIRED = ("ts", "stage", "action")
EXPECTED = ("inputs", "reason", "outcome")


def read_trace(path: pathlib.Path) -> list:
    lines = [line.rstrip("\r") for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]
    if path.suffix in (".jsonl", ".json"):
        return [json.loads(line) for line in lines]
    if not lines:
        return []
    head = lines[0].split("\t")
    return [dict(zip(head, line.split("\t"))) for line in lines[1:]]


def when(value):
    """A timestamp as a comparable number (epoch seconds) or None. Fractions finer than microseconds
    are cut so Python 3.9 reads nanosecond stamps too."""
    value = str(value if value is not None else "").strip()
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except ValueError:
        pass
    value = re.sub(r"(\.\d{6})\d+", r"\1", re.sub(r"Z$", "+00:00", value))
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp()


def cmd_env(a) -> int:
    rows = tables.requirements(pathlib.Path(a.file), a.stage)
    if not rows:
        print(f"! no requirement table (columns Requirement, Status, Evidence) in {a.file}")
        return 1
    open_ = tables.unproven(rows)
    for line in open_:
        print(f"! {line}")
    print(f"{len(rows) - len(open_)} of {len(rows)} requirements proven")
    return 1 if open_ else 0


def expected_stages(path: pathlib.Path) -> list:
    return [r["stage"].strip("`* ").lower() for r in tables.table_rows(path.read_text(encoding="utf-8"), ("stage",))
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


def numeric(value) -> bool:
    try:
        float(str(value))
        return True
    except ValueError:
        return False


def decisions_before(path: pathlib.Path, cut: float, at_is_number: bool):
    """{stream: [decision, ...]} before the cut, the number of rows at or after it, and problems."""
    keep, out, after, problems = ("ts", "stage", "action", "inputs"), {}, 0, []
    for n, r in enumerate(read_trace(path), 1):
        t = when(r.get("ts"))
        if t is None:
            problems.append(f"{path}: decision {n} has an unreadable ts {r.get('ts')!r}")
        elif numeric(r.get("ts")) != at_is_number:
            problems.append(f"{path}: ts {r.get('ts')!r} and --at are on different clocks (a number and a date)")
        elif t < cut:
            out.setdefault(str(r.get("stream", "")), []).append(tuple(str(r.get(k, "")) for k in keep))
        else:
            after += 1
    return out, after, problems[:3]


def cmd_truncate(a) -> int:
    cut = when(a.at)
    if cut is None:
        print(f"! cannot read --at {a.at}")
        return 2
    if pathlib.Path(a.full).read_bytes() == pathlib.Path(a.cut).read_bytes():
        print("! the two traces are the same file content: run once with the full data and once with the data cut at --at")
        return 1
    full, _, bad_full = decisions_before(pathlib.Path(a.full), cut, numeric(a.at))
    short, after, bad_cut = decisions_before(pathlib.Path(a.cut), cut, numeric(a.at))
    if bad_full or bad_cut:
        for problem in bad_full + bad_cut:
            print(f"! {problem}")
        return 1
    total = sum(len(v) for v in full.values())
    if not total:
        print(f"! no decision before {a.at} in {a.full}: nothing was compared")
        return 1
    for stream in sorted(set(full) | set(short)):
        x_rows, y_rows = full.get(stream, []), short.get(stream, [])
        for n, (x, y) in enumerate(zip(x_rows, y_rows), 1):
            if x != y:
                where = f" in stream {stream}" if stream else ""
                print(f"! decision {n}{where} before {a.at} differs: full data {x[1]} {x[2]} ({x[3]}) at {x[0]}, "
                      f"cut data {y[1]} {y[2]} ({y[3]}) at {y[0]}: something before the cut reads data after it")
                return 1
        if len(x_rows) != len(y_rows):
            print(f"! {len(x_rows)} decisions before {a.at} with the full data, {len(y_rows)} with the cut data"
                  + (f" in stream {stream}" if stream else ""))
            return 1
    if after:
        print(f"WARN  the cut trace has {after} decisions at or after {a.at}: was the data really cut there?")
    print(f"same {total} decisions before {a.at} with the data cut there: no look-ahead across the cut")
    return 0


def digest(path: pathlib.Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def cmd_repro(a) -> int:
    if not a.command or not a.out:
        print("! usage: lab.py repro --out FILE [--out FILE ...] [--runs N>=2] -- COMMAND ...")
        return 2
    if a.runs < 2:
        print("! --runs must be at least 2: one run reproduces nothing")
        return 2
    outs = [pathlib.Path(f) for f in a.out]
    kept = {f: pathlib.Path(f"{f}.repro-kept") for f in outs if f.exists()}
    for f, keep in kept.items():  # the real outputs are put back afterwards, whatever happens
        f.replace(keep)
    try:
        runs = []
        for n in range(a.runs):
            for f in outs:  # each run must write its outputs itself
                if f.exists():
                    f.unlink()
            try:
                done = subprocess.run(a.command, timeout=a.timeout)
            except subprocess.TimeoutExpired:
                print(f"! run {n + 1} took longer than {a.timeout} s")
                return 1
            if done.returncode:
                print(f"! run {n + 1} failed with exit code {done.returncode}")
                return 1
            runs.append([digest(f) for f in outs])
        same = True
        for i, f in enumerate(outs):
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
    finally:
        for f in outs:
            if f in kept:
                kept[f].replace(f)
            elif f.exists():
                f.unlink()


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
    s = with_lab(sub, "settings")
    s.add_argument("--column")
    s.add_argument("--block")
    s.add_argument("--alpha", type=float)
    for name in ("stats", "best"):
        with_lab(sub, name).add_argument("id")
    s = with_lab(sub, "compare")
    s.add_argument("new")
    s.add_argument("old")
    s = with_lab(sub, "review")
    s.add_argument("id")
    s.add_argument("--verdict", choices=selection.VERDICTS, required=True)
    s.add_argument("--round", default="")
    s.add_argument("--by", default="rl-hypothesis-reviewer")
    with_lab(sub, "fdr")
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
            "settings": selection.cmd_settings, "stats": selection.cmd_stats, "compare": selection.cmd_compare,
            "review": selection.cmd_review, "best": selection.cmd_best, "fdr": selection.cmd_fdr,
            "env": cmd_env, "trace": cmd_trace, "truncate": cmd_truncate, "sample": cmd_sample,
            "repro": cmd_repro}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
