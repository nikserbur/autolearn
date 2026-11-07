# Contributing

Thanks for helping research-lab. The most valuable contributions come from real use:

- **Research protocol**: requirements for simulation and verification environments, checks
  `lab.py` can make without judgement, and review steps that caught a real mistake, with the
  evidence that motivated them.
- **Retrieval**: a golden question set from your own notes for `scripts/rag_eval.py`, and
  changes that improve it without hurting the independent set.
- **Languages** for retrieval in `skills/knowledge-search/scripts/ragkit/lang.json`.
- **Router and knowledge-base improvements**: scoring, slugs, budgets, linting.

## Ground rules

1. **Standard library only** for hooks and scripts (Python 3.9+). No network calls.
2. **Every behavior has a test.** `python3 -m unittest discover -s tests` must pass.
3. **Structure stays valid.** `python3 scripts/validate.py` must report 0 errors.
4. **Measure, then claim.** A change to retrieval or to the research prompts states what it was
   measured on, and on data the change was not tuned on.
5. **Prompts are terse and imperative.** Match the tone of the existing agents: what to do,
   what not to do, what to return. No filler.
6. **No secrets, no personal data, no employer-specific content** in examples or fixtures.
   Build fake keys at runtime in tests so the repo never trips a secret scanner.

## Publishing a fork

Run `./scripts/set-owner.sh <github-user> "<Your Name>"` to replace the `YOUR_GITHUB_USER` /
`YOUR_NAME` placeholders in the README, manifests and license, then `./scripts/publish-github.sh
<owner>/research-lab` for the description and topics.

## Releasing

Bump `version` in `.claude-plugin/plugin.json` and `CHANGELOG.md`, tag `vX.Y.Z`.

## Pull requests

Small and focused, with the "why" in the description. The PR template asks for the checks you ran.
