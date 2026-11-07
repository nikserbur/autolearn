# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Bump `version` in `.claude-plugin/plugin.json`
with every release, or plugin users will not receive the update. Up to 0.3.1 the project was
called autolearn.

## [0.9.0] - 2026-11-07

Phase 5 of the [v2 architecture](docs/architecture-v2.md), the last: a search instead of a hill climb.
An independent review of the first version found three ways it misled or overran; all are closed
below, each with a test.

### Added
- The journal's `parent` column makes the experiments a tree; `register` refuses a parent that was
  never registered and a row whose cells do not match the header. `lab.py tree` draws it.
- `lab.py next`, in the spirit of AIDE and best-first search: first what is owed (never run,
  interrupted, ran without a testable series), then debug a crash while its fix chain is short,
  improve the best lower bounds with at most half the suggestions per branch, leave a node whose
  three tested children did no better (children not run yet do not count), start a new idea when
  there is one idea or the best has not changed in five runs since the last new idea, and the
  trials left; at zero, stop.
- `lab.py settings --run-timeout S --max-trials N`. Every run gets the same time: past it, the run
  and everything it started are stopped (SIGTERM, then SIGKILL to the group; `taskkill /T` on
  Windows), recorded as timed out (exit 124). The trial budget is checked under the ledger's lock,
  so parallel runs cannot both take the last trial; a sweep inside a run can overshoot it once.
- `lab.py worktree add|remove|list`: a git working copy per experiment on its own branch
  (`exp/<research>-<id>`, so commits outlive the copy), hidden through `.git/info/exclude`;
  `--repo` when the research folder lives outside the project's repository.

### Changed
- A run stopped from outside (Ctrl-C, SIGTERM, SIGHUP) stops its command too and is recorded as
  interrupted; a run without a time budget stays in lab.py's process group, so a signal reaches it.
- `rl-executor` reports exit 124 as the time budget and relays a spent budget; the reviewer checks
  the budgets and reads the tree; round files carry a `parent` column and what `next` suggested.

## [0.8.0] - 2026-11-07

Phase 4 of the [v2 architecture](docs/architecture-v2.md): a research survives a crash or a new
session, and the controller's context stops growing with every round. An independent review of the
first version found the run pairing, the Next list, the session brief and the trial counts wrong in
ways a resumed session would trust; all are fixed, each with a test.

### Added
- `lab.py jobs`: every run as running, interrupted, done or crashed. A run is running only while
  its `lab.py` process lives and its heartbeat (touched every 30 s) is fresh, so a reused pid or a
  Windows machine does not keep a dead run alive. A run end names its start, and jobs, status,
  check and the session brief share one pairing.
- `lab.py status` writes `STATUS.md` beside the KB (never indexed): requirements, rounds and
  decisions, trials and discoveries, the best (flagged when it has run again since), holdout looks,
  discrepancies, and a Next list of what is owed: running, interrupted, run but not judged, judged
  PASS but not reviewed, registered but never run, in the journal but not registered.
- The session brief names each active research of the project from its ledger, with sanitised
  ids, and survives a broken ledger.
- `rl-executor` (Sonnet) runs one registered experiment through `lab.py run`: in the foreground when
  short, otherwise in the background with a launch log, collected on a second call; it runs only
  `check`, `run`, `jobs`, `trace` and `stats`.
- `ROUND-TEMPLATE.md`: one file per round (`KB/rounds/R<NN>.md`) with plan, critique and outcome,
  each labelled; the reviewer's critique goes to `reviews/R<NN>_critique.md`, one line per
  hypothesis in its reply.

### Changed
- One trial count for `judge`, `check`, `status` and the deflated Sharpe: every start, interrupted
  ones included, plus a run's own `configs` sweep.
- A dead run left without an end is closed when the next run starts, as an interrupted run with a
  test of p = 1, so it spends the LORD++ budget like any other; a live run of the same id blocks
  another.
