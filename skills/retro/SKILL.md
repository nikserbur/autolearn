---
name: retro
description: >
  Escaped-defect retrospective: a bug got past planning, review or CI. Finds the first pipeline stage that had the information to catch it, and turns the miss into the cheapest durable guard - a regression test, a hook, a rule, a reviewer prompt patch, or a new specialised reviewer agent that /feature will run automatically on matching diffs from then on. Use when the user says a bug slipped through, "you missed this", "the reviewer didn't catch it", "bug in prod", or when /learn reports an escaped_bug signal.
argument-hint: "<what escaped> [fix commit or PR] [ISSUE-KEY]"
---

# /retro - missed a bug? grow a reviewer

Input: `$ARGUMENTS`. If the symptom, the repo or the fix is unclear, ask once (AskUserQuestion,
up to three questions) and go.

1. **Analyse (delegate).** Agent -> `al-escape-analyst`. Brief: the bug description, the fix commit
   or diff if known, the repo path, the issue key, `~/.claude/autolearn/runs/<repo>/`, the lessons
   file. It reconstructs the stage analysis, writes the "Escaped bugs" lesson and drafts the
   proposal in `~/.claude/autolearn/proposals/`.
2. **Show the user** the result in a few lines: root stage, why it missed, the proposed guard(s).
   Then AskUserQuestion: apply / edit / reject.
3. **Apply on yes**, by guard type:
   - **Regression test** - add it to the repo (team repo: text for an MR / `/git-handoff`). Check
     it fails on the buggy commit and passes on the fix when that is cheap to do.
   - **Hook** - add the script to `~/.claude/hooks/autolearn/` and register it in
     `~/.claude/settings.json` (show the exact JSON first).
   - **Rule** - the line into the repo's CLAUDE.md (team repo: MR text) or the global CLAUDE.md.
   - **Prompt patch** - edit the named agent file in `~/.claude/agents/`.
   - **New reviewer** - write `~/.claude/agents/<name>.md` from the proposal and add a row to
     "Extra reviewers" in `~/.claude/autolearn/lessons/<repo>.md` (or `pipeline.md` if it is not
     repo-specific): `| <name> | <path globs or diff keywords> | <date> | escaped: <bug> |`.
     /feature step 5 launches it automatically whenever the trigger matches.
   Mark the proposal `Status: applied (<date>)` and commit `~/.claude/autolearn`.
4. **Report**: what escaped, where the guard now lives, how it will fire next time.

Guard choice rule: the most deterministic guard that would have caught it wins
(test > hook > rule > prompt patch > new agent). A new reviewer agent is worth its cost only for a
recurring *class* of defects that needs its own fresh-context pass.
