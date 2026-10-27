# Hooks

All hooks are standard-library Python, read JSON on stdin, print JSON on stdout, exit 0, make
no network calls, and are covered by `tests/test_hooks.py`.

| Hook | Event | Scope | Effect |
|---|---|---|---|
| `check_secrets.py` | PreToolUse, matcher `Bash` | global | For commands containing `git ... commit`: scans added lines of `git diff --cached` in each target repo (handles `cd X &&` and `git -C X`) for AWS keys, private keys, GitHub / Slack / Anthropic / OpenAI / Google keys and password-like literals; denies the call with the list |
| `capture_feedback.py` | UserPromptSubmit | global | Tags corrections, standing rules, approvals and escaped-bug reports with regexes in 15 languages (English built in; Chinese, Spanish, Portuguese, Russian, Japanese, German, French, Korean, Italian, Turkish, Polish, Ukrainian, Dutch and Vietnamese from `feedback-patterns.json` in the data directory, seeded from `templates/feedback-patterns.json`; `"enabled": [...]` limits the set) and appends a redacted 280-char excerpt to `inbox/feedback.jsonl`. Silent; never blocks; skips slash commands; `AUTOLEARN_CAPTURE=0` disables |
| `session_brief.py` | SessionStart (`startup\|clear\|compact`) | global | Adds a pointers-only brief: active pipeline rules, this repo's lessons and extra reviewers, KB topics, a missing project page, new inbox signals, pending proposals, radar age |
| `verify_gate.py` | Stop (agent frontmatter) / SubagentStop (plugin) | `al-implementer`, `al-test-writer` | Runs the `verify` command from `runs/<repo>/.current-task.json` with `bash -c` (not a login shell, so your venv stays first on PATH); returns `decision: block` with the output tail until it passes; after `max_blocks` blocks it releases the agent silently (a `systemMessage` for you) and records `passed: false`; `STATUS: BLOCKED` / `STATUS: FAILED` stop it at once; task files older than 6 h are ignored |
| `test_output_filter.py` | PreToolUse, matcher `Bash` | `al-implementer`, `al-test-writer` | A test command (pytest, jest, vitest, cargo, go, make test, tox... judged by the command name, not by words in arguments) that is not yet filtered is denied with the exact command to run instead: the original on its own lines inside `{ ... }`, piped through `--filter`, exit code kept with `pipefail`. Deny-with-suggestion works on every Claude Code version and never auto-approves |
| `planner_guard.py` | PreToolUse, matcher `Bash` | `al-planner` | Judges only the command name of each simple command, so reading code is never limited. Denies network, cloud, cluster, database and package-install commands and `python -c` one-liners importing network libraries (extend with `AUTOLEARN_PLANNER_DENY`); code runs need the Bash `timeout` set to <= 60000 ms and are capped at `AUTOLEARN_PLANNER_MAX_RUNS` (default 3) |

## Scoping: install script vs plugin

- **Install script**: the three pipeline hooks are declared in the agents' own frontmatter and
  called with `--scope agent`, so they only ever run for that agent. The frontmatter command is
  guarded with `[ -f ... ] && ... || true`, so a missing script never blocks anything.
- **Plugin**: plugin-provided agents cannot declare hooks, so `hooks/hooks.json` registers the
  same scripts globally; each one reads `agent_type` from the hook input and acts only for
  `autolearn:al-*` agents (agents of other plugins with the same name and the main session are
  ignored). `session_brief.py --seed` creates the data directory on the first session.

## Permissions

Hooks never auto-approve. The test-output filter's suggestion is `<test command> 2>&1 | python3
<hooks>/test_output_filter.py --filter`; `install.sh` adds the allow rule
`Bash(python3 ~/.claude/hooks/autolearn/test_output_filter.py --filter)` (with your absolute path) so
the filter part never prompts; your own allow rules decide the test command itself. In plugin
mode, add the same rule with the plugin's path if subagent test runs keep prompting.

## What the gate executes

The verify command comes from `.current-task.json`, which the /feature controller writes from the
PLAN.md you approved. It runs without a permission prompt, like any hook. Review the verify
commands when you approve a plan.

## Environment variables

| Variable | Default | Used by |
|---|---|---|
| `AUTOLEARN_HOME` | `~/.claude/autolearn` | all hooks and scripts (skills assume the default; use it for testing) |
| `AUTOLEARN_CAPTURE` | `1` | `capture_feedback.py` (`0` disables) |
| `AUTOLEARN_PLANNER_DENY` | empty | `planner_guard.py`: extra regex of denied commands |
| `AUTOLEARN_PLANNER_MAX_RUNS` | `3` | `planner_guard.py` |

## Testing a hook by hand

```bash
echo '{"tool_name":"Bash","agent_type":"al-planner","tool_input":{"command":"curl https://x"}}' \
  | python3 hooks/planner_guard.py
```