- `KNOWLEDGE.md` stays under 30 KB, older sections in `KB/archive/` (`check` warns); `PROGRAM.md`
  no longer holds round tables. The reviewer searches the web only for flagged questions or what
  the KB, the code and the sources leave open.

## [0.7.0] - 2026-11-07

Phase 3 of the [v2 architecture](docs/architecture-v2.md): typed knowledge. An independent review of
0.6.0 found that a search for "E7 net result" returned E7's expectation first, graded CORRECT; a
second review of the first version of this fix found seventeen more ways a prediction or a retracted
result could still pass for a measurement. All are closed, each with a test.

### Added
- Section labels on lines under a heading: `kind: measurement · status: valid · scope: ES Q3 2026 ·
  date: 2026-11-07`. Several lines, markdown decoration (`**Status:**`, `> status:`) and reasons
  (`status: superseded by E9`) are read; kinds and statuses come from a closed list (hypothesis is a
  conjecture, prediction an expectation, retracted is invalid, obsolete is superseded), and anything
  else is `unclear`, never trusted as valid. Subsections and the rest of a long section inherit.
  Research files take a kind from their own folder (reviews, sources, the journal, KNOWLEDGE.md).
- A journal row is read by its columns: no result yet, or a pending decision, makes it an
  expectation; a decision of INVALID or INVALIDATED retires it.
- Search labels each section, leaves invalid and superseded sections out unless `--with-retired`
  (and says how many), and never grades an expectation, a conjecture, a snippet or an unclear
  section CORRECT, on the local and the graph route. `verify` does not accept them as support and
  names the kind of its evidence.
- `--everywhere`: research KBs answer only their own research (`--root`) unless asked; a search
  that does not settle its question says how many research folders it left out.
- `lab.py check` refuses a KNOWLEDGE.md section that rests on an INVALID run unless its label says
  invalid or superseded; `kb_lint.py` warns about numbers read from a source over a year ago.

### Changed
- `expect/` and `sealed/` folders are never indexed; an index older than the labels keeps
  `expect/` out by path while it is rebuilt, and says so.
- A file is in scope by the folder it was found in, so a symlink into the base still answers.
- One indexer loads a model at a time: a second one leaves a note before loading anything, and a
  lock whose file vanished is not mistaken for a busy one.
- Store schema 4: stores rebuild themselves once.

## [0.6.0] - 2026-11-07

Phase 2 of the [v2 architecture](docs/architecture-v2.md): statistics that count what was tried.
An independent check confirmed the formulas against the papers and by simulation, and found four
ways around the gates in the first draft; all are closed below, each with a test.

### Added
- `labkit/stats.py`, standard library only: Sharpe per period with skewness and kurtosis, the
  probabilistic and deflated Sharpe ratio, the periods a deflated Sharpe of 0.95 needs, a stationary
  block bootstrap in one pass with a block length from the data (Politis-White, doubled) and a
  Student-tail p-value, Benjamini-Yekutieli, and LORD++.
- Every `lab.py run` is tested as it ends, under the ledger's lock, on `results/<id>/daily.tsv`:
  the pool of trial Sharpes counts each candidate once with its variance floored at 1/n, trials
  count a run's own `configs` metric, and a run without a usable series is a test with p = 1.
- `lab.py settings` (value column, block, alpha up to 0.1, fixed before the first run), `stats` (the
  record), `compare` (paired on identical periods, recorded once per pair of runs), `review` (the
  reviewer's verdict on a run, in the ledger), `best` (PASS, reviewer PASS, a LORD++ discovery,
  deflated Sharpe of at least 0.95, better than the current best), `fdr`.

### Changed
- Only the current best may be looked at on the holdout. `check` refuses a BEST row the ledger
  does not hold, and KEEP without a reviewer PASS recorded for the latest run.
- `lab.py brief` matches numbers with fewer than two decimals exactly, so a coarse made-up number
  cannot ride on one of the many recorded values.
