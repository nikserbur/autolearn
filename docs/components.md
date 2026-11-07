# Components

Everything research-lab ships. Design notes: [knowledge router](knowledge-router.md), [hooks](hooks.md),
and the [research skill](../skills/research/SKILL.md) itself.

## Skills

| Skill | Mode | Trigger | What it does |
|---|---|---|---|
| `research` | direct | "research", "train a model", "test hypotheses", "what is known about X", or `/research` | Rounds of pre-registered hypotheses in a simulation environment with written requirements; expected behaviour per stage before every run; an independent reviewer compares expected with actual and turns discrepancies into the next hypotheses; one judged look at the holdout per candidate; conclusions in a research KB; non-blocking check-ins |
| `knowledge-search` | background | domain questions, "did we try", "what did we decide" | Retrieves (RAG) from the knowledge base and every research KB, ≤ 8 KB with `path:line`, acts on the verdict (answer, rephrase and fill the gap, or go to the sources), checks drafts with `verify`; the topic map as the curated route; logs hits and misses |
| `knowledge-ingest` | background | "remember this", a miss, the end of a research | Distills sources into chunk-friendly pages **and** registers them in the router, lints, re-indexes, commits. Holds [SOURCES.md](../skills/knowledge-ingest/SOURCES.md), the collection and web rules of both modes |

## Agents

| Agent | Model | Role |
|---|---|---|
| `rl-hypothesis-reviewer` | Opus | Fresh-context skeptic of the research loop. `critique`: attacks a round before compute. `review`: leakage, costs, simulation assumptions, recomputed numbers, expected against actual stage by stage on sampled decisions, sideways findings, causes, sources, ranked next tests. `verify`: one run of a frozen candidate on the holdout, logged, PASS / FAIL / SPOILED |
| `rl-executor` | Sonnet | Runs one registered experiment through `lab.py run` (in the background when long, collected on a second call), checks its trace, reports in a few lines; never fixes, judges or promotes |
| `rl-source-scout` | Sonnet | Collects what is known for the loop: the knowledge base, the project's code and docs, then papers and official docs on the web; every fact with a source, a date and a status, into `KB/sources/` |

## Hooks

| Hook | Event | Purpose |
|---|---|---|
| `session_brief.py` | SessionStart | Injects pointers and counts (< 700 chars), never content; seeds the data directory in plugin mode |
| `kb_autoindex.py` | PostToolUse (Write / Edit) | A write to the knowledge base or any `KB/` folder starts a background re-embedding of the changed files; never blocks |
| `check_secrets.py` | PreToolUse (Bash) | Denies `git commit` when staged lines look like keys or tokens |

## Scripts (deterministic work stays out of the context window)

| Script | Used by | Does |
|---|---|---|
| `skills/knowledge-search/scripts/kb_rag.py` + `ragkit/` | knowledge-search, knowledge-ingest, research, kb_autoindex hook | Self-routing RAG: parent-child chunks, contextual retrieval, embeddings (fastembed / sentence-transformers, or stdlib hashing), late chunking, entity graph, incremental updates with a change journal; search routes each query, fuses vector, BM25 and graph rankings, reranks, grades (CRAG) and logs hit/miss; `verify` checks a draft against the base; `events` and `watch` take outside changes; `forget` drops a folder; `setup` installs a model into a private virtualenv |
| `skills/knowledge-search/scripts/kb_route.py` | knowledge-search | Ranks topic blocks, resolves `file#section` to line ranges with sizes, prints budgeted `sed` commands, logs hit/miss |
| `skills/knowledge-ingest/scripts/kb_lint.py` | knowledge-ingest | Broken pointers, unroutable pages, size budgets |
| `skills/research/scripts/lab.py` + `labkit/` | research, rl-hypothesis-reviewer | The protocol through an append-only, hash-chained ledger: `register`, `run`, `judge`, `check`, `holdout open/close/status` (one budget per research), `brief` (every number traces to the record). Selection: `stats` (deflated Sharpe, block bootstrap, minimum track length, LORD++), `compare`, `best`, `fdr`. Where it stands: `jobs`, `status` (writes `STATUS.md`). Search: `tree`, `next`, `worktree`. Environment checks: `env --stage`, `trace`, `truncate` (look-ahead across a data cut), `sample`, `repro` |
| `scripts/rag_eval.py` | contributors | Runs any engine version against a golden question set: top-1, top-3, MRR, recall, correct refusals |
| `scripts/validate.py` | contributors, CI | Frontmatter, links, script paths, manifests, English-only, everything compiles |
