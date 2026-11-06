# Knowledge base and context router

The context window is the scarcest resource in a long session. autolearn spends it on
pointers, not content:

| Layer | What enters the context | Budget |
|---|---|---|
| `CLAUDE.md` | a routing table: "when X, read/use Y" | keep it short; content lives elsewhere |
| SessionStart brief | counts and paths: rules, repo lessons, inbox, proposals, radar age | < 700 chars |
| `kb-router` | the sections retrieval graded as an answer, or the matching topic blocks | 8 KB, more goes to a subagent |
| Pipeline briefs | paths and facts, never pasted files | `CONTEXT.md` 8 KB, `CODEMAP.md` 10 KB |
| Test output | failures and the summary line | 120 lines |

## Building the base and the router together

`kb-ingest` is the only writer of the knowledge base, and its rule is: **no page without a
router entry, no router entry without a page or a source.**

1. Read the source cheaply (subagent for anything large).
2. Distill into `topics/<slug>.md` or `projects/<repo>.md`; every fact ends with its source and
   a status mark (✅ read, 🔎 search snippet only, ⛔ do not crawl).
3. In the same step, create or extend the topic block in `topic-map.md`: keywords (synonyms,
   your language, module names, issue prefixes), `read first` pointers to exact sections,
   sources, date.
4. `kb_lint.py`: broken pointers and unroutable pages are errors; size budgets are warnings.
5. Commit.

Where pages come from:
- `kb-ingest repo` in any repository (the session brief offers it when a repo has no page),
- `al-docs-scout`'s "New for the topic map" at the end of every `/feature` run,
- any "remember this" in conversation,
- repeated router misses surfaced by `/learn`.

## Retrieval (RAG): one command, every technique

`kb_rag.py` (in `skills/research/scripts/`, engine in `ragkit/`) is the default way to read the
knowledge base. It runs on the standard library and uses a model when it finds one. Nothing is
configured per query: each technique below decides when it applies.

### Indexing

1. **Decoupled chunks.** A *parent* is what the reader gets: a Markdown heading section (long ones
   cut at paragraphs) or one `.tsv` row with its header. *Children* are what the search sees:
   paragraphs, list items, table rows with their header and sentences, up to 480 characters, about
   the 128-token window of MiniLM models. A section that fits stays one child.
2. **Contextual retrieval.** Each child is embedded and indexed with what the cut took away: the
   document's title, a `summary:` line or its first sentence, the heading path and the section's
   lead sentence. With an LLM (`AUTOLEARN_LLM`) a sentence written for that chunk replaces this, in
   the background, at most 60 chunks per run; the result is cached by the chunk's content.
3. **Embeddings.** A multilingual sentence model through `fastembed` or `sentence-transformers`
   (`kb_rag.py setup`, `$AUTOLEARN_EMBED`, `$AUTOLEARN_EMBED_MODEL`), otherwise stdlib signed hashing of stems and
   character trigrams. Changing the backend rebuilds the store and keeps its roots.
4. **Late chunking.** When the model exposes token vectors and sees at least 256 tokens at once,
   the whole document goes through it (in windows overlapping by a quarter if it is longer), and
   each child's vector is the mean of its tokens, combined with its contextual vector. Below 256
   tokens there is little document left to give, so it stays off; the default MiniLM has 128.
   `AUTOLEARN_LATE=off` disables it.
5. **Knowledge graph.** The same chunks also yield entities (names, ids, paths, `code`) and typed
   relations (`depends_on`, `uses`, `causes`, `part_of`, `owned_by`, `replaces`, arrows) in the
   languages of `ragkit/lang.json`; an LLM can add more. They are stored per chunk, so the graph
   is always in step with the vectors, and assembled in memory only for queries that need it.
6. **Change data capture.** Every known root is scanned on each refresh, by size and time and then
   by content hash; only children whose text or context changed are embedded again (a whole document
   under late chunking, since each vector saw all of it). `changes.jsonl` journals each change.
   A single writer holds a lock, a busy one leaves a note for it, and readers keep the last snapshot.

| How a change arrives | Latency |
|---|---|
| Claude writes a file: the `kb_autoindex` hook | seconds, in the background |
| Any `search` | refreshes before ranking |
| `kb_rag.py watch [--interval 5]` | the interval; for edits outside Claude Code |
| `kb_rag.py events` | immediately; for connectors |

`events` reads JSON lines from stdin, so a Confluence or Notion webhook, a scheduled export or an MCP
pull can stream changes without any full reindex. Documents land in `<KB>/.stream/` under a slugged,
hashed name; an id can never leave that folder. Nothing of this kind is bundled: something has to send
the events.

```json
{"op": "upsert", "id": "confluence:42", "title": "SSO runbook", "url": "https://wiki/42", "updated": "2026-11-01", "text": "..."}
{"op": "delete", "id": "confluence:42"}
```

### Searching