- `daily.tsv` is part of every experiment's output (`ENVIRONMENT-TEMPLATE.md`, the skill).

## [0.5.0] - 2026-11-07

Phase 1 of the [v2 architecture](docs/architecture-v2.md): the research protocol is enforced by a
ledger instead of promised in prose. Triggered by an independent review of 0.4.0 that found the
holdout reusable per candidate and the brief checked against itself.

### Added
- `research/<slug>/LEDGER.jsonl`, append-only and hash-chained, written only by `lab.py`:
  `register` (fixes the journal row, its machine-readable pass criterion and the expect file),
  `run` (candidate hash, every attempt counted as a trial, `RUN.json`, `DONE`, `run.log`),
  `judge` (the registered criterion decides PASS, FAIL or CRASH), `check` (edits after
  registration, KEEP or BEST without a PASS, unregistered results, unfinished runs, a broken
  chain), `holdout open/close/status`, `brief`.
- `lab.py truncate`: decisions before a cut must be the same with the data cut there, the proof
  of point in time that a self-reported `inputs_ts` cannot give.
- `tests/fixtures/research/toy_sim.py`: a toy strategy with a planted look-ahead; truncation
  catches it and passes the honest version.

### Changed
- One holdout look budget per research, fixed by the first look, opened before the run, only for
  a candidate judged PASS; any failure spends the holdout. The verification report goes to
  `sealed/`, outside the knowledge base, and the controller gets only the verdict and the number.
- The brief is written outside `KB/` and `lab.py brief` traces every number in it to a metrics
  file, the ledger, the journal or the program before it is copied in.
- The research skill: expect files and rows are written before the critique; registration fixes
  them; `KEEP` and `BEST` need the judge's PASS and the reviewer's PASS; knowledge sections are
  written after the review; every result reports the trial count.
- `rl-hypothesis-reviewer` reads the record and recomputes before reading anyone's conclusions,
  ends a review with PASS or BLOCK, and in mode `verify` opens and closes the look itself.
- `lab.py trace` says when a trace is no evidence of point in time (no `inputs_ts`, or
  `inputs_ts` equal to `ts`) and takes `--min-delay`; `repro` moves outputs aside so a file left
  from before cannot pass, and takes `--timeout`; `env --stage sim|verify`; `sample` ignores
  prices when looking for rare decisions.
- S6 asks for every decision in the trace, doing nothing included.

- Hardened after an independent review of the ledger code, each case with a test: appends take a
  lock (parallel runs no longer break the chain); `LEDGER.head` catches an edited or deleted last
  line; unreadable lines and U+2028 in a command no longer crash `check`; a judgement is bound to
  the run it judged, so a later `--again` needs a new judgement before KEEP or a holdout look, and a
  candidate looked at cannot run again; `holdout close` takes only a file under `sealed/` that is
  not a simulation output, and `open` refuses until V1-V5 are proven and caps the budget at 3;
  `metrics.tsv` with several rows, `inf` or `nan` is refused; `run` needs `--code`; ids are
  checked; `brief` also checks whole numbers, reads thousands separators, and no longer takes
  numbers from the journal (seeds and expected effects backed made-up results); `truncate`
  compares stream by stream and fails when it compared nothing, met an unreadable time, or got the
  same file twice; `repro` puts the real outputs back and needs at least two runs; KEEP needs the
  reviewer's PASS in the row's review cell; nanosecond timestamps read on Python 3.9.

### Removed
- `lab.py holdout <file> log` and `KB/HOLDOUT.tsv`: looks are ledger entries now.

## [0.4.0] - 2026-11-07

autolearn is now **research-lab**. The project narrows to research and the knowledge base, in two
modes over one index: the background mode answers from what is known, and the direct mode,
`/research`, produces new knowledge with subagents.

### Added
- Simulation and verification environments for `/research` (`ENVIRONMENT-TEMPLATE.md`):
  requirements S1-S7 (point in time, explicit conservative costs, reproducible, isolated,
  controls, decision trace, units) and V1-V5 (holdout apart, frozen candidate, independent run,
  same environment, counted looks), each proven with evidence before round 1.
