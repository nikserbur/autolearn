# Contributing

Thanks for helping autolearn learn. The most valuable contributions come from real use:

- **Feedback patterns** in `templates/feedback-patterns.json`: better phrasings, fewer false
  positives, new languages. Every language needs one example per signal kind plus a `neutral`
  example; `tests/test_hooks.py` checks that each example triggers its kind and that no neutral
  example triggers anything with all languages loaded.
- **Reviewer archetypes**: specialised reviewer agents that `/retro` produced for you and that
  generalise (numerical stability, migrations, concurrency, time zones, data leakage...). Put them in `agents/` with a narrow checklist and a suggested trigger.
- **Router and knowledge-base improvements**: scoring, slugs, budgets, linting.
- **Pipeline lessons** that are not repo-specific: propose a change to an agent prompt with the
  evidence that motivated it.

## Ground rules

1. **Standard library only** for hooks and scripts (Python 3.9+). No network calls.
2. **Every behavior has a test.** `python3 -m unittest discover -s tests` must pass.
3. **Structure stays valid.** `python3 scripts/validate.py` must report 0 errors.
4. **Nothing self-modifies without approval.** New features that change prompts, skills,
   settings or `CLAUDE.md` must go through a proposal the user approves.
5. **Prompts are terse and imperative.** Match the tone of the existing agents: what to do,
   what not to do, what to return. No filler.
6. **No secrets, no personal data, no employer-specific content** in examples or fixtures.
   Build fake keys at runtime in tests so the repo never trips a secret scanner.

## Publishing a fork

Run `./scripts/set-owner.sh <github-user> "<Your Name>"` to replace the `YOUR_GITHUB_USER` /
`YOUR_NAME` placeholders in the README, manifests and license, then `./scripts/publish-github.sh
<owner>/autolearn` for the description and topics.

## Releasing

Bump `version` in both manifests and `CHANGELOG.md`, tag `vX.Y.Z`.

## Pull requests

Small and focused, with the "why" in the description. The PR template asks for the checks you ran.
