# How autolearn compares

As of 2026-10-27, from each project's public README. Projects move fast; corrections are welcome
via a pull request. "Not documented" means we did not find it in the project's docs, not that it
cannot be done.

The multi-agent plan -> implement -> review pipeline is common ground: obra/superpowers,
EveryInc/compound-engineering-plugin and affaan-m/ECC all ship one, and they are more mature than
autolearn. What autolearn adds is a loop that grows guards from verified misses.

| | autolearn | [ECC](https://github.com/affaan-m/ECC) continuous learning v2 | [claude-reflect](https://github.com/BayramAnnakov/claude-reflect) | [compound-engineering](https://github.com/EveryInc/compound-engineering-plugin) | [claude-mem](https://github.com/thedotmack/claude-mem) |
|---|---|---|---|---|---|
| Learns from | escaped bugs, merge-gate findings, refuted / confirmed reviews, gate blocks, your corrections | observed prompts and tool calls | your corrections | the "compound" step after each piece of work | captured session activity |
| Turns it into | evidence-backed lessons -> regression tests, hooks, rules, prompt patches, new reviewer agents with diff triggers, new skills | confidence-scored instincts; `/evolve` turns them into skills, commands or agents | CLAUDE.md entries | documented learnings for the next cycle | compressed, searchable memories |
| Who decides | you approve every behavior change | observation is automatic; evolution is user-invoked | you approve | you | automatic capture |
| Root-causes which stage missed a bug | yes (`/retro` stage analysis) | not documented | not documented | not documented | not documented |
| Context strategy | topic-map router, 8 KB budget, pointers-only session brief | top instincts injected at session start | CLAUDE.md | skills on demand | semantic search, context injection |
| Semantic search over history | no (keyword router) | not documented | no | not documented | yes |
| Hosts | Claude Code only | Claude Code, Codex, Cursor, OpenCode and more | Claude Code | Claude Code, Codex, Cursor and more | Claude Code and other agents |
| Maturity | v0.1.0, new | large user base | small | established | large user base |

Use them together: autolearn's lessons and reviewers sit fine next to a memory plugin or a
skills library.
