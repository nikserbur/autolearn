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
   (`$AUTOLEARN_EMBED`, `$AUTOLEARN_EMBED_MODEL`), otherwise stdlib signed hashing of stems and
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
| `exact` | the query is mostly identifiers | BM25 first; neither a reranker nor a paraphrase can outvote `E99` |
| `local` | a specific question | hybrid search plus reranking |
| `graph` | relation words and a named entity the graph knows | walks typed edges into the fusion; "what breaks" follows dependents, "what does it depend on" the other way, two entities the path between them |
| `global` | a question about the whole base and 3+ documents | every document with evidence gets a voice (round robin), a stated period filters them, and the answer opens with what recurs across documents and which entity clusters they share |

The route, its reason and the verdict open every answer. `--mode` overrides the route.

1. **Fusion.** Reciprocal rank fusion of the child vector, section vector (models only), child BM25, section
   BM25 and (when routed) graph rankings. BM25 searches children together with their contextual prefix, at half weight; identifiers
   are never cut by the stemmer.
2. **Rerank.** A cross-encoder when one is installed and cached: the background `index` fetches it
   and a search never waits for a download (`$AUTOLEARN_RERANK`, `$AUTOLEARN_RERANK_MODEL`). An
   English-only model sits out on questions and sections in other languages, and it reads the matched chunk: on
   whole sections the same model did worse than no cross-encoder. The built-in reranker always runs and weighs coverage
   near the match, proximity, phrase, literal word form, headings, fusion and cosine. Its weights came
   from a coarse grid on half of the golden questions and were checked on the other half. Sections far
   below the best score become pointers.
3. **CRAG.** The best section is graded by the share of the query's information it holds, weighted by
   rarity, so a term the base never saw counts most. An embedding model can vouch for a paraphrase, unless
   terms the base never saw hold half of the query: near any machine-learning note is not an answer about
   gradient boosting. Across alphabets a close vector is all there is, so the verdict stays `AMBIGUOUS`.

| Verdict | Means | What happens |
|---|---|---|
| `CORRECT` | the base answers | sections are shown; the log says `hit` |
| `AMBIGUOUS` | partly | sections are shown with what was not found; the log says `partial`; Claude rephrases once and fills the gap from other sources |
| `INCORRECT` | it does not | no section is presented as an answer, weak candidates stay as pointers; the log says `miss`; Claude goes to repo docs, doc tools and the web, then writes the finding into the KB |

   Before that verdict stands, one correction runs inside the base: a compound question is split so
   that the unanswered part can be named, and terms the base never saw are repaired to a vocabulary
   term with the same start. The scripts make no web requests themselves: `kb-router` tells Claude to.
4. **Faithfulness (Self-RAG).** `kb_rag.py verify` takes a draft from `--answer`, `--file` or stdin and
   looks up each claim on its own. Numbers and identifiers must appear in the evidence, negation must
   agree, cited `path:line` pointers must exist. Exit 1 when any claim is unsupported or the score is
   below 0.8, with the evidence named. It is lexical: it will not catch a subtle misreading.

### Optional LLM

`$AUTOLEARN_LLM` = `auto` (default: an Ollama server on this machine, if one is running with a chat
model) | `ollama[:model]` | `claude[:model]` | `off`. It runs only in `index`, `watch` and `events`, never
in a search. `claude` runs the Claude Code CLI with no tools and no customisations and sends the changed
documents to the model, so it is never picked by itself (`AUTOLEARN_CLAUDE_BIN` names the binary if it is
not on `PATH`). Documents go in fenced as data, and the answers are validated and used only as search
text and graph edges.

### Measured

`scripts/rag_eval.py` runs any engine version against a golden set, on a private copy of the corpus. These
are the previous engine and this one on `tests/fixtures/rag/golden_repo.tsv` (this repo's docs at commit
`4a32ca7`) and `golden_fixture.tsv` (a corpus written to exercise each technique).

| 24 questions on real docs: top-1 / top-3 / MRR | v0.2.3 | v0.3.0 |
|---|---:|---:|
| Standard library only | 0.79 / 0.92 / 0.87 | 0.83 / 1.00 / 0.91 |
| `fastembed`, multilingual MiniLM (no late chunking: 128-token window) | 0.50 / 0.71 / 0.63 | 0.79 / 1.00 / 0.89 |
| The same plus `ms-marco-MiniLM-L-6-v2` | | 0.88 / 0.96 / 0.92 |
| `fastembed`, `bge-small-en-v1.5`, late chunking off | | 0.88 / 0.96 / 0.91 |
| The same, late chunking on | | 0.88 / 1.00 / 0.93 |

| Other checks | v0.2.3 | v0.3.0 |
|---|---:|---:|
| 9 questions with no answer: refused, standard library | 3 | 9 |
| The same, MiniLM | 3 | 9 |
| The same, `bge-small-en-v1.5` with late chunking | | 7 |
| 11 targeted questions: top-1, standard library | 0.73 | 0.91 |
| The same, MiniLM | 0.91 | 1.00 |
| 3 multi-hop questions: documents found, either backend | 0.72 | 1.00 |
| 2 whole-base questions: documents found, standard library / MiniLM | 0.80 / 0.70 | 1.00 / 1.00 |
| 5 Russian questions on English docs: in the top 3, MiniLM | 3 | 2 |

The last row is the one place this engine is worse: small chunks carry less topic than whole sections when no
word can match. The verdict is capped at `AMBIGUOUS` there and the output asks Claude to repeat the question in
the language of the notes; asked in English, 4 of the 5 are found.

How much a cosine means depends on the model: everyday questions reach 0.25 on this corpus under MiniLM and 0.57
under `bge-small`. So each store measures that noise floor on neutral probes (`lang.json`) and the verdict reads
how far a cosine stands above it, as a share of the room up to 1. With that, MiniLM refuses all 9 and `bge-small`
7, so treat a new model's verdicts with care until it has been measured.

The sets are small, and the weights and thresholds were set looking at them (the reranker's on half of the
real-docs questions, which did no better than the held-out half). They work as a regression floor, which
`tests/test_rag_search.py` enforces, rather than as a general claim. Verified on real models: `fastembed` with
two embedding models and a cross-encoder. Checked against stand-ins that follow their APIs:
`sentence-transformers`, Ollama and `AUTOLEARN_LLM=claude`.

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
