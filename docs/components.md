# Components

Everything autolearn ships, with the model each agent runs on and the event each hook listens to.
Design notes: [pipeline](pipeline.md), [learning loop](learning-loop.md), [knowledge router](knowledge-router.md), [hooks](hooks.md).

## Agents

| Agent | Model | Role |
|---|---|---|
| `al-docs-scout` | Sonnet | Routes the task through the topic map, repo docs and doc MCPs; writes `CONTEXT.md` (≤ 8 KB) with the applicable checklist items |
| `al-code-scout` | Sonnet | Maps entry points, signatures, tests and verify commands into `CODEMAP.md` (≤ 10 KB) so the planner does not burn turns searching |
| `al-planner` | Opus (deep: strongest available, auto-fallback) | Writes `PLAN.md`: tasks with interfaces, boundaries, a red test and a verify command each; network-guarded, experiment-budgeted |
| `al-plan-critic` | Opus | Fresh-context skeptic: coverage, truthfulness (grep, not faith), lessons not repeated, every checklist item closed |
| `al-implementer` | Sonnet | One task: red test → code → green run; a Stop hook will not let it finish until `verify` passes |
| `al-test-writer` | Sonnet | Fills uncovered Review Focus items; mutation-checks every new test |
| `al-code-reviewer` | Opus | Mode A: high-signal review of the diff. Mode B: tries to *refute* another reviewer's finding |
| `al-doc-writer` | Sonnet | Docs for what is really in the diff, nothing invented |
| `al-merge-gate` | Opus, max effort | "Can this be merged?" with *no* plan or session context, like a tech lead seeing it cold |
| `al-lessons-keeper` | Opus | Turns run artifacts and captured feedback into evidence-backed lessons; drafts proposals; keeps stats |
| `al-escape-analyst` | Opus | Root-causes an escaped bug to a pipeline stage and drafts the guard, up to a full new reviewer agent |
| `al-skill-scout` | Sonnet | Scouts skills, plugins and MCP servers for a gap; reads and trust-grades every candidate; installs nothing |

## Skills

| Skill | Trigger | What it does |
|---|---|---|
| `/feature` | manual | The multi-agent delivery pipeline (light / deep / two-stage) |
| `/merge-check` | "can we merge?" | Fresh-context pre-review; findings feed `/learn` |
| `/learn` | manual or prompted | Inbox → lessons → proposals → you approve → applied |
| `/retro` | "a bug slipped through" | Escaped-defect analysis → regression test, hook, rule, or a new reviewer |
| `/skill-radar` | manual, monthly | Inventory + session mining + ecosystem scouting → install / build / skip |
| `kb-router` | auto, on domain questions | Greps the topic map, loads ≤ 8 KB of the right sections, logs hits and misses |
| `kb-ingest` | "remember this", router miss | Distills sources into pages **and** registers them in the router, lints, commits |
| `colleague-message` | "draft a message to..." | Drafts in your voice; learns from your edits and approvals; never sends |
| `pr-description` | "describe the PR" | What / why / verification / known limitations; closing keyword only in the last PR |
| `git-handoff` | team repos | Read-only git; prints the exact commands for you to run |

## Hooks

| Hook | Event | Purpose |
|---|---|---|
| `check_secrets.py` | PreToolUse (Bash) | Denies `git commit` when staged lines look like keys or tokens |
| `capture_feedback.py` | UserPromptSubmit | Regex-tags corrections, rules, approvals and escaped bugs in 15 languages into the inbox; silent, local, secrets redacted |
| `session_brief.py` | SessionStart | Injects pointers and counts (< 700 chars), never content: rules, repo lessons, inbox, proposals, radar age |
| `verify_gate.py` | Stop / SubagentStop | Blocks `al-implementer` / `al-test-writer` from finishing until the task's verify command passes; releases them as FAILED after the budget; ignores stale task files |
| `test_output_filter.py` | PreToolUse (Bash) | Sends subagents' test commands back with the exact filtered form to run, so they see failures and the summary, not 5,000 dots |
| `planner_guard.py` | PreToolUse (Bash) | Planner: no network / cloud / prod data / installs; at most 3 experiments, each with a 60 s timeout; reading code is never limited |

## Helper scripts (deterministic work stays out of the context window)

| Script | Used by | Does |
|---|---|---|
| `skills/kb-router/scripts/kb_route.py` | kb-router | Ranks topic blocks, resolves `file#section` to line ranges with sizes, prints budgeted `sed` commands, logs hit/miss |
| `skills/kb-ingest/scripts/kb_lint.py` | kb-ingest | Broken pointers, unroutable pages, size budgets |
| `skills/learn/scripts/lessons.py` | learn, feature, merge-check | Inbox, cursor, proposals, extra-reviewer matching against a patch, run stats with tuning hints |
| `skills/skill-radar/scripts/scan_sessions.py` | skill-radar | Mines transcripts: repeated commands, failing/denied tools, skills used, repeated requests |
| `skills/skill-radar/scripts/inventory.py` | skill-radar | Installed skills, agents, plugins and MCP server *names* (never their config) |
