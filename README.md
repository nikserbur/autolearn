<div align="center">

# research-lab

**A research lab inside Claude Code: hypotheses, simulations, an independent reviewer, and a knowledge base that remembers every result.**

Claude runs research the way a careful lab does: it writes down what it expects before every run, tests in a simulation that meets stated requirements, judges the winner once on data it never saw, has a reviewer compare what happened with what was expected, and turns every surprise into the next hypothesis. Everything it learns lands in a local knowledge base that answers later questions in any project.

[![CI](https://github.com/nikserbur/research-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/nikserbur/research-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.4.0-informational)](CHANGELOG.md)
[![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)](#install)

</div>

## Install

```text
/plugin marketplace add nikserbur/research-lab
/plugin install research-lab@research-lab
```

Restart Claude Code. The first session creates the data directory `~/.claude/research-lab/` (a git repo). Prefer plain files in `~/.claude`? Use the [install script](#install-script). Coming from autolearn? See [Upgrading from autolearn](#upgrading-from-autolearn).

```text
/research "train a model that predicts next-minute direction better than the baseline, net of costs"
/research research/nifty-mm rounds 3       # continue an existing research folder
/knowledge-ingest repo                       # give this repo a page in the knowledge base
```

## Two modes, one knowledge base

| | Background mode | Direct mode: `/research` |
|---|---|---|
| What it is for | answering from what is already known | producing new knowledge |
| Who works | the main session, no subagents | the session as controller, plus `rl-source-scout` and `rl-hypothesis-reviewer` |
| Parts | `knowledge-search`, `knowledge-ingest`, three hooks | the research loop, its environments, `lab.py` |
| Writes to | the global knowledge base | `research/<slug>/KB/` in the project; durable facts are promoted to the global base |
| Cost | close to zero, never interrupts | real compute and tokens; starts when you ask for research |

Both read the same index, so a conclusion from last month's research answers today's question in another repo.

```mermaid
flowchart LR
  U[Your message] --> M{Claude matches it<br/>against skill descriptions}
  M -- "research, train a model, test hypotheses,<br/>what is known about X" --> RS[/research: direct mode/]
  M -- "how does X work here, did we try Y,<br/>what did we decide" --> KS[knowledge-search]
  M -- "remember this, add to the knowledge base" --> KI[knowledge-ingest]
  RS -- "conclusions, sources, reviews" --> KB[(knowledge base<br/>+ research KBs)]
  KI --> KB
  KS --> KB
  subgraph Hooks["Hooks: always, no decision involved"]
    H1[SessionStart: brief with pointers]
    H2[PostToolUse Write/Edit: embed KB writes]
    H3[PreToolUse Bash: secret guard]
  end
```

`/research` starts by itself when a request matches its description, and always with `/research` or `/research-lab:research`.

## A research round

```mermaid
flowchart TD
  G[Goal + your hard rules] --> PR[PROGRAM.md]
  PR --> SC[rl-source-scout:<br/>KB, code, papers, docs]
  SC --> ENV[ENVIRONMENT.md:<br/>simulation S1-S7, verification V1-V5,<br/>each proven with evidence]
  ENV --> H[Round table: hypotheses<br/>effect · cost · pass criterion]
  H --> CR{reviewer: critique}
  CR -- cut / change --> H
  CR -- keep --> REG[journal row + expect file:<br/>what each stage should do]
  REG --> RUN[run in the simulation<br/>writes a decision trace]
  RUN --> RV{reviewer: expected against actual,<br/>leakage, costs, recomputed numbers}
  RV -- "every discrepancy<br/>becomes a hypothesis" --> SY[KNOWLEDGE.md:<br/>what we know, what surprised us]
  SY --> H
  SY -- candidate passes --> VF{reviewer: verify<br/>once, on the holdout}
  VF --> BR[BRIEF + durable facts<br/>to the global KB]
  RUN -. check-in, never blocks .-> YOU((you))
  YOU -. steer any time .-> H
```

Everything lands in `research/<slug>/KB/`; the hook embeds it, so the next round, the reviewer and any future project retrieve it.

### Simulation and verification environments

Hypotheses run only in the simulation environment. A frozen candidate is judged once in the verification environment. Both are written down in round 0 in [`ENVIRONMENT.md`](skills/research/ENVIRONMENT-TEMPLATE.md), and each requirement needs evidence before round 1:

| Simulation | Verification |
|---|---|
| S1 point in time: decisions see only earlier data, delays modelled | V1 holdout apart by time or entity, with a gap |
| S2 costs and fills explicit and conservative | V2 candidate frozen: commit, config, data hashes |
| S3 reproducible: same inputs, same files | V3 run by the reviewer, never by the controller |
| S4 isolated: no live orders, no network, read-only data | V4 same environment, only the data differs |
| S5 controls: nothing gives zero, a known case gives its numbers, a shuffled target gives no edge | V5 looks counted; over budget the result is SPOILED |
| S6 a decision trace: what each decision looked at, did, why, and what came of it | |
| S7 units and conventions written down | |

`scripts/lab.py` checks what can be checked without judgement:

```text
lab.py env KB/ENVIRONMENT.md                            # requirements without evidence (exit 1)
lab.py repro --out results/E0/metrics.tsv -- python3 scripts/03_sim.py   # same command twice, same files?
lab.py trace results/E7/trace.tsv --expect KB/expect/E7.md   # look-ahead, time order, blind decisions
lab.py sample results/E7/trace.tsv                       # best, worst, rare and random decisions to read
lab.py holdout KB/HOLDOUT.tsv status                     # looks per candidate; SPOILED over budget
```

### Expected against actual

Before a run, Claude writes `KB/expect/<id>.md`: for each stage (signal, decision, order, fill, position, exit, result; or data, features, prediction, decision, outcome for a model) what the system should look at, what it should do, and where that shows in the trace. After the run, `rl-hypothesis-reviewer`, in a fresh context, walks those stages on concrete decisions and in aggregate, and also looks sideways at what moved without being under test:

| Stage | Expected | Actual | Looked at | Discrepancy -> hypothesis |
|---|---|---|---|---|
| signal | long when imbalance > 0.3, 40-60 a day | 212 a day | imbalance, **next bar's close** | look-ahead in a feature -> rebuild it from closed bars, expect the edge to drop |
| fill | buys at the ask | buys at the mid | book | optimistic fills -> fill at the ask, expect -0.5 bp per trade |
| neighbours | other instruments unchanged | NQ trades doubled | shared risk limit | the limit is shared -> split it per instrument |

<sub>Illustrative. Each discrepancy goes into `KNOWLEDGE.md` and becomes a row in the next round's table, with a mechanism, a cost and a pass criterion.</sub>

### Training a model

The loop follows a fixed protocol whenever an experiment trains or tunes a model: splits by time, never shuffled, with selection on validation only; a trivial and a simple baseline first; one change per experiment and an ablation for what stays; at least three seeds and the spread reported; a learning curve before a bigger model; importance or ablation to see what the model uses; thresholds tuned on validation and frozen with the model. The full list is in [the skill](skills/research/SKILL.md#training-a-model).

### Collecting knowledge and the web

One set of rules for both modes, in [SOURCES.md](skills/knowledge-ingest/SOURCES.md): the knowledge base first, then the project's code and docs, then connected tools, then the web. On the web Claude only reads: no sign-ins, forms or downloads, and no internal names or data in search queries. Papers and official docs come before blogs and forums. Every fact is kept with its source, the day it was read and a status, a number that drives a decision needs two sources or a measurement, and disagreements stay visible. In the direct mode only the scout and the reviewer browse, so the controller's context stays on the experiments.

## Examples: how to ask

Any language works; the skills match on meaning.

```text
# research, explicit
/research "find an options market-making strategy that is net positive after fees on two-week windows" rounds 5
/research "what is known about queue-position models for limit orders? build a knowledge base on it"

# research, in plain words (usually triggers it)
Audit what the previous model proposed, then test hypotheses on per-side markout prediction.
Work in rounds: hypothesis -> test -> review -> conclusions in the KB -> new hypotheses.
No positional short-vol strategies. Two-week windows only, our standard pipeline.

# ask the knowledge base (knowledge-search, RAG): the route is chosen for each question
What did we learn about the CatBoost training window? Did we try E07c?
What breaks if Redis goes down?                                      # walks the entity graph
What integration problems did all teams have in the third quarter?   # every document, what recurs

# store a fact (knowledge-ingest; the hook embeds it right away)
Remember: back-of-queue sells are toxic, about -0.03 per lot.
```

## Knowledge base, retrieval (RAG) and router

Writing is automatic and touches only what changed:

```mermaid
flowchart LR
  W[Claude writes<br/>.md / .tsv] --> CDC[content hashes:<br/>changed chunks only]
  CDC --> PC[sections as parents,<br/>small chunks as children]
  PC --> CX[context for each chunk]
  CX --> EM[embeddings]
  CX --> GR[entities, relations]
  EM --> VS[(knowledge/.rag/)]
  GR --> VS
```

Reading is one command, routed per question:

```mermaid
flowchart TD
  Q[question] --> RT{route}
  RT -- "an id or a specific question" --> H[vectors + BM25 + graph,<br/>fused with RRF, then reranked]
  RT -- "what breaks if X is off" --> GW[graph walk] --> H
  RT -- "problems of all teams" --> GL[every document,<br/>what recurs, clusters]
  H --> CR{CRAG grade}
  GL --> CR
  CR -- CORRECT --> OUT[sections with path:line<br/>within 8 KB]
  CR -- AMBIGUOUS --> FIX[split the question, repair terms,<br/>name what is missing] --> OUT
  CR -- INCORRECT --> WEB[say so: repo docs, tools, web,<br/>then write it into the KB]
  OUT --> VF[verify the draft:<br/>numbers, ids, negation, citations]
```

There is nothing to choose. `kb_rag.py search <question>` works with no packages installed, and `kb_rag.py setup` adds an embedding model for all later runs (about 370 MB of downloads). Each technique decides when it applies:

| Technique | Applies when | With no packages | When more is available |
|---|---|---|---|
| Hybrid search (vectors + BM25, RRF) | every question; an id shifts the weight to BM25 | hashed vectors | multilingual model via `fastembed` or `sentence-transformers` |
| Decoupled chunking | a section is longer than a chunk; shorter ones stay whole. Search sees the small chunk, Claude gets its section | yes | |
| Contextual retrieval | every chunk | title, summary, heading path, the section's lead sentence | a small LLM writes the context (`RESEARCH_LAB_LLM`) |
| Late chunking | the model exposes token vectors and sees at least 256 tokens | off | turns itself on, for example with `BAAI/bge-small-en-v1.5` |
| Rerank | every non-exact question | built-in: coverage, proximity, phrase and literal match | cross-encoder; an English-only one sits out on other languages |
| GraphRAG, local | a relation question about a known entity | relations from patterns | an LLM adds relations |
| GraphRAG, global | a question about the whole base or a period, with 3+ documents | every document gets a voice, recurring terms, entity clusters, month, quarter and year filter. Always graded as a sample, never as complete | |
| Corrective RAG | every result is graded; correction runs only when it is not `CORRECT` | splits compound questions, repairs inflections and typos, names what is missing, shows the closest sections to read before leaving for repo docs and the web | the embedding model vouches for paraphrases |
| Faithfulness (Self-RAG) | `verify` on a draft with numbers or ids, or built on a partial result | numbers with units, ids, names, negation, citations; exit 1 on a failure | |
| Streaming / CDC | every write (hook), every search, `watch`, `events` | re-embeds changed chunks only, journals each change | |

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

That set comes from a fresh session asked to break v0.3.0. It reversed the author's own claims: without packages the release found less than the engine before it, and it answered made-up ids and nonsense with `CORRECT`. v0.3.1 fixes what it found and takes its tests as acceptance tests, but the fixes were tuned on that same set, so it is no longer blind. The retrieval engine is unchanged since v0.3.1. What is left is plain in the table: without a model a third of answerable questions are graded `INCORRECT`, though the closest sections are shown for Claude to read; a question about the whole base finds well under half of the relevant documents, so its answer is always labelled a sample. Run [`scripts/rag_eval.py`](scripts/rag_eval.py) on your own notes before you rely on these. More in [docs/knowledge-router.md](docs/knowledge-router.md).

`knowledge-ingest` never writes a page without a router entry, and `kb_lint.py` fails on any page the router cannot reach. Every page, and every research `KB/` folder, is indexed for retrieval.

```text
~/.claude/research-lab/knowledge/
├── topic-map.md          # the router: "## topic" blocks, up to 8 lines each
├── map/<area>.md         # second level, once the top map passes 25 KB
├── topics/<slug>.md      # distilled notes, every fact with its source
├── projects/<repo>.md    # one page per repository: purpose, layout, commands, conventions, pitfalls
├── .rag/                 # the index: meta.json, vectors.f32, changes.jsonl
├── .stream/              # documents received through `kb_rag.py events`
└── .router-log.tsv       # hits and misses, showing knowledge-ingest what is missing
```

```markdown
## Payments: refunds
keywords: refund, chargeback, reversal, PAY-, stripe webhook
read first: topics/payments.md#refunds · projects/billing-api.md#webhooks
sources: https://wiki.example.com/refunds ✅ · docs/adr/0012-refunds.md ✅
updated: 2026-10-21
```

`kb_route.py refund webhook` returns the matching curated block, the exact line ranges and sizes of the sections to read, and `sed -n` commands that fit an 8 KB budget; anything larger goes to a subagent.

## What's inside

- **Skills (3):** `research` (the direct mode), `knowledge-search` (retrieval, with the RAG engine), `knowledge-ingest` (the only writer of the global base, and the source rules).
- **Agents (2, prefixed `rl-`):** `rl-hypothesis-reviewer` (critique, review with expected against actual, verify), `rl-source-scout` (collects what is known, with sources).
- **Hooks (3):** session brief, knowledge-base auto-indexing, secret guard on commits.
- **Scripts:** `kb_rag.py` (retrieval), `kb_route.py` (curated router), `kb_lint.py` (router linter), `lab.py` (environment checks), plus `scripts/rag_eval.py` and `scripts/validate.py` for this repo.

Every component with its trigger and purpose: [docs/components.md](docs/components.md).

```text
research/<slug>/              in your project, one folder per research
├── KB/                       PROGRAM, ENVIRONMENT, KNOWLEDGE, EXPERIMENTS.tsv, expect/, HOLDOUT.tsv,
│                             sources/, reviews/, BRIEF_<date>.md  (indexed for retrieval)
├── scripts/                  numbered scripts, run from frozen copies
└── results/<id>/             metrics.tsv, trace.tsv, models and predictions

~/.claude/research-lab/       your data, a git repo
├── knowledge/                topic map, topics, projects, the index
├── venv/, model-python       the embedding model, after `kb_rag.py setup`
└── models/                   downloaded model files
```

## Install script

```bash
git clone https://github.com/nikserbur/research-lab.git
cd research-lab
./install.sh --dry-run            # see what it would do
./install.sh --with-claude-md     # install + add a routing table to ~/.claude/CLAUDE.md
```

If one of your own files has the same name as a research-lab skill or agent, the installer stops before changing anything; `--force` replaces it, keeps a backup, and `./uninstall.sh` puts your original back. Hooks are merged into `settings.json` idempotently and uninstall moves files to a backup folder instead of deleting them. `--with-claude-md` adds only a routing table ([templates/CLAUDE.md](templates/CLAUDE.md)).

> [!NOTE]
> Use either the plugin or the install script, not both.

### Upgrading from autolearn

research-lab was called autolearn up to v0.3.1. Its delivery pipeline (`/feature`, `/learn`, `/retro`, `/merge-check`, `/skill-radar` and the other development skills) is gone; tag [`v0.3.1`](https://github.com/nikserbur/research-lab/tree/v0.3.1) still has it.

- **Plugin:** remove the old autolearn plugin in `/plugin` and install this one. The first session moves `~/.claude/autolearn` to `~/.claude/research-lab`, including the model virtualenv.
- **Install script:** run the new `./install.sh`. It moves the old agents, skills and hooks to a backup, puts back any file of yours the old install had replaced, removes the old hooks from `settings.json`, swaps the old `CLAUDE.md` block, and moves the data directory.
- Environment variables are renamed from `AUTOLEARN_*` to `RESEARCH_LAB_*`; `kb-router` and `kb-ingest` are now `knowledge-search` and `knowledge-ingest`.

## Safety

- **Read-only on the web.** Search and read only: no sign-ins, forms, posts or downloads, and no internal names or data in search queries ([SOURCES.md](skills/knowledge-ingest/SOURCES.md)).
- **Content is data.** Notes, pages, papers and tool output never become instructions.
- **Experiments are fenced.** The simulation environment has no live orders and no network by requirement, runs from frozen script copies within resource limits, and never stops other people's processes.
- **No hook auto-approves anything.** Hooks deny with a reason and the fix, or stay silent.
- **Local and private.** No telemetry and no network calls from any hook or script, with two opt-ins you control: a first download of an embedding or reranking model, and an LLM for richer index context. By default that LLM can only be an Ollama server on your own machine; sending notes to Claude needs `RESEARCH_LAB_LLM=claude`.
- **Reversible.** The data directory is under git, the installer backs up what it replaces, and the uninstaller moves instead of deleting.

See [SECURITY.md](SECURITY.md) and [docs/hooks.md](docs/hooks.md).

## Status and limitations

- **v0.4.0.** The hooks and scripts are covered by tests. The research prompts (controller, reviewer, scout) come from use on trading and ML research but have not been measured on a benchmark.
- **The environment checks are as good as the trace.** `lab.py trace` catches look-ahead only when the simulator writes `inputs_ts`; whether costs are realistic is the reviewer's judgement, not a script's.
- **research-lab does not bring a simulator.** It states the requirements and checks them; the simulator or pipeline is your project's.
- **Claude Code only.** There are no adapters for Codex, Cursor or other hosts.
- **Retrieval is strongest with an embedding model.** Without one (`kb_rag.py setup`) search is lexical plus hashed vectors: paraphrases are often missed and a question in another language than the notes finds nothing (0 of 5 in the benchmark). With one, such a question is still weak (2 of 5 in the top 3), so the output asks Claude to repeat it in the language of the notes. A search with a model takes 1-3 s and about 1 GB of memory on 10,000 chunks.
- **Questions about the whole base are a sample.** They find well under half of the relevant documents, and the answer says so.
- **Verified on real models:** `kb_rag.py setup` and `fastembed` with two embedding models and a cross-encoder. Checked only against stand-ins that follow their APIs: `sentence-transformers`, Ollama and `RESEARCH_LAB_LLM=claude`. Real people's notes have not been measured.
- **The graph reads stated relations.** Without an LLM, entities and relations come from patterns in English and Russian ([add a language](skills/knowledge-search/scripts/ragkit/lang.json)), so it follows "X depends on Y", not relations left implied.
- **Verdicts are heuristics.** `CORRECT` means the question's words stand together in the section, not that the section answers it. Claude still reads what it cites.
- **`verify` is a lexical check.** It catches wrong numbers and units, unknown ids and names, bad citations and claims the base does not hold. It misses what a sentence only implies and decisions superseded in another note, and it raises false alarms across languages and on paraphrases.
- **Every indexed folder joins every later search.** `kb_rag.py forget --root DIR` takes one out. Files over 2 MB are skipped.
- **No connectors are bundled.** `kb_rag.py events` takes changes from Confluence, Notion or anything else as JSON lines, but something has to send them.

## FAQ

<details>
<summary><b>Does it need any Python packages?</b></summary>
No. Hooks and scripts use the Python 3.9+ standard library only. Tests run with <code>python3 -m unittest</code>. If <code>fastembed</code> or <code>sentence-transformers</code> is installed, or after <code>kb_rag.py setup</code>, retrieval uses real embeddings and a cross-encoder by itself.
</details>

<details>
<summary><b>Does it work outside trading?</b></summary>
Yes. The examples are from trading and ML because that is where it is used most. For other work, a "decision" is whatever the system acts on (a prediction, a ranking, an answer) and "costs" are what each decision pays (fees, latency, compute, errors); the environment template says so.
</details>

<details>
<summary><b>Will it overwrite my existing agents or CLAUDE.md?</b></summary>
No. If a file of yours has the same name, the installer stops before changing anything; <code>--force</code> replaces it, keeps a backup, and <code>./uninstall.sh</code> puts it back. The CLAUDE.md routing block is opt-in and fenced by markers so the uninstaller removes exactly that block.
</details>

<details>
<summary><b>Where did /feature, /learn and /retro go?</b></summary>
They were removed in v0.4.0 when the project narrowed to research and the knowledge base. Tag <code>v0.3.1</code> has them.
</details>

<details>
<summary><b>Can I keep the data somewhere else?</b></summary>
Hooks and scripts honour <code>RESEARCH_LAB_HOME</code>, but the skills' instructions assume the default <code>~/.claude/research-lab</code>; treat the variable as a testing aid.
</details>

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md); run `python3 scripts/validate.py && python3 -m unittest discover -s tests` before a pull request.

## License

[MIT](LICENSE)

<sub>Topics: claude-code · claude-code-plugin · claude-skills · subagents · research · experiments · backtesting · machine-learning · hypothesis-testing · knowledge-base · rag · graphrag · hybrid-search · agent-memory · context-engineering</sub>
