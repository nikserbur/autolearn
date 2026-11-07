"""Protocol commands of lab.py: register, run, judge, check, holdout, brief. They are the only way the
research loop changes its state, and every change is a ledger entry. `lab` below is the research
folder (`research/<slug>`), which holds KB/, results/, sealed/ and LEDGER.jsonl.
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import time

from .ledger import Ledger, judge, parse_criterion, read_journal, read_metrics, sha256_bytes, sha256_file

PRE_RUN = ("id", "round", "hypothesis", "change", "expected", "pass_criterion", "commit", "data", "seed")
BRIEF_NUMBER = re.compile(r"(?<![\w.])[-+]?(?:\d+\.\d+%?|\d+%)(?!\.\d)")
CELL_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")


def fail(message: str) -> int:
    print(f"! {message}")
    return 1


def journal(lab: pathlib.Path) -> dict:
    return read_journal(lab / "KB" / "EXPERIMENTS.tsv")


def expect_file(lab: pathlib.Path, id: str) -> pathlib.Path:
    return lab / "KB" / "expect" / f"{id}.md"


def edits(lab: pathlib.Path, ledger: Ledger, id: str) -> list:
    """What changed in a registered experiment's pre-run fields or expectation since registration."""
    reg = ledger.last("register", id)
    if not reg:
        return [f"{id} is not registered"]
    row, problems = journal(lab).get(id), []
    if row is None:
        problems.append("its row is gone from KB/EXPERIMENTS.tsv")
    else:
        problems += [f"{k} changed after registration" for k, v in reg["fields"].items() if row.get(k, "") != v]
    if sha256_file(expect_file(lab, id)) != reg["expect_sha"]:
        problems.append(f"expect/{id}.md changed after registration")
    return problems


def cmd_register(a) -> int:
    ledger, row = Ledger(a.lab), journal(a.lab).get(a.id)
    if row is None:
        return fail(f"no row {a.id} in KB/EXPERIMENTS.tsv: write it first")
    if not expect_file(a.lab, a.id).is_file():
        return fail(f"no KB/expect/{a.id}.md: write the expected behaviour first")
    if ledger.find("register", a.id):
        return fail(f"{a.id} is registered already; a changed experiment gets a new id")
    try:
        parse_criterion(row.get("pass_criterion", ""))
    except ValueError as e:
        return fail(f"pass_criterion of {a.id}: {e}")
    ledger.append("register", id=a.id, fields={k: row.get(k, "") for k in PRE_RUN},
                  expect_sha=sha256_file(expect_file(a.lab, a.id)), criterion=row["pass_criterion"])
    print(f"registered {a.id}: {row['pass_criterion']}. Its row and expect/{a.id}.md are fixed from now on.")
    return 0


