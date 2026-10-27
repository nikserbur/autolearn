#!/usr/bin/env python3
"""PreToolUse(Bash) guard for the /feature `al-planner` agent.

Only the command name at the head of each simple command is judged, so reading code is never
limited (`grep -rn requests.get`, `sed -n 1,80p src/aws/client.py`, `cat main.tf` all pass).
- Denies network, cloud, cluster, database and package-install commands, and python -c one-liners
  that import network libraries. Extend with a regex in $AUTOLEARN_PLANNER_DENY (matched against
  the whole command).
- Code runs (python, pytest, uv run, cargo, node...): at most MAX_RUNS per agent run, and each
  must set the Bash `timeout` parameter to <= 60000 ms; otherwise denied with the fix.
Nothing is auto-approved: allowed commands go through the normal permission flow.

Usage: planner_guard.py [--scope agent]   (see _common.in_scope)
"""
from __future__ import annotations

import os
import re
import sys

from _common import PLANNER_AGENTS, emit, git_call, head, home, in_scope, read_payload, segments

MAX_RUNS = int(os.environ.get("AUTOLEARN_PLANNER_MAX_RUNS", "3"))
TIMEOUT_MS = 60_000
NET_NAMES = {"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "telnet", "ftp", "aws",
             "gcloud", "gsutil", "az", "kubectl", "helm", "terraform", "psql", "mysql", "mongo",
             "mongosh", "redis-cli", "pip", "pip3", "brew"}
NET_HEADS = {"uv pip", "uv add", "uv sync", "npm install", "npm i", "npm ci", "pnpm add",
             "pnpm install", "yarn add", "cargo add", "cargo install", "go get", "poetry add"}
GIT_NET = {"fetch", "pull", "push", "clone", "ls-remote"}
NET_PY = re.compile(r"\b(requests|httpx|aiohttp|urllib\.request|urllib3|boto3|botocore|socket|paramiko)\b")
RUN_NAMES = {"python", "python3", "pytest", "maturin", "jupyter", "node", "npx", "deno", "bun",
             "ruby", "Rscript", "julia"}
RUN_HEADS = {"uv run", "cargo run", "cargo test", "cargo bench", "go run", "go test",
             "poetry run", "npm run", "npm test", "make test"}
EXTRA = os.environ.get("AUTOLEARN_PLANNER_DENY", "").strip()


def deny(reason: str) -> int:
    return emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }})


def classify(command: str) -> tuple:
    net, runs, interpreter = False, False, False
    for tok in segments(command):
        name, h = tok[0].rsplit("/", 1)[-1], head(tok)
        _, git_args = git_call(tok, ".")
        if name in NET_NAMES or h in NET_HEADS or (git_args and git_args[0] in GIT_NET):
            net = True
        if "-m" in tok and tok[tok.index("-m") + 1:tok.index("-m") + 2] == ["pip"]:
            net = True  # python -m pip install ...
        if name in RUN_NAMES or h in RUN_HEADS:
            runs = True
        if name in ("python", "python3") or h in ("uv run", "poetry run"):
            interpreter = True
    # inline code (-c '...; ...', heredocs) is split apart by the shell parser, so scan it whole
    if interpreter and NET_PY.search(command):
        net = True
    if EXTRA and re.search(EXTRA, command, re.I):
        net = True
    return net, runs


def main() -> int:
    p = read_payload()
    if p.get("tool_name") != "Bash" or not in_scope(p, PLANNER_AGENTS, sys.argv):
        return 0
    ti = p.get("tool_input") or {}
    net, runs = classify(ti.get("command") or "")
    if net:
        return deny("/feature planner: network, cloud, production data and package installs are off "
                    "limits. Use the repo's fixtures or synthetic data, or move the question to "
                    "'Open questions' with a default decision.")
    if not runs:
        return 0
    timeout = ti.get("timeout")
    if not timeout or int(timeout) > TIMEOUT_MS:
        return deny(f"/feature planner: experiments are capped at 60 s. Re-run the same command with "
                    f"the Bash tool's timeout parameter set to {TIMEOUT_MS}.")
    key = p.get("agent_id") or p.get("session_id") or "default"
    state = home() / "runs" / f".planner-runs-{key}"
    state.parent.mkdir(parents=True, exist_ok=True)
    n = int(state.read_text() or 0) if state.exists() else 0
    if n >= MAX_RUNS:
        return deny(f"Experiment budget spent ({MAX_RUNS} runs). Read code only from here on; put "
                    "anything unclear into 'Open questions' with a default decision.")
    state.write_text(str(n + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
