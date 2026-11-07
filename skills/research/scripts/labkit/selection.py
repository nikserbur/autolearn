"""Selection commands of lab.py: stats, compare, best, fdr. They decide what may be called the best
result once many things have been tried. Everything they compute is written to the ledger once per
run, so a statistic cannot be recomputed with friendlier settings.

The input is `results/<id>/daily.tsv` from the run: a header, then one row per day (or any period),
with a `period` column to pair runs on and a value column, `pnl` unless `--column` says otherwise.
"""
from __future__ import annotations

import math
import pathlib
import statistics

from . import stats
from .ledger import Ledger, sha256_file, text_lines
from .protocol import bad_id, current_best, fail, journal, judged_latest, latest_run

MIN_PERIODS = 20
MIN_DSR = 0.95


def read_series(path: pathlib.Path, column: str) -> dict:
    """{period: value} with finite values only; rows without a period are keyed by their position."""
    lines = text_lines(path)
    if not lines:
        return {}
    head = lines[0].split("\t")
    if column not in head:
        raise ValueError(f"no column {column!r} in {path.name} (columns: {', '.join(head)})")
    out = {}
    for n, line in enumerate(lines[1:], 1):
        row = dict(zip(head, line.split("\t")))
        try:
            value = float(row.get(column, ""))
        except ValueError:
            continue
        if math.isfinite(value):
            out[row.get("period") or str(n)] = value
    return out


def run_series(a, ledger: Ledger, id: str):
    """(run, series) for the latest finished run of `id`, or (None, error message)."""
    run, end = latest_run(ledger, id), ledger.last("run_end", id)
    if not run or not end or end["seq"] < run["seq"]:
        return None, f"{id} has no finished `lab.py run`"
    path = a.lab / "results" / id / "daily.tsv"
    if not path.is_file() or sha256_file(path) != end.get("daily_sha"):
        return None, f"results/{id}/daily.tsv is missing or changed after the run"
    try:
        series = read_series(path, a.column)
    except ValueError as e:
        return None, str(e)
    if len(series) < MIN_PERIODS:
        return None, f"results/{id}/daily.tsv has {len(series)} periods; at least {MIN_PERIODS} are needed"
    return run, series


def recorded(ledger: Ledger, kind: str, **match):
    return next((e for e in reversed(ledger.find(kind)) if all(e.get(k) == v for k, v in match.items())), None)


def cmd_stats(a) -> int:
    if bad_id(a):
        return 1
    ledger = Ledger(a.lab)
    run, series = run_series(a, ledger, a.id)
    if run is None:
        return fail(series)
    first = ledger.find("stats")
    if first and first[0].get("alpha") != a.alpha:
        return fail(f"this research tests at alpha {first[0].get('alpha')}, fixed by its first test")
    done = recorded(ledger, "stats", id=a.id, run_seq=run["seq"])
    if done and (done["column"], done["block"]) != (a.column, a.block):
        return fail(f"stats for this run of {a.id} were recorded with --column {done['column']} --block "
                    f"{done['block']:g}; they are not recomputed with other settings")
    if not done:
        values = list(series.values())
        mean, sd, skew, kurt = stats.moments(values)
        sr, trials = stats.sharpe(values), len(ledger.find("run_start"))
        others = [e["sharpe"] for e in ledger.find("stats")] + [sr]
        variance = statistics.pvariance(others) if len(others) >= 2 else 1 / len(values)
        benchmark = stats.expected_max_sharpe(trials, variance)
        p = stats.p_mean_above_zero(values, a.block)
        lo, hi = stats.interval(values, a.block)
        level, found = stats.lord_levels([e["p"] for e in ledger.find("stats")] + [p], a.alpha)[-1]
        row = journal(a.lab).get(a.id, {})
        done = ledger.append(
            "stats", id=a.id, run_seq=run["seq"], round=row.get("round", ""), column=a.column, block=a.block, alpha=a.alpha,
            n=len(values), mean=mean, sd=sd, skew=skew, kurt=kurt, sharpe=sr, trials=trials,
            sharpe_noise_best=benchmark, psr=stats.psr(sr, 0.0, len(values), skew, kurt),
            dsr=stats.psr(sr, benchmark, len(values), skew, kurt), p=p, lo=lo, hi=hi,
            min_periods=stats.min_track_length(sr, trials) if sr > 0 else None,
            lord_level=level, discovery=found)
    need = done["min_periods"]
    print(f"{a.id}: {done['n']} periods, mean {done['mean']:.4g} (95% block-bootstrap interval "
          f"{done['lo']:.4g} to {done['hi']:.4g}), Sharpe {done['sharpe']:.3f} per period")
    print(f"  trials so far {done['trials']}: the best of them by pure noise would show Sharpe "
          f"{done['sharpe_noise_best']:.3f}; deflated Sharpe {done['dsr']:.3f} (probabilistic, against 0: {done['psr']:.3f})")
    print(f"  p(mean > 0) {done['p']:.4f}; LORD++ level {done['lord_level']:.5f}: "
          f"{'a discovery' if done['discovery'] else 'not a discovery'}")
    if need is None or need > done["n"]:
        print(f"  too short: {'no positive Sharpe' if need is None else f'about {math.ceil(need)} periods'} "
              f"needed before this Sharpe beats {done['trials']} trials of noise")
    return 0 if done["discovery"] and done["dsr"] >= MIN_DSR else 1