def cmd_run(a) -> int:
    if not a.command:
        return fail("usage: lab.py run --lab DIR ID [--code F] [--config F] [--data F] -- COMMAND ...")
    ledger = Ledger(a.lab)
    problems = edits(a.lab, ledger, a.id)
    if problems:
        return fail(f"{a.id}: " + "; ".join(problems))
    if ledger.find("run_start", a.id) and not a.again:
        return fail(f"{a.id} has run already; --again runs it once more and counts as another trial")
    hashes = {}
    for role, paths in (("code", a.code), ("config", a.config), ("data", a.data)):
        for p in paths:
            if not pathlib.Path(p).is_file():
                return fail(f"{role} file {p} does not exist")
            hashes[f"{role}:{p}"] = sha256_file(pathlib.Path(p))
    candidate = sha256_bytes(json.dumps(hashes, sort_keys=True).encode("utf-8"))[:16]
    out = a.lab / "results" / a.id
    (out / "code").mkdir(parents=True, exist_ok=True)
    for p in a.code:
        shutil.copy2(p, out / "code" / p.replace("/", "__"))
    for name in ("metrics.tsv", "trace.tsv"):  # a stale file must not pass for this run's output
        if (out / name).exists():
            (out / name).replace(out / f"{name}.previous")
    if (out / "DONE").exists():
        (out / "DONE").unlink()
    start = time.time()
    ledger.append("run_start", id=a.id, command=a.command, candidate=candidate, hashes=hashes, pid=os.getpid())
    info = {"id": a.id, "command": a.command, "candidate": candidate, "pid": os.getpid(),
            "start": datetime.datetime.now().isoformat(timespec="seconds")}
    (out / "RUN.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    with (out / "run.log").open("w", encoding="utf-8") as log:
        try:
            code = subprocess.run(a.command, stdout=log, stderr=subprocess.STDOUT,
                                  env=dict(os.environ, RESEARCH_LAB_RUN=a.id)).returncode
        except OSError as e:
            log.write(f"lab.py: could not start the command: {e}\n")
            code = 127
    seconds = round(time.time() - start, 1)
    ledger.append("run_end", id=a.id, exit=code, seconds=seconds, metrics_sha=sha256_file(out / "metrics.tsv"))
    info.update(end=datetime.datetime.now().isoformat(timespec="seconds"), exit=code, seconds=seconds)
    (out / "RUN.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    (out / "DONE").write_text(f"{code}\n", encoding="utf-8")
    print(f"{a.id}: exit {code} after {seconds} s, candidate {candidate}, log results/{a.id}/run.log")
    return code


def cmd_judge(a) -> int:
    ledger = Ledger(a.lab)
    problems = edits(a.lab, ledger, a.id)
    if problems:
        return fail(f"{a.id}: " + "; ".join(problems))
    end = ledger.last("run_end", a.id)
    if not end:
        return fail(f"{a.id} has no finished `lab.py run`")
    metrics_path = a.lab / "results" / a.id / "metrics.tsv"
    if end["exit"] != 0:
        verdict, lines, metrics = "CRASH", [f"the run exited with code {end['exit']}"], {}
    elif sha256_file(metrics_path) != end["metrics_sha"] or not metrics_path.is_file():
        return fail(f"results/{a.id}/metrics.tsv is missing or changed after the run")
    else:
        clauses = parse_criterion(ledger.last("register", a.id)["criterion"])
        metrics = read_metrics(metrics_path)
        try:
            verdict, lines = judge(clauses, metrics)
        except KeyError as e:
            return fail(f"results/{a.id}/metrics.tsv has no metric {e}")
        metrics = {name: metrics[name] for name, _, _ in clauses}
    trials = len(ledger.find("run_start"))
    ledger.append("judge", id=a.id, verdict=verdict, metrics=metrics, trials=trials)
    print(f"{a.id}: {verdict}  ({trials} trial{'s' if trials != 1 else ''} so far in this research)")
    for line in lines:
        print(f"  {line}")
    return 0 if verdict == "PASS" else 1


def unfinished(ledger: Ledger) -> list:
    out = []
    for start in ledger.find("run_start"):
        if not any(e["seq"] > start["seq"] for e in ledger.find("run_end", start["id"])):
            out.append(start)
    return out


def cmd_check(a) -> int:
    ledger = Ledger(a.lab)
    errors = [f"ledger {p}" for p in ledger.broken()]
    registered = {e["id"] for e in ledger.find("register")}
    for id in sorted(registered):
        errors += [f"{id}: {p}" for p in edits(a.lab, ledger, id)]
    rows, best = journal(a.lab), []
    for id, row in rows.items():
        decision = row.get("decision", "").upper()
        if "BEST" in decision:
            best.append(id)
        if ("KEEP" in decision or "BEST" in decision) and (ledger.last("judge", id) or {}).get("verdict") != "PASS":
            errors.append(f"{id}: marked {row.get('decision')} without a PASS from lab.py judge")
        if id not in registered and row.get("result", "").strip():
            errors.append(f"{id}: has a result but was never registered")
    warnings = [f"{e['id']}: started {e['ts']} (pid {e.get('pid')}), no end: running, or crashed" for e in unfinished(ledger)]
    if len(best) > 1:
        warnings.append(f"more than one BEST: {', '.join(best)}")
    judged = [e["verdict"] for e in ledger.find("judge")]
    print(f"{len(registered)} registered, {len(ledger.find('run_start'))} runs (trials), "
          f"{judged.count('PASS')} PASS / {judged.count('FAIL')} FAIL / {judged.count('CRASH')} CRASH, "
          f"{len(ledger.find('holdout_open'))} holdout look(s)")
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    return 1 if errors else 0


def cmd_holdout(a) -> int:
    ledger = Ledger(a.lab)
    opens, closes = ledger.find("holdout_open"), ledger.find("holdout_close")
    budget = opens[0]["budget"] if opens else (a.budget or 1)
    if a.action == "status":
        print(f"look budget {budget} for this research; {len(opens)} used")
        for c in closes:
            print(f"  look {c['look']}: {c['id']} {c['verdict']} {json.dumps(c['metrics'])}")
        if len(opens) > len(closes):
            print(f"  look {len(opens)}: {opens[-1]['id']} open")
        return 0
    if not a.id:
        return fail(f"holdout {a.action} needs an experiment id")
    if a.action == "open":
        if opens and a.budget and a.budget != budget:
            return fail(f"the look budget was fixed at {budget} by the first look")
        if (ledger.last("judge", a.id) or {}).get("verdict") != "PASS":
            return fail(f"{a.id} was not judged PASS in simulation; only such a candidate is looked at")
        problems = edits(a.lab, ledger, a.id)
        if problems:
            return fail(f"{a.id}: " + "; ".join(problems))
        spent = [c for c in closes if c["verdict"] != "PASS"]
        if spent:
            return fail(f"the holdout is spent: {spent[0]['id']} failed on it ({spent[0]['ts'][:10]}); "
                        "confirm on a new, later window written into ENVIRONMENT.md")
        if len(opens) > len(closes):
            return fail(f"the look for {opens[-1]['id']} is still open; close it first")
        if len(opens) >= budget:
            return fail(f"look budget {budget} is used; another look would make any result SPOILED")
        candidate = ledger.last("run_start", a.id)["candidate"]
        ledger.append("holdout_open", id=a.id, candidate=candidate, budget=budget, look=len(opens) + 1, by=a.by)
        (a.lab / "sealed").mkdir(parents=True, exist_ok=True)
        print(f"look {len(opens) + 1} of {budget} is open for {a.id} (candidate {candidate}). Run it once on the "
              f"holdout, write the report into sealed/ (outside KB/), then: lab.py holdout close {a.id} --metrics FILE")
        return 0
    if not opens or len(opens) == len(closes) or opens[-1]["id"] != a.id:
        return fail(f"no open look for {a.id}")
    if not a.metrics or not pathlib.Path(a.metrics).is_file():
        return fail("close needs --metrics FILE from the holdout run")
    clauses = parse_criterion(ledger.last("register", a.id)["criterion"])
    metrics = read_metrics(pathlib.Path(a.metrics))
    try:
        verdict, lines = judge(clauses, metrics)
    except KeyError as e:
        return fail(f"{a.metrics} has no metric {e}")
    ledger.append("holdout_close", id=a.id, verdict=verdict, look=len(opens),
                  metrics={name: metrics[name] for name, _, _ in clauses})
    print(f"{a.id} on the holdout: {verdict}")
    for line in lines:
        print(f"  {line}")
    return 0 if verdict == "PASS" else 1


def known_numbers(lab: pathlib.Path, ledger: Ledger) -> list:
    values = []
    for path in sorted((lab / "results").glob("*/metrics.tsv")):
        values += read_metrics(path).values()
    for e in ledger.entries():
        values += [v for v in (e.get("metrics") or {}).values() if isinstance(v, (int, float))]
        values += [e["trials"]] if "trials" in e else []
    texts = [cell for row in journal(lab).values() for cell in row.values()]
    for name in ("PROGRAM.md", "ENVIRONMENT.md"):
        path = lab / "KB" / name
        texts += [path.read_text(encoding="utf-8")] if path.exists() else []
    values += [float(n) for text in texts for n in CELL_NUMBER.findall(text)]
    return values


def traced(token: str, values: list) -> bool:
    percent = token.endswith("%")
    number = abs(float(token.rstrip("%")))
    decimals = len(token.rstrip("%").split(".")[1]) if "." in token else 0
    shapes = [(number, decimals)] + ([(number / 100, decimals + 2)] if percent else [])
    return any(abs(round(abs(v), d) - n) <= 0.5 * 10 ** -d + 1e-12 for n, d in shapes for v in values)


def cmd_brief(a) -> int:
    path = pathlib.Path(a.file)
    values, missing, count = known_numbers(a.lab, Ledger(a.lab)), [], 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for token in BRIEF_NUMBER.findall(line):
            count += 1
            if not traced(token, values):
                missing.append(f"line {n}: {token} is in no metrics file, ledger entry, journal row or program")
    for m in missing:
        print(f"! {m}")
    print(f"{count - len(missing)} of {count} numbers trace to the record")
    return 1 if missing else 0