| Route | When | What it adds |
|---|---|---|
| `exact` | the query is mostly identifiers | BM25 first. `pg-orders-3`, `INC-2291`, `v0.3.0` and `1.8` are one term each, so a near twin does not answer; the document named after an id comes before those that mention it |
| `local` | a specific question | hybrid search plus reranking |
| `graph` | relation words (word by word, not substrings) and a named entity with stated relations; `between` needs two | walks typed edges into the fusion, up to two hops; "what breaks" follows what stops working along with it, "what does it depend on" the other way, two entities the path between them |
| `global` | the whole base (`across all teams`), or `overall`, `summary` and period questions when no specific entity is named, with 3+ documents | every document with evidence gets a voice, a stated month, quarter or year filters them, and the answer opens with what recurs across documents |

The route, its reason and the verdict open every answer. `--mode` overrides the route.

1. **Fusion.** Reciprocal rank fusion of the child vector, section vector (models only), child BM25, section
   BM25 and (when routed) graph rankings. BM25 searches children together with their contextual prefix, at half weight.
2. **Rerank.** A cross-encoder when one is installed and cached: the background `index` fetches it
   and a search never waits for a download (`$AUTOLEARN_RERANK`, `$AUTOLEARN_RERANK_MODEL`). An
   English-only model sits out on questions and sections in other languages, and it reads the matched chunk: on
   whole sections the same model did worse than no cross-encoder. The built-in reranker always runs and weighs coverage
   near the match, proximity, phrase, literal word form, headings, fusion and cosine. Sections far
   below the best score become pointers.
3. **CRAG.** The best section is graded by the share of the query's information it holds near the match, weighted by
   rarity, so a term the base never saw counts most, and a word found only in a chunk's context counts for less. The
   rarest word must be there for `CORRECT`. An embedding model can vouch for a paraphrase, unless terms the base never
   saw hold half of the query, and never alone: some words of the question have to be there too. Across alphabets a
   close vector is all there is, so the verdict stays `AMBIGUOUS` and asks for the question in the notes' language.

| Verdict | Means | What happens |
|---|---|---|
| `CORRECT` | the question's words stand together in the section | shown under `===`; the log says `hit` |
| `AMBIGUOUS` | partly, or a sample of the whole base, or links without the rest of the question | shown under `===` with what was not found; the log says `partial`; Claude rephrases once and fills the gap from other sources |
| `INCORRECT` | nothing was graded an answer | the two closest sections are shown under `~==`, so that whoever parses the output does not count them; the log says `miss`; Claude reads them before leaving for repo docs, doc tools and the web, then writes the finding into the KB |

   Before that verdict stands, one correction runs inside the base: a compound question is split so
   that the unanswered part can be named, and terms the base never saw are repaired to the closest vocabulary term, which
   covers inflections and typos. The scripts make no web requests themselves: `kb-router` tells Claude to.
4. **Graph evidence.** A stated link answers a question about links, and only that. Whatever else the question asks must
   be in the evidence too, or the verdict is `AMBIGUOUS` and names it. Negated and planned statements (`no longer depends
   on`, `might later use`) do not become edges. Two entities without a stated path are reported as such.
5. **Whole base.** `global` is never `CORRECT`: it shows a sample, and nothing here can prove it complete. It refuses when the
   base never mentions the topic or the stated period instead of showing something else.
6. **Faithfulness (Self-RAG).** `kb_rag.py verify` takes a draft from `--answer`, `--file` or stdin and looks up each claim
   on its own, held to one place in the notes: a line, or two side by side. Numbers with their units (`15 minutes`
   against `15 hours`) and identifiers must be there, names must stand where the rest of the claim is, negation must
   agree, a name the notes turn down (`over MySQL`) does not count, and cited `path:line` pointers must exist. Exit 1 when
   any claim is unsupported or the score is below 0.8. It is lexical: it misses what a sentence only implies, and raises
   false alarms across languages and on paraphrases. Read the evidence it names.

Lines starting with `| ` in the output quote the notes, so that a note cannot pass for a line of the report.

### An embedding model

`kb_rag.py setup` builds a virtualenv in the data directory, installs `fastembed` (system pythons refuse `pip install`
under PEP 668), downloads the model and remembers that python. Every later run moves into it on start, whichever python
called the script, so skills and the hook need no changes. `AUTOLEARN_PYTHON` names another interpreter. Without the
model's python a store built with it is searched by words only and new notes stay out until it runs: `kb_rag.py index`
then exits 1 and says which python to use. A search never downloads a model, and never waits for a rebuild with
one: that runs in the background while the last snapshot answers by words.

### Limits and upkeep

Files over 2 MB are not indexed (`AUTOLEARN_MAX_FILE_MB`), and searches and `status` say so. Notes that are not UTF-8 are
read as a legacy encoding, and `status` lists them. A store that cannot be read is rebuilt. A lock is taken over when its
process is gone or it has been quiet for ten minutes. A folder indexed once stays in every later search until
`kb_rag.py forget --root DIR`. Upgrading adds the index to the data directory's `.gitignore` and stops tracking it.

### Optional LLM

