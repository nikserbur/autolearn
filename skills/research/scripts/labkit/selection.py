"""Selection: what may be called the best result once many things have been tried.

Every `lab.py run` is a test. When it ends, the run's `results/<id>/daily.tsv` (a header, then one
row per day or period with a `period` column and the value column) is tested at once, under the
ledger's lock: deflated Sharpe against the best of all trials so far, a block-bootstrap p-value and
interval, and the LORD++ level for this place in the sequence of runs. A run without a usable
series is a test with p = 1, so untested runs still spend the error budget. The settings (value
column, block length, alpha) are fixed for the whole research before its first run. Nothing here
can be recomputed with friendlier settings: stats only shows the record, and compare is recorded
once per pair of runs.
"""
from __future__ import annotations

import math
import pathlib
import re
import statistics

from . import stats
from .ledger import Ledger, read_metrics, sha256_file, text_lines
from .protocol import bad_id, current_best, fail, journal, judged_latest, latest_run

MIN_PERIODS = 20
MIN_DSR = 0.95
MAX_ALPHA = 0.1
DEFAULTS = {"column": "pnl", "block": "auto", "alpha": 0.05}
VERDICTS = ("PASS", "BLOCK")


def research_settings(ledger: Ledger) -> dict:
    found = ledger.last("settings")
    return {k: found[k] for k in DEFAULTS} if found else dict(DEFAULTS)


def read_series(path: pathlib.Path, column: str) -> dict:
    """{period: value} in file order. Duplicate periods, blank or non-finite values are errors: a
    series with gaps chosen after the fact could be made to look better."""
    lines = text_lines(path)
    if not lines:
        raise ValueError(f"{path.name} is empty")
    head = lines[0].split("\t")
    if column not in head:
        raise ValueError(f"no column {column!r} in {path.name} (columns: {', '.join(head)})")
    out = {}
    for n, line in enumerate(lines[1:], 2):
        row = dict(zip(head, line.split("\t")))
        key = row.get("period") or str(n - 1)
        try:
            value = float(row.get(column, ""))
        except ValueError:
            raise ValueError(f"{path.name} line {n}: {column} is {row.get(column)!r}, not a number")
        if not math.isfinite(value):
            raise ValueError(f"{path.name} line {n}: {column} is {value}")
        if key in out:
            raise ValueError(f"{path.name} line {n}: period {key} appears twice")
        out[key] = value
    return out


def block_for(values: list, setting) -> float:
    return stats.auto_block(values) if setting == "auto" else min(max(float(setting), 1.0), len(values) / 4)


def configs_of(lab: pathlib.Path, id: str) -> int:
    """Configurations a run tried inside itself (a `configs` metric), so a sweep counts as its size."""
    try:
        value = read_metrics(lab / "results" / id / "metrics.tsv").get("configs", 1)
    except (OSError, ValueError):
        return 1
    return max(1, int(value))


def record_test(lab: pathlib.Path, ledger: Ledger, id: str, run: dict, exit_code: int) -> dict:
    """Test one finished run and append the result; called by `lab.py run` as it ends."""
    path, configs = lab / "results" / id / "daily.tsv", configs_of(lab, id)
    row = journal(lab).get(id, {})

    def compute(led: Ledger) -> dict:
        settings, prior = research_settings(led), led.find("stats")
        trials = sum(e.get("configs", 1) for e in prior) + configs
        fields = {"id": id, "run_seq": run["seq"], "candidate": run["candidate"], "round": row.get("round", ""),
                  "configs": configs, "trials": trials, **{f"setting_{k}": v for k, v in settings.items()}}
        try:
            if exit_code:
                raise ValueError(f"the run exited with code {exit_code}")
            series = read_series(path, settings["column"])
            if len(series) < MIN_PERIODS:
                raise ValueError(f"{len(series)} periods; at least {MIN_PERIODS} are needed")
        except (OSError, ValueError) as e:
            fields.update(p=1.0, sharpe=None, dsr=0.0, note=f"not tested: {e}")
        else:
            values = list(series.values())
            n = len(values)
            mean, sd, skew, kurt = stats.moments(values)
            sr = stats.sharpe(values)
            pool = {e["candidate"]: e["sharpe"] for e in prior if e.get("sharpe") is not None}
            pool[run["candidate"]] = sr
            variance = statistics.variance(pool.values()) if len(pool) >= 2 else 0.0
            benchmark = stats.expected_max_sharpe(trials, max(variance, 1 / n))
            block = block_for(values, settings["block"])
            boot = stats.bootstrap(values, block)
            n95 = stats.min_track_length_95(sr, trials, skew, kurt)
            fields.update(n=n, mean=mean, sd=sd, skew=skew, kurt=kurt, sharpe=sr, pool=len(pool),
                          sharpe_noise_best=benchmark, psr=stats.psr(sr, 0.0, n, skew, kurt),
                          dsr=stats.psr(sr, benchmark, n, skew, kurt), block=block, p=boot["p"], lo=boot["lo"],
                          hi=boot["hi"], periods_for_dsr95=n95 if math.isfinite(n95) else None,
                          daily_sha=sha256_file(path))
        level, found = stats.lord_levels([e["p"] for e in prior] + [fields["p"]], settings["alpha"])[-1]
        fields.update(lord_level=level, discovery=found)
        return fields

    return ledger.append("stats", compute=compute)