- Expected behaviour per stage, written before every run (`EXPECT-TEMPLATE.md`).
- `rl-hypothesis-reviewer` compares expected with actual stage by stage on sampled decisions,
  looks sideways at what moved without being tested, and turns each discrepancy into a
  hypothesis; mode `verify` judges a frozen candidate once on the holdout (PASS, FAIL, SPOILED).
- `rl-source-scout` collects what is known (knowledge base, code, papers, official docs) with a
  source, date and status per fact.
- `skills/research/scripts/lab.py`: `env`, `trace` (look-ahead, time order, blind decisions,
  stages against the expectation), `sample`, `holdout`, `repro`.
- `skills/knowledge-ingest/SOURCES.md`: one set of rules for collecting knowledge and using the
  web in both modes (read-only, no internal data in queries, sources dated and ranked).
- A model-training protocol in the research skill: time splits, baselines, one change per
  experiment, seeds and spread, learning curves, frozen thresholds.

### Changed
- Renamed: plugin, repository, data directory (`~/.claude/research-lab`), hook namespace,
  environment variables (`AUTOLEARN_*` -> `RESEARCH_LAB_*`), agent prefix (`rl-`);
  `kb-router` -> `knowledge-search`, `kb-ingest` -> `knowledge-ingest`.
- The retrieval engine (`kb_rag.py`, `ragkit/`) moved from `research` to `knowledge-search`;
  research calls it from there. The engine itself is unchanged.
- `/research` promotes durable facts through the knowledge-ingest rules instead of writing to
  the global base on its own.
- The session brief carries knowledge-base pointers only.

### Removed
- The delivery pipeline: `/feature` and its ten agents, `/learn`, `/retro`, `/merge-check`,
  `/skill-radar`, `colleague-message`, `pr-description`, `git-handoff`, the feedback capture,
  verify gate, test-output filter and planner guard hooks, `install.sh --deny-push`. Tag
  `v0.3.1` still has them.
- The 24-question golden set on this repo's docs: the docs it asked about are gone.

### Upgrading
- Plugin: remove the autolearn plugin and install research-lab; the first session moves
  `~/.claude/autolearn` (with the model virtualenv) to `~/.claude/research-lab`.
- Install script: the new `install.sh` moves the old agents, skills and hooks to a backup,
  restores files of yours the old install had replaced, removes the old hooks from
  `settings.json`, swaps the old `CLAUDE.md` block and moves the data directory.

## [0.3.1] - 2026-11-06

A fresh session asked to break v0.3.0 found that its numbers did not hold on notes the author had not seen: without
packages it found less than v0.2.3, and it graded made-up ids and nonsense `CORRECT`. Measurements are in the README.

### Fixed
- Graph verdicts were hard-coded: any stated link made `CORRECT`. What else a question asks must now be in the evidence.
- Negated and planned statements (`no longer depends on`, `might later use`) became graph edges, the opposite of the note.
- Ids that do not exist were graded `CORRECT` on their parts. `pg-orders-3`, `KES-2291`, `v0.3.0` and `1.8` are now one term.
- Whole-base answers were graded on one matching word and ignored a stated period with no data. They are now always a
  sample, refuse a topic or period the base does not have, and tell a month from its quarter.
- Routing matched substrings, so ordinary questions left the local route. Signals now match words, and `how do I connect
  to X` is not a relation question. Questions about a period reach whole-base search without `all` or `every`.
- `verify` accepted false claims: it checks one place in the notes rather than a whole section, numbers with their units,
  names, ids, and names the notes turn down.
- `pip install fastembed` failed under PEP 668, and a model in a virtualenv went unused because skills call `python3`.
  `kb_rag.py setup` now installs one privately and every later run moves into its python.
