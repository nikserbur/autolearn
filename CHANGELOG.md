# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). Bump `version` in `.claude-plugin/plugin.json`
and `.claude-plugin/marketplace.json` together with every release, or plugin users will not
receive the update.

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