def describe(e: dict) -> list:
    if e.get("sharpe") is None:
        return [f"{e['id']}: {e.get('note', 'not tested')}; counts as a test with p = 1 (trial {e['trials']})"]
    lines = [f"{e['id']}: {e['n']} periods, mean {e['mean']:.4g} (95% interval {e['lo']:.4g} to {e['hi']:.4g}, "
             f"block {e['block']:.1f}), Sharpe {e['sharpe']:.3f} per period",
             f"  {e['trials']} trials so far: noise alone would show a best Sharpe of {e['sharpe_noise_best']:.3f}; "
             f"deflated Sharpe {e['dsr']:.3f} (against 0: {e['psr']:.3f})",
             f"  p(mean > 0) {e['p']:.2e}; LORD++ level {e['lord_level']:.2e}: "
             f"{'a discovery' if e['discovery'] else 'not a discovery'}"]
    if e["dsr"] < MIN_DSR:
        need = e.get("periods_for_dsr95")
        lines.append(f"  deflated Sharpe below {MIN_DSR}: " + (f"about {math.ceil(need)} periods would be needed "
                     f"at this Sharpe" if need else "no positive Sharpe to build on"))
    return lines


def cmd_settings(a) -> int:
    ledger = Ledger(a.lab)
    current = research_settings(ledger)
    if a.column is None and a.block is None and a.alpha is None:
        print("  ".join(f"{k} {v}" for k, v in current.items()) + ("" if ledger.last("settings") else "  (defaults)"))
        return 0
    if ledger.find("stats"):
        return fail("the settings are fixed: this research has tested runs already")
    new = {"column": a.column or current["column"], "block": a.block or current["block"],
           "alpha": a.alpha if a.alpha is not None else current["alpha"]}
    if not re.fullmatch(r"[A-Za-z_][\w.]*", new["column"]):
        return fail(f"column {new['column']!r}: a plain column name")
    if new["block"] != "auto":
        try:
            if not 1 <= float(new["block"]) <= 250:
                raise ValueError
        except ValueError:
            return fail("block: 'auto' or a number of periods from 1 to 250")
    if not 0 < new["alpha"] <= MAX_ALPHA:
        return fail(f"alpha must be above 0 and at most {MAX_ALPHA}")
    ledger.append("settings", **new)
    print("  ".join(f"{k} {v}" for k, v in new.items()))
    return 0


def tested(ledger: Ledger, id: str):
    run = latest_run(ledger, id)
    if not run:
        return None, None
    return run, next((e for e in reversed(ledger.find("stats", id)) if e["run_seq"] == run["seq"]), None)


def cmd_stats(a) -> int:
    if bad_id(a):
        return 1
    run, e = tested(Ledger(a.lab), a.id)
    if not e:
        return fail(f"{a.id} has no tested run: every `lab.py run` is tested as it ends")
    for line in describe(e):
        print(line)
    return 0 if e.get("discovery") and e.get("dsr", 0) >= MIN_DSR else 1


