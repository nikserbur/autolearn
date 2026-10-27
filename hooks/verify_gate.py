#!/usr/bin/env python3
"""Stop / SubagentStop gate for the `al-implementer` and `al-test-writer` agents of /feature.

Before launching the agent, the controller writes
  $AUTOLEARN_HOME/runs/<repo>/.current-task.json
  {"task": "Task 2: ...", "verify": "uv run pytest tests/test_x.py -q", "max_blocks": 2,
   "timeout": 600, "created": <unix time>}
The verify command is the one the user approved in PLAN.md.

On every attempt to finish, this hook runs `verify`. If it fails, the agent may not stop yet
(decision=block) and gets the tail of the output. After max_blocks blocks the agent is released
on the next failure: the hook writes `passed: false` to .current-task.state.json and exits
silently, so the controller records FAILED no matter what the agent claims.
No task file, a task file older than TTL_HOURS, or STATUS: BLOCKED/FAILED -> no gating.

Usage: verify_gate.py [--scope agent]   (see _common.in_scope)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

from _common import GATED_AGENTS, emit, home, in_scope, read_payload, repo_key

TTL_HOURS = 6


def load(path) -> dict:
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def save(path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1))


def main() -> int:
    payload = read_payload()
    if not in_scope(payload, GATED_AGENTS, sys.argv):
        return 0
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    task_file = home() / "runs" / repo_key(payload) / ".current-task.json"
    if not task_file.exists():
        return 0
    try:
        created = float(load(task_file).get("created"))
        created = created / 1000 if created > 1e12 else created  # milliseconds
    except (TypeError, ValueError):
        created = task_file.stat().st_mtime
    if time.time() - created > TTL_HOURS * 3600:
        return 0  # stale file from an interrupted run: never gate on it
    task = load(task_file)
    verify = (task.get("verify") or "").strip()
    if not verify:
        return 0

    state_file = task_file.with_name(".current-task.state.json")
    state = load(state_file) if state_file.exists() else {}
    if state.get("task") != task.get("task"):
        state = {"task": task.get("task"), "blocks": 0}
    max_blocks = int(task.get("max_blocks", 3))
    timeout = int(task.get("timeout", 600))

    last = (payload.get("last_assistant_message") or "").upper()
    for status in ("STATUS: BLOCKED", "STATUS: FAILED"):
        if status in last:  # the agent gave up honestly; record it and let it stop
            state.update({"passed": False, "released": status.split(": ")[1].lower()})
            save(state_file, state)
            return 0

    try:
        proc = subprocess.run(["bash", "-c", verify], cwd=root, capture_output=True,
                              text=True, timeout=timeout)
        code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        code, out = 124, f"timeout after {timeout}s"

    tail = "\n".join(out.splitlines()[-40:])
    state.update({"verify": verify, "exit_code": code, "tail": tail})

    if code == 0:
        state["passed"] = True
        save(state_file, state)
        return 0

    state["passed"] = False
    failures = int(state.get("blocks", 0)) + 1
    state["blocks"] = failures
    if failures > max_blocks:
        state["released"] = "max_blocks"
        save(state_file, state)
        return emit({"systemMessage": f"autolearn gate: '{task.get('task')}' still fails after "
                                      f"{failures} verify runs; released as FAILED."})
    save(state_file, state)
    reason = (f"Pipeline gate: the verify command exited with code {code} "
              f"(failure {failures}; you will be released after {max_blocks + 1}).\n"
              f"Command: {verify}\nOutput tail:\n{tail}\n\n"
              "Fix the task's code and try to finish again. Do not weaken or delete tests. If "
              "verification is impossible within the task's boundaries, finish with STATUS: BLOCKED "
              "and the reason; if you are out of ideas, finish with STATUS: FAILED and the output.")
    return emit({"decision": "block", "reason": reason})


if __name__ == "__main__":
    sys.exit(main())
