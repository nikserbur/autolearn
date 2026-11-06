# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Bump `version` in `.claude-plugin/plugin.json`
and `.claude-plugin/marketplace.json` together with every release, or plugin users will not
receive the update.

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
