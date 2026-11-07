# Environments: <research name>

Written in round 0, before any hypothesis. Hypotheses run in the **simulation environment**; a
frozen candidate is judged once in the **verification environment**. Status is ✅ (with evidence),
❌, or n/a (with the reason in Evidence). `lab.py env` lists what is still unproven; a ❌ blocks
round 1 unless the user accepts the risk in PROGRAM.md (quoted, dated).

Outside trading, read "decision" as the unit the system acts on (a prediction, a ranking, an
answer) and "costs" as what each decision pays (fees, latency, compute, errors).

## Simulation environment
- Command: `<exact command, run from a frozen copy of the script>`
- Data: `<files or tables>`, sha256 `<...>`; code: commit `<...>`; seeds: `<...>`
- Output per experiment: `results/<id>/metrics.tsv`, `results/<id>/trace.tsv` (one row per decision)
- Limits: `<cores, memory, hours>`

| # | Requirement | Status | Evidence |
|---|---|---|---|
| S1 | Point in time: every decision uses only data stamped before it; the delay between data and decision is modelled | | |
| S2 | Costs and fills are explicit and conservative: fees, taxes, spread, slippage, partial and rejected fills | | |
| S3 | Reproducible: the same command, data, commit and seed give the same files (`lab.py repro`) | | |
| S4 | Isolated: no live orders or accounts, no network, read-only source data, limits respected | | |
| S5 | Controls hold: doing nothing gives zero, a known case gives its known numbers, a shuffled target gives no edge | | |
| S6 | Decision trace: ts, stage, inputs looked at (with inputs_ts), action, reason, outcome (`lab.py trace`) | | |
| S7 | Units and conventions written down: price and size units, time zone, sign of returns, fill side | | |

## Verification environment
- Holdout: `<dates or ids>`, sha256 `<...>`. Never used for training, selection, tuning or plots.
- Runs it: `rl-hypothesis-reviewer`, mode `verify`, fresh context, on a frozen candidate.
- Look budget: `<1>` per candidate; every look is a row in `KB/HOLDOUT.tsv` (`lab.py holdout`).
- Pass criterion: `<fixed now; never moved>`

| # | Requirement | Status | Evidence |
|---|---|---|---|
| V1 | Holdout is apart from all data the loop has seen, by time or entity, with a gap where targets overlap | | |
| V2 | The candidate is frozen before the look: commit, config and data hashes in EXPERIMENTS.tsv | | |
| V3 | Independent: the reviewer runs it, the controller does not; nothing in the candidate changes after a look | | |
| V4 | Same environment as simulation (S1-S7 hold); only the data differs | | |
| V5 | Looks are counted; over the budget the result is SPOILED, not PASS | | |
