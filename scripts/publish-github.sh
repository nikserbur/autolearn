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
# PUT replaces the whole list, so topics from an earlier description do not pile up.
gh api --method PUT "repos/$repo/topics" --silent \
  -f 'names[]=claude-code' \
  -f 'names[]=claude' \
  -f 'names[]=anthropic' \
  -f 'names[]=claude-code-plugin' \
  -f 'names[]=claude-skills' \
  -f 'names[]=agent-skills' \
  -f 'names[]=subagents' \
  -f 'names[]=ai-agents' \
  -f 'names[]=research' \
  -f 'names[]=experiments' \
  -f 'names[]=hypothesis-testing' \
  -f 'names[]=backtesting' \
  -f 'names[]=machine-learning' \
  -f 'names[]=model-training' \
  -f 'names[]=knowledge-base' \
  -f 'names[]=rag' \
  -f 'names[]=graphrag' \
  -f 'names[]=hybrid-search' \
  -f 'names[]=agent-memory' \
  -f 'names[]=context-engineering'

echo "done: https://github.com/$repo"