- A file of any size was indexed: one 8 MB export cost every later search 10 s. Files over 2 MB are skipped and reported.
- A note could pass for a line of the report. Each line it contributes now starts with `| `.
- The first search could block on a model download or a full rebuild. Searches never download, rebuilds run in the
  background, and Hugging Face's Xet transfer, which hung behind a VPN, is off by default.
- `events`: a line separator inside a document rejected it, and deep JSON crashed the batch.
- A damaged `meta.json` crashed every command, a dead or hung lock blocked indexing for an hour, and `index` reported
  success when it could not update the store.
- Upgrading left the index tracked in the data directory's git, and rolling back to v0.2 crashed on the new store.
- Notes in a legacy encoding could not be found, questions in Russian on a base full of numbers were told the notes were
  in another language, `# comments` in code fences broke `kb_route.py`, a line break in a query could forge a log line.

### Added
- `INCORRECT` now shows the two closest sections under a `~==` marker, to read before leaving for the web.
- `kb_rag.py forget --root DIR` takes a folder out of later searches, and `status` lists them.
- Lower-case well-known names (`redis`, `kafka`) are entities. Pages about an entity a walk reached come with the answer.
- `tests/test_rag_trust.py`: the independent session's tests, kept as acceptance tests, with the rest of its findings.

## [0.3.0] - 2026-11-04

### Added
- Self-routing retrieval: `kb_rag.py search` picks a route per query (`exact`, `local`, `graph`,
  `global`) from the query's shape and what the base holds; `--mode` overrides. Engine in `ragkit/`.
- Decoupled chunking: search sees small children, the reader gets their parent section.
- Contextual retrieval: each chunk is indexed with its document's title, summary, heading path and
  lead sentence; an optional small LLM (`AUTOLEARN_LLM`) writes a better context in the background.
- Late chunking when the model exposes token vectors and sees 256+ tokens (`AUTOLEARN_LATE`).
- GraphRAG: entities and typed relations per chunk; multi-hop walks with direction, paths between two
  entities, entity clusters; whole-base questions give every document a voice, filter by period
  and open with what recurs across documents. `kb_rag.py graph <entity>` shows what is known.
- Rerank: a built-in reranker always, plus a cross-encoder on the matched chunk when available
  (`AUTOLEARN_RERANK`), never downloaded during a search and kept from languages it cannot read.
- Corrective RAG: every result gets a verdict (`CORRECT`, `AMBIGUOUS`, `INCORRECT`) that also drives the
  `hit|partial|miss` log; compound questions are split and inflected terms repaired; the output names
  what is missing and the next action. `kb-router` follows it, including the fallback to the web.
- `kb_rag.py verify`: checks a drafted answer against the base (numbers, ids, negation, citations).
- CDC: unchanged chunks keep their vectors, every known root is rescanned, `changes.jsonl` journals
  changes; `kb_rag.py events` takes upsert / delete JSON lines from connectors, `kb_rag.py watch`
  follows edits made outside Claude Code.
- A noise floor per model and base, measured on neutral probes, so that verdicts do not depend on one model's cosine
  scale.
- `scripts/rag_eval.py` and golden question sets; floors are enforced in the tests.
- `ragkit/lang.json`: language data of the engine (English, Russian); a new language needs no code.

### Changed
- Refuses instead of guessing: on the golden sets, questions with no answer in the base are
  refused 9 of 9 times, against 3 of 9 before. Weak candidates stay listed as pointers.
- Stores rebuild themselves once on upgrade and keep their research roots.
- New data directories ignore `knowledge/.rag/` and `knowledge/.stream/` in git.

### Fixed
- Sections that moved since indexing are never shown as something else: each section's text is checked against
  a digest. A search waits a moment for a running indexer and says so if it has to use the last snapshot.
- `# comments` in code fences no longer split a section or take over its heading.
- BOMs, invalid UTF-8 (now reported), and line counts past form feeds and U+2028.
- Concurrent indexers could overwrite each other's temporary files, and a reader between the two file
  swaps could pair new vectors with old metadata. There is now one writer at a time, and a checksum
  catches a torn snapshot.