def cmd_compare(a) -> int:
    for id in (a.new, a.old):
        if bad_id(type("A", (), {"id": id})()):
            return 1
    ledger = Ledger(a.lab)
    (new_run, new_test), (old_run, old_test) = tested(ledger, a.new), tested(ledger, a.old)
    for id, t in ((a.new, new_test), (a.old, old_test)):
        if not t or t.get("sharpe") is None:
            return fail(f"{id} has no tested series on its latest run")
    done = next((e for e in ledger.find("compare", a.new)
                 if e["old"] == a.old and e["run_seq"] == new_run["seq"] and e["old_run_seq"] == old_run["seq"]), None)
    if not done:
        settings = research_settings(ledger)
        try:
            new = read_series(a.lab / "results" / a.new / "daily.tsv", settings["column"])
            old = read_series(a.lab / "results" / a.old / "daily.tsv", settings["column"])
        except (OSError, ValueError) as e:
            return fail(str(e))
        if sha256_file(a.lab / "results" / a.new / "daily.tsv") != new_test["daily_sha"] or \
                sha256_file(a.lab / "results" / a.old / "daily.tsv") != old_test["daily_sha"]:
            return fail("a daily.tsv changed after its run was tested")
        if set(new) != set(old):
            return fail(f"{a.new} and {a.old} cover different periods ({len(set(new) - set(old))} only in {a.new}, "
                        f"{len(set(old) - set(new))} only in {a.old}): compare them on the same days")
        diffs = [new[k] - old[k] for k in new]
        boot = stats.bootstrap(diffs, block_for(diffs, settings["block"]))
        done = ledger.append("compare", id=a.new, old=a.old, run_seq=new_run["seq"], old_run_seq=old_run["seq"],
                             n=len(diffs), mean=statistics.fmean(diffs), lo=boot["lo"], hi=boot["hi"],
                             verdict="BETTER" if boot["lo"] > 0 else "NOT BETTER")
    print(f"{a.new} against {a.old} on {done['n']} shared periods: mean difference {done['mean']:.4g}, "
          f"95% interval {done['lo']:.4g} to {done['hi']:.4g}: {done['verdict']}")
    return 0 if done["verdict"] == "BETTER" else 1


def cmd_review(a) -> int:
    if bad_id(a):
        return 1
    ledger = Ledger(a.lab)
    run = latest_run(ledger, a.id)
    if not run:
        return fail(f"{a.id} has not run")
    ledger.append("review", id=a.id, run_seq=run["seq"], verdict=a.verdict, round=a.round, by=a.by)
    print(f"{a.id}: review {a.round} {a.verdict} recorded for its run {run['seq']}")
    return 0


def reviewed(ledger: Ledger, id: str) -> bool:
    run, review = latest_run(ledger, id), ledger.last("review", id)
    return bool(run and review and review["run_seq"] == run["seq"] and review["verdict"] == "PASS")


def cmd_best(a) -> int:
    if bad_id(a):
        return 1
    ledger = Ledger(a.lab)
    run, test = tested(ledger, a.id)
    problems = []
    if not judged_latest(ledger, a.id):
        problems.append("its latest run is not judged PASS")
    if not reviewed(ledger, a.id):
        problems.append("no reviewer PASS for its latest run (`lab.py review`)")
    if not test or test.get("sharpe") is None:
        problems.append("its latest run has no tested series")
    else:
        if not test["discovery"]:
            problems.append(f"not a discovery under LORD++ (p {test['p']:.2e} against level {test['lord_level']:.2e})")
        if test["dsr"] < MIN_DSR:
            problems.append(f"deflated Sharpe {test['dsr']:.3f} is below {MIN_DSR} after {test['trials']} trials")
    best = current_best(ledger)
    if best and best["id"] != a.id:
        old_run = latest_run(ledger, best["id"])
        cmp = next((e for e in ledger.find("compare", a.id) if e["old"] == best["id"] and run and old_run
                    and e["run_seq"] == run["seq"] and e["old_run_seq"] == old_run["seq"]), None)
        if not cmp:
            problems.append(f"not compared with the current best {best['id']}: lab.py compare {a.id} {best['id']}")
        elif cmp["verdict"] != "BETTER":
            problems.append(f"not better than {best['id']} outside the interval ({cmp['lo']:.4g} to {cmp['hi']:.4g})")
    if problems:
        return fail(f"{a.id} cannot be BEST: " + "; ".join(problems))
    ledger.append("best", id=a.id, run_seq=run["seq"], previous=best["id"] if best else "")
    print(f"{a.id} is BEST" + (f" (was {best['id']})" if best else "") + ". Mark its row BEST and the old one KEEP.")
    return 0


def cmd_fdr(a) -> int:
    ledger = Ledger(a.lab)
    tests, alpha = ledger.find("stats"), research_settings(ledger)["alpha"]
    if not tests:
        print("no tested runs yet")
        return 0
    rounds = {}
    for e in tests:
        rounds.setdefault(e.get("round", ""), []).append(e)
    within = {}
    for group in rounds.values():
        for e, keep in zip(group, stats.benjamini_yekutieli([e["p"] for e in group], alpha)):
            within[e["seq"]] = keep
    print("id\tround\tp\tLORD++ level\tdiscovery\tBY in round")
    for e in tests:
        print(f"{e['id']}\t{e.get('round', '')}\t{e['p']:.2e}\t{e['lord_level']:.2e}\t"
              f"{'yes' if e['discovery'] else 'no'}\t{'yes' if within[e['seq']] else 'no'}")
    print(f"{len(tests)} tests (one per run), {sum(e['discovery'] for e in tests)} discoveries under LORD++ "
          f"at alpha {alpha:g}; each level was fixed when its run ended")
    return 0