`$AUTOLEARN_LLM` = `auto` (default: an Ollama server on this machine, if one is running with a chat
model) | `ollama[:model]` | `claude[:model]` | `off`. It runs only in `index`, `watch` and `events`, never
in a search. `claude` runs the Claude Code CLI with no tools and no customisations and sends the changed
documents to the model, so it is never picked by itself (`AUTOLEARN_CLAUDE_BIN` names the binary if it is
not on `PATH`). Documents go in fenced as data, and the answers are validated and used only as search
text and graph edges.

### Measured

`scripts/rag_eval.py` runs any engine version against a golden set, on a private copy of the corpus. The first
table is from a fresh session asked to break v0.3.0, with questions frozen before it read the engine. v0.3.1 was then
tuned on those same questions, so its column shows that the found failures are gone, not how it does on notes it has not
seen. Counts of "top-1" under v0.3.1 with no packages show sections graded as answers first, then with the weak
candidates that are shown alongside them, because Claude reads both.

| Independent set: 106 questions on notes the author had not seen | v0.2.3 | v0.3.0 | v0.3.1 |
|---|---:|---:|---:|
| 71 with one answer, no packages: top-1 / top-3 | 44 / 49 | 36 / 41 | 40 / 41, or 49 / 55 counting weak candidates |
| The same, MiniLM: top-1 / top-3 | 45 / 57 | 48 / 55 | 49 / 55, or 50 / 57 counting weak candidates |
| 84 answerable, graded `INCORRECT`: no packages / MiniLM | | 31 / 7 | 29 / 4 |
| `CORRECT` without the answer in the section: no packages / MiniLM | | 1 / 1 | 0 / 0 |
| 22 with no answer in the base, `INCORRECT`: no packages / MiniLM | 2 / 2 | 17 / 12 | 18 / 12 |
| Of those 22, graded `CORRECT`: no packages / MiniLM | | 1 / 1 | 0 / 0 |
| 8 multi-hop, documents found: no packages / MiniLM | 0.75 / 0.85 | 0.48 / 0.73 | 0.83 / 0.88 |
| 5 about the whole base, documents found: no packages / MiniLM | 0.19 / 0.19 | 0.40 / 0.35 | 0.36 / 0.35 |

| 24 questions on this repo's docs at commit `4a32ca7`: top-1 / top-3 / MRR | v0.2.3 | v0.3.1 |
|---|---:|---:|
| No packages | 0.79 / 0.92 / 0.87 | 0.83 / 1.00 / 0.91 |
| `fastembed`, multilingual MiniLM | 0.50 / 0.71 / 0.63 | 0.79 / 1.00 / 0.89 |
| The same plus `ms-marco-MiniLM-L-6-v2` | | 0.88 / 0.92 / 0.92 |

- **The cross-encoder is not a clear win.** On the independent set it scored 49 / 55 against 50 / 57 without it, weak
  candidates counted in both, and it
  raised false refusals from 4 to 7. It helped on this repo's English docs. `AUTOLEARN_RERANK=off` turns it off.
- **Late chunking is not a clear win either.** On `bge-small-en-v1.5` it made no measurable difference on the independent
  set and cost about twice the CPU. With the default MiniLM it stays off.
- **`bge-small-en-v1.5` refuses less** (10 of 22): the noise floor brings its cosines closer to MiniLM's, not level with them.
- **`verify`** on 45 claims written for that check, standard library: it catches 23 of 26 false ones and accepts 14 of 19
  true ones. The misses are a sentence that only implies the opposite; the false alarms are mostly across languages.
- **Russian questions on English docs** stay weak under MiniLM (2 of 5 in the top 3, 3 under v0.2.3). The verdict is capped
  at `AMBIGUOUS` and the output asks Claude to repeat the question in the language of the notes; asked in English, 4 of the
  5 are found.

Verified on real models: `kb_rag.py setup` and `fastembed` with two embedding models and a cross-encoder. Checked against
stand-ins only: `sentence-transformers`, Ollama and `AUTOLEARN_LLM=claude`. The tests run on macOS and Linux; Windows has not
been run. Real people's notes have not been measured: use `scripts/rag_eval.py` on your own.

## Routing

`kb_route.py <keywords>`:
1. scores topic blocks (keywords line x3, heading and body x1) across `topic-map.md` and
   `map/*.md`,
2. prints the top blocks,
3. resolves each `file#section` pointer to a line range and size (GitHub-style heading slugs),
4. prints `sed -n 'a,bp' file` for sections that fit the budget and marks the rest
   "delegate to a subagent",
5. appends `date, keywords, topic, hit|partial|miss` to `.router-log.tsv`.

## Scaling

| Size | Shape |
|---|---|
| up to ~25 KB of map | one `topic-map.md` |
| beyond | `map/<area>.md` per area, one block per area in the top map (two-level routing) |
| topic page > 40 KB | split into subtopic pages, each with its own block |

## Precedence

Code > ADRs / DECISIONS.md > repo docs > knowledge base > wiki > chat. When the knowledge base
disagrees with the repo, the router says so and offers a fix through `kb-ingest`.
