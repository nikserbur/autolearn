#!/usr/bin/env bash
# One-time GitHub setup for the maintainer: description, homepage and topics (needs the gh CLI,
# logged in). Run after the repository exists on GitHub:
#   ./scripts/publish-github.sh <github-user>/research-lab
set -euo pipefail
repo="${1:?usage: $0 <owner>/research-lab}"

gh repo edit "$repo" \
  --description "A research lab for Claude Code: hypotheses in a simulation with written requirements, an independent reviewer that turns every gap between expected and actual behaviour into the next hypothesis, one judged look at a holdout, and a self-routing local knowledge base (RAG)." \
  --homepage "https://github.com/$repo#readme" \
  --enable-issues

# GitHub allows up to 20 topics: the Claude Code ones people search for, plus this project's niche.
gh repo edit "$repo" \
  --add-topic claude-code \
  --add-topic claude \
  --add-topic anthropic \
  --add-topic claude-code-plugin \
  --add-topic claude-skills \
  --add-topic agent-skills \
  --add-topic subagents \
  --add-topic ai-agents \
  --add-topic research \
  --add-topic experiments \
  --add-topic hypothesis-testing \
  --add-topic backtesting \
  --add-topic machine-learning \
  --add-topic model-training \
  --add-topic knowledge-base \
  --add-topic rag \
  --add-topic graphrag \
  --add-topic hybrid-search \
  --add-topic agent-memory \
  --add-topic context-engineering

echo "done: https://github.com/$repo"