def cmd_compare(a) -> int:
    for id in (a.new, a.old):
        if bad_id(type("A", (), {"id": id})()):
            return 1
    ledger = Ledger(a.lab)
    (new_run, new), (old_run, old) = run_series(a, ledger, a.new), run_series(a, ledger, a.old)
    if new_run is None or old_run is None:
        return fail(new if new_run is None else old)
    common = [k for k in new if k in old]
    if len(common) < MIN_PERIODS:
        return fail(f"{a.new} and {a.old} share {len(common)} periods; at least {MIN_PERIODS} are needed to pair them")
    diffs = [new[k] - old[k] for k in common]
    lo, hi = stats.interval(diffs, a.block)
    verdict = "BETTER" if lo > 0 else "NOT BETTER"
    ledger.append("compare", id=a.new, old=a.old, run_seq=new_run["seq"], old_run_seq=old_run["seq"],
                  n=len(diffs), mean=statistics.fmean(diffs), lo=lo, hi=hi, verdict=verdict)
    print(f"{a.new} against {a.old} on {len(diffs)} shared periods: mean difference {statistics.fmean(diffs):.4g}, "
          f"95% block-bootstrap interval {lo:.4g} to {hi:.4g}: {verdict}")
    return 0 if verdict == "BETTER" else 1


def cmd_best(a) -> int:
    if bad_id(a):
        return 1
    ledger = Ledger(a.lab)
    run, row = latest_run(ledger, a.id), journal(a.lab).get(a.id, {})
    problems = []
    if not judged_latest(ledger, a.id):
        problems.append("its latest run is not judged PASS")
    if "PASS" not in row.get("review", "").upper():
        problems.append("the reviewer's PASS is not in its review cell")
    st = recorded(ledger, "stats", id=a.id, run_seq=run["seq"]) if run else None
    if not st:
        problems.append("no `lab.py stats` for its latest run")
    else:
        if not st["discovery"]:
            problems.append(f"not a discovery under LORD++ (p {st['p']:.4f} against level {st['lord_level']:.5f})")
        if st["dsr"] < MIN_DSR:
            problems.append(f"deflated Sharpe {st['dsr']:.3f} is below {MIN_DSR} after {st['trials']} trials")
    best = current_best(ledger)
    if best and best["id"] != a.id:
        old_run = latest_run(ledger, best["id"])
        cmp = recorded(ledger, "compare", id=a.id, old=best["id"], run_seq=run["seq"] if run else None,
                       old_run_seq=old_run["seq"] if old_run else None)
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
    tests = ledger.find("stats")
    if not tests:
        print("no `lab.py stats` recorded yet")
        return 0
    rounds = {}
    for e in tests:
        rounds.setdefault(e.get("round", ""), []).append(e)
    within = {}
    for r, group in rounds.items():
        for e, keep in zip(group, stats.benjamini_yekutieli([e["p"] for e in group], a.alpha)):
            within[e["seq"]] = keep
    print("id\tround\tp\tLORD++ level\tdiscovery\tBY in round")
    for e in tests:
        print(f"{e['id']}\t{e.get('round', '')}\t{e['p']:.4f}\t{e['lord_level']:.5f}\t"
              f"{'yes' if e['discovery'] else 'no'}\t{'yes' if within[e['seq']] else 'no'}")
    found = sum(e["discovery"] for e in tests)
    print(f"{len(tests)} tests, {found} discoveries under LORD++ at alpha {a.alpha:g} "
          f"(levels were fixed when each test was made)")
    return 0
