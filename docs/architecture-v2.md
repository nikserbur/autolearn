# research-lab v2: evaluation of v0.4.0 and the proposed architecture

Status: **phases 1-3 implemented in v0.5.0-v0.7.0** (2026-11-07); phases 4 and 5 are proposals.

## How v0.4.0 was evaluated

1. **Acceptance run.** A toy one-day strategy with three planted defects (a momentum feature that
   reads the next bar, buys filled at the mid instead of the ask, an entry before a forbidden
   time) and a research KB claiming +0.442 points per trade. `rl-hypothesis-reviewer`, run blind on
   Sonnet with only its own instructions, found all three, recomputed the numbers exactly, showed
   the honest result is -0.20 per trade (-0.52 on 200 fresh days), noticed that the S1 evidence
   proved nothing and that the S5 controls were skipped, and turned every finding into a ranked
   next test. **The review step works.**
2. **Independent architecture review** in a fresh context, with runs against throwaway data.
3. **Survey** of comparable systems and methodology (sources at the end).

## What is wrong with v0.4.0

Confirmed by running the code:

| # | Severity | Finding |
|---|---|---|
| C1 | critical | The holdout turns into a selection set. Looks are counted per free-text candidate, so a new commit resets the budget; the verify report, with its discrepancies and "next tests", lands in `KB/` and feeds the next hypotheses; the look is logged after the run, so a crash loses it. |
| C2 | critical | The brief's number check checks the brief against itself: it lives in `KB/`, is indexed first, and `verify` passes any number it contains. |
| H1 | high | Pre-registration is on trust: the expected cells, the pass criterion and the expect file can be edited after the run without a trace. |
| H2 | high | No defence against multiple testing: `BEST` is the maximum of every trial on the same validation windows; nothing counts the trials. |
| H3 | high | The look-ahead check passes silently when the simulator writes no `inputs_ts`, or writes `inputs_ts = ts` (what the toy did). |
| H4 | high | `repro` says "same" when the command skipped because its outputs already existed. |
| H5 | high | Expectations, conjectures and search snippets are indexed as equal to measurements: a search for "E7 net result" returned the expect file's prediction first, graded `CORRECT`. Every research KB joins global search forever. |
| H6 | high | No resumability: a `pending` row carries no PID, log or completion marker; long verify runs exceed what the reviewer may run. |
| H7 | high | Four contradictions: critique runs before expect files exist but the reviewer requires them; "the reviewer's PASS" in simulation has no PASS verdict; V1-V5 cannot be proven in round 0 but block round 1; two different verification criteria. |
| M1 | medium | The controller grades its own work: KEEP/BEST before the review, the reviewer reads the controller's conclusions first. |
| M2 | medium | Context and cost grow every round: one ever-growing PROGRAM and KNOWLEDGE read in full by every reviewer call; mandatory web search on every review. |
| M3 | medium | Indexing scales poorly: the model loads before the lock, the store is rewritten on every write. |
| M4 | medium | Checks left to the LLM that a script can do: stage counts, "must not happen", recomputing headline numbers from the trace, journal schema. |

What the survey adds: systems that hold up (autoresearch, AIDE, evo) keep **an evaluator the agent
cannot change, a fixed budget per run, and a flat log of every attempt**; the documented failures
of research agents are fabricated numbers after failed runs (MLR-Bench: about 80% of cases),
tampering with the evaluator (METR, ImpossibleBench), early "done", and greedy local optima.

## Architecture v2

### 1. A ledger instead of trust
`lab.py` becomes the only way to change protocol state. `KB/LEDGER.jsonl` is append-only, each
entry carries the hash of the previous one, and every number in a brief must trace to an entry.

| Command | Does |
|---|---|
| `lab.py register <id>` | hashes the expect file and the pre-run fields; parses a machine-readable pass criterion (`net_per_trade > 0 & trades >= 300`) |
| `lab.py run <id> -- cmd` | freezes the code into `results/<id>/code/`, records the candidate hash (code, config, data), PID, start, end and exit code (`RUN.json`, then `DONE`); clears outputs first |
| `lab.py judge <id>` | applies the registered criterion to `metrics.tsv`; the decision is the script's, not the controller's |
| `lab.py check` | fails on any edit to a registered expect file or criterion, or a broken hash chain |
| `lab.py holdout open/close` | opens a look before the run and seals the result outside `KB/` |
| `lab.py status` | writes `STATUS.md`: requirements proven, rounds, decisions, the best metric over time with its interval, trials N, holdout looks, open discrepancies, running jobs |

### 2. Enforcement by hooks, not prose
A `research_guard.py` PreToolUse hook denies reading the holdout path unless a look is open, and
denies edits to registered expect files and to the ledger. The rule cannot be forgotten in a long
session because it is not in the model's context to forget.