- Identifiers were cut by the stemmer: `AUTOLEARN_PYTHON` matched every `autolearn`, and long numbers
  sharing five digits were one term.
- A folder whose name starts like another root's (`KB`, `KB2`) lost files on re-indexing.
- A root that was not passed again went stale; every known root is now scanned on each refresh.

## [0.2.3] - 2026-11-03

### Fixed
- `kb_rag.py` on Python 3.9-3.11: options after the query words (`search --root DIR words`)
  failed to parse; CI was red on 3.9 since 0.2.0.

## [0.2.2] - 2026-11-03

### Added
- `/feature` research phase (step 1c): when the goal is the best variant, the run first
  finds the winner with the research loop and the planner implements it.
- README: diagrams of what starts by itself, the research loop and the RAG write / read
  path, plus example requests.

## [0.2.1] - 2026-11-03

### Added
- The session brief offers to install an embedding model (`pip install fastembed`) on the first
  run and weekly while RAG runs on the stdlib hashing fallback.

## [0.2.0] - 2026-11-03

### Added
- `/research` skill: a hypothesis-driven research loop (program, round tables, pre-registered
  `EXPERIMENTS.tsv`, `KNOWLEDGE.md`, reviews) with non-blocking check-ins the user can steer.
- `al-hypothesis-reviewer` agent: critiques a round before compute and reviews finished
  experiments (leakage, costs, recomputed numbers, causes, literature, forums, local code, KB).
- `kb_rag.py` (inside the research skill): RAG with embeddings and a vector store
  (`.rag/meta.json` + `vectors.f32`) over the knowledge base and every research `KB/` folder;
  multilingual model via `fastembed` or `sentence-transformers` when installed, stdlib hashing
  otherwise; hybrid cosine + BM25 ranking boosted by the topic map; incremental re-embedding.
- `kb_autoindex` hook (PostToolUse Write / Edit): embeds every KB write in the background, so
  knowledge accumulates without calling a skill.

### Changed
- `kb-router` retrieves chunks first and uses the topic map as the curated route; `kb-ingest`
  writes chunk-friendly sections and re-indexes after every ingest.

## [0.1.0] - 2026-10-27

### Added
- `/feature` multi-agent pipeline: docs-scout, code-scout, planner, plan-critic, implementer,
  test-writer, code-reviewer (review + refutation), doc-writer, merge-gate, lessons-keeper;
  light / deep / two-stage modes; verify gate; learned extra reviewers.
- Learning loop: `capture_feedback` hook, `/learn`, `/retro` with `al-escape-analyst`, promotion
  ladder (test > hook > rule > prompt patch > new reviewer / skill), human-approved proposals.
- Knowledge base built with its router: `kb-router` (`kb_route.py`), `kb-ingest` (`kb_lint.py`),
  topic map with section pointers, two-level maps, router hit/miss log.
- `/skill-radar` with `scan_sessions.py`, `inventory.py` and the `al-skill-scout` agent.
- `colleague-message`, `pr-description`, `git-handoff`, `/merge-check`.
- Hooks: `check_secrets`, `session_brief`, `verify_gate`, `test_output_filter`, `planner_guard`.
- Safe `install.sh` / `uninstall.sh` (stops on name clashes, restores your files, merges its
  manifest across runs, dereferences symlinks in backups); plugin mode seeds the data directory
  on the first session; plugin and marketplace manifests, `scripts/validate.py`, CI.
- All agents are prefixed `al-` to avoid collisions with other setups; plugin-mode hooks act
  only for `autolearn:al-*` agents.
- No hook auto-approves: guards deny with a reason and the exact fix.
- Feedback capture in 15 languages (`templates/feedback-patterns.json`, seeded to the data
  directory), each language tested with positive and neutral examples.
- Docs: [components](docs/components.md) reference and a dated, side-by-side
  [comparison](docs/comparison.md) with related projects.
