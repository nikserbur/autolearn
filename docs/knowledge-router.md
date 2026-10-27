# Knowledge base and context router

The context window is the scarcest resource in a long session. autolearn spends it on
pointers, not content:

| Layer | What enters the context | Budget |
|---|---|---|
| `CLAUDE.md` | a routing table: "when X, read/use Y" | keep it short; content lives elsewhere |
| SessionStart brief | counts and paths: rules, repo lessons, inbox, proposals, radar age | < 700 chars |
| `kb-router` | the matching topic blocks and only the listed sections | 8 KB, more goes to a subagent |
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
