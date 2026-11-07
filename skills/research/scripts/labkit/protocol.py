"""Protocol commands of lab.py: register, run, judge, check, holdout, brief. They are the only way the
research loop changes its state, and every change is a ledger entry. `lab` below is the research
folder (`research/<slug>`), which holds KB/, results/, sealed/ and LEDGER.jsonl.
"""
from __future__ import annotations

import contextlib
import datetime
import json
import os
import pathlib
import re
import shutil
import subprocess
import threading
import time

from . import tables
from .ledger import ID, Ledger, judge, parse_criterion, read_journal, read_metrics, sha256_bytes, sha256_file

PRE_RUN = ("id", "round", "parent", "hypothesis", "change", "expected", "pass_criterion", "commit", "data", "seed")
MAX_LOOKS = 3  # more looks than this and the holdout is a selection set
KNOWLEDGE_CAP = 30_000  # bytes of KNOWLEDGE.md before older sections go to KB/archive/
HEARTBEAT_EVERY, HEARTBEAT_STALE = 30, 120  # seconds: a run whose heartbeat is older than this is not running
# Decimals and percentages anywhere; whole numbers of 10 and more unless part of a date, time, id or version
BRIEF_NUMBER = re.compile(r"(?<![\w.,:/-])[-+]?(?:\d+\.\d+%?|\d+%|\d{2,})(?![\w%]|[.,:/-]\d)")
CELL_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?")
THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


def fail(message: str) -> int:
    print(f"! {message}")
    return 1


def bad_id(a):
    if getattr(a, "id", None) and not ID.match(a.id):
        return fail(f"id {a.id!r}: letters, digits, '.', '_' and '-' only, starting with a letter or digit")
    return 0


def runs(ledger: Ledger) -> list:
    """Every start with its end: the end that names its start (`start_seq`), or for older ledgers the
    latest open start of the same id before it. One pairing for jobs, status, check and the brief."""
    out, by_seq = [], {}
    for e in ledger.entries():
        if not isinstance(e.get("id"), str):
            continue
        if e.get("kind") == "run_start":
            job = {"start": e, "end": None}
            out.append(job)
            by_seq[e.get("seq")] = job
        elif e.get("kind") == "run_end":
            job = by_seq.get(e.get("start_seq")) or next(
                (j for j in reversed(out) if j["start"]["id"] == e["id"] and j["end"] is None), None)
            if job is not None and job["end"] is None:
                job["end"] = e
    return out


def pid_alive(pid) -> bool:
    try:
        pid = int(pid)
    except (TypeError, ValueError, OverflowError):
        return False
    if pid <= 0 or os.name == "nt":  # os.kill(pid, 0) would interrupt the process on Windows: the heartbeat decides
        return pid > 0
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, OverflowError):
        return True
    return True


def heartbeat(lab: pathlib.Path, start: dict) -> pathlib.Path:
    return lab / "results" / start["id"] / f".heartbeat-{start['seq']}"


def live(lab: pathlib.Path, start: dict) -> bool:
    """Running: its lab.py process is alive and its heartbeat is fresh. A reused pid has no heartbeat."""
    try:
        fresh = time.time() - heartbeat(lab, start).stat().st_mtime < HEARTBEAT_STALE
    except OSError:
        fresh = None  # an older run, from before heartbeats: the pid alone decides
    return fresh is not False and pid_alive(start.get("pid")) and (fresh or os.name != "nt")


def trial_count(ledger: Ledger) -> int:
    """Every start is a trial, interrupted ones included, and a sweep inside a run counts as its size."""
    return len(ledger.find("run_start")) + sum(max(0, int(e.get("configs") or 1) - 1) for e in ledger.find("stats"))


def latest_run(ledger: Ledger, id: str):
    return ledger.last("run_start", id)


def current_best(ledger: Ledger):
    return ledger.last("best")


def judged_latest(ledger: Ledger, id: str) -> bool:
    """The last judgement is a PASS of the latest run, not of an earlier attempt."""
    j, run = ledger.last("judge", id), latest_run(ledger, id)
    return bool(j and run and j.get("verdict") == "PASS" and j.get("run_seq") == run["seq"])


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
    if bad_id(a):
        return 1
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
    parent = row.get("parent", "").strip()
    if parent and not ledger.find("register", parent):
        return fail(f"parent {parent!r} of {a.id} is not a registered experiment: a child changes something that ran")
    ledger.append("register", id=a.id, fields={k: row.get(k, "") for k in PRE_RUN},
                  expect_sha=sha256_file(expect_file(a.lab, a.id)), criterion=row["pass_criterion"])
    print(f"registered {a.id}: {row['pass_criterion']}. Its row and expect/{a.id}.md are fixed from now on.")
    return 0


