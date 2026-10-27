#!/usr/bin/env bash
# One-time GitHub setup for the maintainer: description, homepage and topics (needs the gh CLI,
# logged in). Run after the repository exists on GitHub:
#   ./scripts/publish-github.sh <github-user>/autolearn
set -euo pipefail
repo="${1:?usage: $0 <owner>/autolearn}"

gh repo edit "$repo" \
  --description "Missed a bug? It grows a reviewer for it. A self-learning Claude Code plugin: plan, TDD, review and merge-gate pipeline that turns your corrections and escaped bugs into lessons, tests and new reviewer agents, plus a routed local knowledge base." \
  --homepage "https://github.com/$repo#readme" \
  --enable-issues

# GitHub allows up to 20 topics; chosen from the most frequent topics of popular Claude Code repos
# plus this project's niche (self-learning, memory, knowledge base, code review).
gh repo edit "$repo" \
  --add-topic claude-code \
  --add-topic claude \
  --add-topic anthropic \
  --add-topic claude-code-plugin \
  --add-topic claude-skills \
  --add-topic agent-skills \
  --add-topic claude-code-hooks \
  --add-topic subagents \
  --add-topic multi-agent \
  --add-topic ai-agents \
  --add-topic agentic-coding \
  --add-topic coding-agents \
  --add-topic self-learning \
  --add-topic continual-learning \
  --add-topic ai-memory \
  --add-topic agent-memory \
  --add-topic knowledge-base \
  --add-topic context-engineering \
  --add-topic code-review \
  --add-topic tdd

echo "done: https://github.com/$repo"