### 3. A holdout that stays a holdout
One look budget per research, not per candidate. The look is opened before the run; the verify
report goes to `sealed/`, outside `KB/` and outside the index, and returns only the verdict and the
number. After a FAIL the holdout is spent: the next confirmation needs a new, later window.

### 4. Statistics that know how many things were tried (standard library)
- Trial count N from the ledger, printed on every result and in the brief.
- Deflated Sharpe ratio and minimum backtest length for every reported backtest
  (`statistics.NormalDist`).
- A new `BEST` must beat the old one outside a day-block bootstrap interval on paired per-day
  results; a block permutation for the shuffled-target control.
- Within a round: Benjamini-Yekutieli; across rounds: LORD++ (online FDR), because hypotheses arrive
  round after round.
- Optional: PBO by CSCV, purged and embargoed walk-forward splits.

### 5. Environments proven by experiment, not by a self-reported column
- S1: `lab.py truncate --at T -- cmd` reruns with the data cut at T and diffs every decision before
  T; a trace without `inputs_ts` is not S1 evidence, and `inputs_ts >= ts - min_delay` is an error.
- S5: the controls are registered experiments like any other.
- `lab.py env --stage sim|verify`: V1-V5 are proven for a candidate, not in round 0.
- Evidence must be an existing file or a ledger entry, not free text.

### 6. Roles with less self-grading
- **Controller**: plans, keeps state, talks to the user. It does not run experiments or decide KEEP.
- **Executor** (new, Sonnet): runs one registered experiment through `lab.py run` and reports.
- **Reviewer**: recomputes from artifacts before reading any conclusion; review mode returns
  PASS/BLOCK; a disputed blocking finding goes to the user.
- **Scout**: unchanged.

### 7. Typed knowledge
Every KB section carries `kind` (measurement, expectation, conjecture, snippet), `status` (valid,
invalid, superseded) and `scope` (data window, cost model, project). Retrieval filters and labels
by them; `expect/` and `sealed/` are not indexed; a research KB is searched only
by its own research until knowledge-ingest promotes facts; an `INVALID` decision marks its
conclusions invalid instead of leaving them next to the new ones (bi-temporal, as in Graphiti).
`kb_lint.py` flags stale numeric facts.

### 8. Context and cost
One file per round; `KNOWLEDGE.md` keeps a capped "current state" and an archive; the reviewer
searches the web only for a flagged question; the session brief names active researches, their
round, running jobs and pending reviews, so a new session resumes from files.

### 9. The harness tests itself
The toy research with planted defects becomes a fixture: the `lab.py` chain must catch what a
script can catch (look-ahead by truncation, the forbidden entry, the mid fill against the
expectation), and a scripted reviewer run is scored on finding the rest.

## Phases

| Phase | Content | Fixes |
|---|---|---|
| 1 (done, v0.5.0) | ledger, `register`/`run`/`judge`/`check`/`holdout open`, one holdout budget, sealed verify, brief checked against the ledger, `repro` and `trace` fixes, the four contradictions, toy fixture | C1, C2, H1, H3, H4, H7, M1 |
| 2 (done, v0.6.0) | trial count, deflated Sharpe, block bootstrap for BEST, BY within a round, LORD++ across rounds | H2 |
| 3 (done, v0.7.0) | typed knowledge, private research KBs, supersession, stale-fact lint, lock-before-model | H5, M3 |
| 4 | executor agent, `status`/`STATUS.md`, resumable jobs, per-round files, brief lists active researches | H6, M2 |
| 5 (optional) | tree search over experiments in parallel worktrees with per-run budgets | greedy local optima |

## Sources
- Karpathy, autoresearch: https://github.com/karpathy/autoresearch
- AIDE: https://arxiv.org/abs/2502.13138 · MLE-bench: https://arxiv.org/abs/2410.07095
- AI Scientist v2: https://arxiv.org/abs/2504.08066 · independent evaluation: https://arxiv.org/abs/2502.14297
- Google AI co-scientist: https://arxiv.org/abs/2502.18864 · Kosmos: https://arxiv.org/abs/2511.02824
- RE-Bench: https://arxiv.org/abs/2411.15114 · METR on reward hacking: https://metr.org/blog/2025-06-05-recent-reward-hacking
- MLR-Bench: https://arxiv.org/abs/2505.19955 · ImpossibleBench: https://arxiv.org/abs/2510.20270
- Anthropic, long-running harnesses: https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- evo: https://github.com/evo-hq/evo
- Bailey et al., Probability of Backtest Overfitting: https://papers.ssrn.com/abstract=2326253
- Bailey and Lopez de Prado, Deflated Sharpe Ratio: https://papers.ssrn.com/abstract=2460551
- Javanmard and Montanari, online FDR (LORD): https://arxiv.org/abs/1502.06197
- Dwork et al., reusable holdout: https://www.science.org/doi/10.1126/science.aaa9375
- Graphiti / Zep temporal knowledge graph: https://arxiv.org/abs/2501.13956