def cmd_run(a) -> int:
    if bad_id(a):
        return 1
    if not a.command or not a.code:
        return fail("usage: lab.py run --lab DIR ID --code F [--code F ...] [--config F] [--data F] -- COMMAND ...; "
                    "--code names the files that make the candidate, so its hash means something")
    ledger = Ledger(a.lab)
    problems = edits(a.lab, ledger, a.id)
    if problems:
        return fail(f"{a.id}: " + "; ".join(problems))
    if ledger.find("holdout_open", a.id):
        return fail(f"{a.id} was looked at on the holdout; it cannot change. A new candidate gets a new id")
    if ledger.find("run_start", a.id) and not a.again:
        return fail(f"{a.id} has run already; --again runs it once more and counts as another trial")
    from . import selection  # every run is a test, recorded as it ends
    settings = selection.research_settings(ledger)
    if settings["max_trials"] and trial_count(ledger) >= settings["max_trials"]:
        return fail(f"the research's budget of {settings['max_trials']} trials is spent: stop, write the brief, "
                    "or start a new research")
    for job in runs(ledger):  # close dead runs first, so they count as tests with p = 1
        if job["end"] is None and not live(a.lab, job["start"]):
            start = job["start"]
            ledger.append("run_end", id=start["id"], exit=-1, seconds=None, interrupted=True, start_seq=start["seq"],
                          metrics_sha="", daily_sha="")
            selection.record_test(a.lab, ledger, start["id"], start, -1)
        elif job["end"] is None and job["start"]["id"] == a.id:
            return fail(f"{a.id} is still running (pid {job['start'].get('pid')}); let it end before running it again")
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
    for name in ("metrics.tsv", "trace.tsv", "daily.tsv"):  # a stale file must not pass for this run's output
        if (out / name).exists():
            (out / name).replace(out / f"{name}.previous")
    if (out / "DONE").exists():
        (out / "DONE").unlink()
    start = time.time()
    entry = ledger.append("run_start", id=a.id, command=a.command, candidate=candidate, hashes=hashes, pid=os.getpid())
    beat, stop = heartbeat(a.lab, entry), threading.Event()
    beat.touch()

    def pulse():
        while not stop.wait(HEARTBEAT_EVERY):
            beat.touch()

    threading.Thread(target=pulse, daemon=True).start()
    info = {"id": a.id, "command": a.command, "candidate": candidate, "pid": os.getpid(),
            "start": datetime.datetime.now().isoformat(timespec="seconds")}
    (out / "RUN.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    with (out / "run.log").open("w", encoding="utf-8") as log:
        try:
            child = subprocess.Popen(a.command, stdout=log, stderr=subprocess.STDOUT,
                                     env=dict(os.environ, RESEARCH_LAB_RUN=a.id), start_new_session=os.name != "nt")
            info["child_pid"] = child.pid
            (out / "RUN.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
            code = wait_within(child, settings["run_timeout"], log)
        except OSError as e:
            log.write(f"lab.py: could not start the command: {e}\n")
            code = 127
    stop.set()
    seconds = round(time.time() - start, 1)
    ledger.append("run_end", id=a.id, exit=code, seconds=seconds, start_seq=entry["seq"],
                  metrics_sha=sha256_file(out / "metrics.tsv"), daily_sha=sha256_file(out / "daily.tsv"))
    with contextlib.suppress(OSError):
        beat.unlink()
    info.update(end=datetime.datetime.now().isoformat(timespec="seconds"), exit=code, seconds=seconds)
    (out / "RUN.json").write_text(json.dumps(info, indent=1), encoding="utf-8")
    (out / "DONE").write_text(f"{code}\n", encoding="utf-8")
    print(f"{a.id}: exit {code} after {seconds} s, candidate {candidate}, log results/{a.id}/run.log")
    for line in selection.describe(selection.record_test(a.lab, ledger, a.id, entry, code)):
        print(line)
    return code


def wait_within(child, limit, log) -> int:
    """The child's exit code; past the research's time budget its whole process group is stopped and the
    run ends with 124, as `timeout` does, so every run gets the same compute."""
    try:
        return child.wait(timeout=limit)
    except subprocess.TimeoutExpired:
        log.write(f"\nlab.py: stopped after the research's budget of {limit} s per run\n")
        log.flush()
        if os.name == "nt":
            child.kill()
        else:
            import signal
            for sig, grace in ((signal.SIGTERM, 10), (signal.SIGKILL, 10)):
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, sig)
                try:
                    child.wait(timeout=grace)
                    break
                except subprocess.TimeoutExpired:
                    continue
        child.wait()
        return 124


def cmd_judge(a) -> int:
    if bad_id(a):
        return 1
    ledger = Ledger(a.lab)
    problems = edits(a.lab, ledger, a.id)
    if problems:
        return fail(f"{a.id}: " + "; ".join(problems))
    end, run = ledger.last("run_end", a.id), latest_run(ledger, a.id)
    if not end or not run or end["seq"] < run["seq"]:
        return fail(f"{a.id} has no finished `lab.py run` (its latest run may still be going)")
    metrics_path = a.lab / "results" / a.id / "metrics.tsv"
    if end["exit"] != 0:
        verdict, lines, metrics = "CRASH", [f"the run exited with code {end['exit']}"], {}
    elif sha256_file(metrics_path) != end["metrics_sha"] or not metrics_path.is_file():
        return fail(f"results/{a.id}/metrics.tsv is missing or changed after the run")
    else:
        clauses = parse_criterion(ledger.last("register", a.id)["criterion"])
        try:
            metrics = read_metrics(metrics_path)
            verdict, lines = judge(clauses, metrics)
        except ValueError as e:
            return fail(f"results/{a.id}/metrics.tsv: {e}")
        except KeyError as e:
            return fail(f"results/{a.id}/metrics.tsv has no finite value for {e}")
        metrics = {name: metrics[name] for name, _, _ in clauses}
    trials = trial_count(ledger)
    ledger.append("judge", id=a.id, verdict=verdict, metrics=metrics, trials=trials,
                  run_seq=run["seq"], candidate=run["candidate"])
    print(f"{a.id}: {verdict}  ({trials} trial{'s' if trials != 1 else ''} so far in this research)")
    for line in lines:
        print(f"  {line}")
    return 0 if verdict == "PASS" else 1


def unfinished(ledger: Ledger) -> list:
    return [job["start"] for job in runs(ledger) if job["end"] is None]


RETIRED = re.compile(r"(?:^|[·;|])[\s>*_`+-]*status[*_`\s]*:[*_`\s]*(invalid|invalidated|retracted|wrong|superseded|obsolete|replaced)\b",
                     re.I | re.M)


def still_valid(lab: pathlib.Path, invalid: list) -> list:
    """KNOWLEDGE.md sections that cite an INVALID experiment and do not say, in the label lines under
    their heading (where search reads them), that they are invalid or superseded. A line that names the
    experiment as invalid is history, not a claim."""
    path = lab / "KB" / "KNOWLEDGE.md"
    if not invalid or not path.exists():
        return []
    out = []
    for section in re.split(r"(?m)^(?=#{1,6} )", path.read_text(encoding="utf-8")):
        lines = section.splitlines()
        if not lines or not lines[0].startswith("#"):
            continue
        head = lines[0].lstrip("#").strip()
        if RETIRED.search("\n".join(lines[1:7])) or head.lower().startswith(("rejected", "discrepancies")):
            continue
        cited = [id for id in invalid if any(re.search(rf"(?<![\w.]){re.escape(id)}(?![\w])", line, re.I)
                                             and not re.search(r"\b(invalid|superseded|retracted)\b", line, re.I)
                                             for line in lines[1:])]
        if cited:
            out.append(f"KNOWLEDGE.md section '{head}' rests on {', '.join(cited)}, which is INVALID: "
                       "mark it `status: invalid` with the reason, or `status: superseded` and point to what replaced it")
    return out


def cmd_check(a) -> int:
    from .selection import reviewed
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
        if "KEEP" in decision or "BEST" in decision:
            if not judged_latest(ledger, id):
                errors.append(f"{id}: marked {row.get('decision')} without a PASS from lab.py judge on its latest run")
            if not reviewed(ledger, id):
                errors.append(f"{id}: marked {row.get('decision')} without a reviewer PASS for its latest run")
        if id not in registered and row.get("result", "").strip():
            errors.append(f"{id}: has a result but was never registered")
    warnings = [f"{e['id']}: started {e['ts']} (pid {e.get('pid')}), no end: running, or crashed" for e in unfinished(ledger)]
    errors += still_valid(a.lab, [id for id, row in rows.items() if "INVALID" in row.get("decision", "").upper()])
    knowledge = a.lab / "KB" / "KNOWLEDGE.md"
    if knowledge.exists() and knowledge.stat().st_size > KNOWLEDGE_CAP:
        warnings.append(f"KNOWLEDGE.md is {knowledge.stat().st_size // 1000} KB: keep the current state there and move "
                        "older sections to KB/archive/KNOWLEDGE-<date>.md (still searched, no longer read in full)")
    if len(best) > 1:
        errors.append(f"more than one row marked BEST: {', '.join(best)}")
    recorded_best = current_best(ledger)
    if best and (not recorded_best or best != [recorded_best["id"]]):
        errors.append(f"row {best[0]} is marked BEST but `lab.py best` "
                      + (f"recorded {recorded_best['id']}" if recorded_best else "recorded none"))
    elif recorded_best and recorded_best["id"] in rows and not best:
        warnings.append(f"{recorded_best['id']} is BEST in the ledger; mark its row BEST")
    judged = [e["verdict"] for e in ledger.find("judge")]
    tests = ledger.find("stats")
    print(f"{len(registered)} registered, {len(ledger.find('run_start'))} runs ({trial_count(ledger)} trials), "
          f"{judged.count('PASS')} PASS / {judged.count('FAIL')} FAIL / {judged.count('CRASH')} CRASH, "
          f"{len(tests)} tested ({sum(e['discovery'] for e in tests)} discoveries), "
          f"best {recorded_best['id'] if recorded_best else '-'}, {len(ledger.find('holdout_open'))} holdout look(s)")
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    return 1 if errors else 0


def cmd_holdout(a) -> int:
    if bad_id(a):
        return 1
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
        if not 1 <= budget <= MAX_LOOKS:
            return fail(f"a look budget of {budget}: 1 to {MAX_LOOKS} looks per research, or the holdout becomes a selection set")
        if not judged_latest(ledger, a.id):
            return fail(f"{a.id}: its latest run was not judged PASS in simulation; only such a candidate is looked at")
        best = current_best(ledger)
        if not best or best["id"] != a.id or best["run_seq"] != latest_run(ledger, a.id)["seq"]:
            return fail(f"{a.id} is not the current BEST (`lab.py best`); only the best candidate is looked at")
        env = a.lab / "KB" / "ENVIRONMENT.md"
        rows = tables.requirements(env, "verify")
        open_ = tables.unproven(rows) if rows else [f"no V requirements in {env}"]
        if open_:
            return fail("verification requirements are not proven: " + "; ".join(open_))
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
    path = pathlib.Path(a.metrics or "")
    sealed = (a.lab / "sealed").resolve()
    if not a.metrics or not path.is_file() or sealed not in path.resolve().parents:
        return fail(f"close needs --metrics FILE written by the holdout run under {a.lab / 'sealed'}")
    digest = sha256_file(path)
    if any(e.get("metrics_sha") == digest for e in ledger.find("run_end")):
        return fail(f"{a.metrics} is the output of a simulation run, not of the holdout")
    if latest_run(ledger, a.id)["candidate"] != opens[-1]["candidate"]:
        return fail(f"{a.id} changed after its look was opened")
    clauses = parse_criterion(ledger.last("register", a.id)["criterion"])
    try:
        metrics = read_metrics(path)
        verdict, lines = judge(clauses, metrics)
    except ValueError as e:
        return fail(f"{a.metrics}: {e}")
    except KeyError as e:
        return fail(f"{a.metrics} has no finite value for {e}")
    ledger.append("holdout_close", id=a.id, verdict=verdict, look=len(opens), metrics_sha=digest,
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
        if e.get("kind") in ("stats", "compare", "judge"):
            values += [v for v in e.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    values += [len(ledger.find("run_start")), len(ledger.find("register")), len(ledger.find("holdout_open"))]
    for name in ("PROGRAM.md", "ENVIRONMENT.md"):  # stated costs, windows and limits; never the expectations
        path = lab / "KB" / name
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        values += [float(n) for n in CELL_NUMBER.findall(THOUSANDS.sub("", text))]
    return values


def traced(token: str, values: list) -> bool:
    percent = token.endswith("%")
    number = abs(float(token.rstrip("%")))
    decimals = len(token.rstrip("%").split(".")[1]) if "." in token else 0
    shapes = [(number, decimals)] + ([(number / 100, decimals + 2)] if percent else [])
    # Rounding only from two decimals on: a coarse number would match one of many recorded values by chance
    return any((abs(round(abs(v), d) - n) <= 0.5 * 10 ** -d + 1e-12) if d >= 2 else abs(abs(v) - n) <= 1e-9
               for n, d in shapes for v in values)


def cmd_brief(a) -> int:
    path = pathlib.Path(a.file)
    values, missing, count = known_numbers(a.lab, Ledger(a.lab)), [], 0
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for token in BRIEF_NUMBER.findall(THOUSANDS.sub("", line)):
            if "." not in token and "%" not in token and 1900 <= int(token.lstrip("+-")) <= 2100:
                continue  # a year
            count += 1
            if not traced(token, values):
                missing.append(f"line {n}: {token} is in no metrics file, ledger entry or program")
    for m in missing:
        print(f"! {m}")
    print(f"{count - len(missing)} of {count} numbers trace to the record")
    return 1 if missing else 0
