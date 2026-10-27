#!/usr/bin/env python3
"""Keep test-runner output small so subagents spend context on failures, not on dots.

Two roles in one file:
  test_output_filter.py [--scope agent]   PreToolUse(Bash) hook. A test command that is not yet
                                          piped through the filter is denied with the exact
                                          command to run instead (deny + suggestion works on
                                          every Claude Code version and never auto-approves).
  test_output_filter.py --filter          stdin -> stdout: keeps failures, errors and the summary,
                                          exits 1 when the output shows failures.
The suggestion is a plain `<cmd> 2>&1 | python3 <this file> --filter` so each part can match your
Bash allow rules (install.sh adds one for the filter); multi-line commands, comments and heredocs
are wrapped in `{ ... }` on their own lines.
"""
from __future__ import annotations

import re
import shlex
import sys

from _common import FILTERED_AGENTS, emit, head, in_scope, read_payload, segments

RUNNER_NAMES = {"pytest", "py.test", "tox", "nox", "jest", "vitest", "mocha", "rspec", "phpunit"}
RUNNER_HEADS = {"npm test", "pnpm test", "yarn test", "bun test", "cargo test", "cargo nextest",
                "go test", "make test", "uv run", "poetry run"}
KEEP = re.compile(r"(FAILED|ERROR|Error|error\[|error:|Traceback|assert|^E |AssertionError|"
                  r"passed|failed|skipped|short test summary|warnings summary|test result:|"
                  r"^ok |^FAIL|^--- FAIL|panicked|\d+ tests?)", re.I)
FAILURE = re.compile(r"(^FAILED|^ERROR|Traceback \(most recent|\b[1-9]\d* (failed|errors?)\b|^FAIL\b|"
                     r"^--- FAIL|panicked at|test result: FAILED|^E\s{2,})", re.M)
UV_OPTS_WITH_VALUE = {"--with", "--python", "-p", "--project", "--directory", "--extra", "--group",
                      "--env-file", "--package", "--index"}
MAX_LINES = 120
TAIL = 8


def is_test_command(command: str) -> bool:
    for tok in segments(command):
        h, name = head(tok), tok[0].rsplit("/", 1)[-1]
        if name in RUNNER_NAMES:
            return True
        if name in ("python", "python3") and tok[1:3] == ["-m", "pytest"]:
            return True
        if h in ("uv run", "poetry run"):
            rest = tok[2:]
            while rest and rest[0].startswith("-"):  # uv run --with pytest pytest ...
                rest = rest[2:] if rest[0] in UV_OPTS_WITH_VALUE else rest[1:]
            if rest and (rest[0] in RUNNER_NAMES or rest[:3] in (["python", "-m", "pytest"], ["python3", "-m", "pytest"])):
                return True
        elif h in RUNNER_HEADS:
            return True
    return False


def filter_stream(text: str) -> str:
    lines = text.splitlines()
    idx = sorted({i for i, l in enumerate(lines) if KEEP.search(l)}
                 | set(range(max(0, len(lines) - TAIL), len(lines))))
    if len(idx) > MAX_LINES:
        idx = idx[:MAX_LINES - TAIL] + idx[-TAIL:]
    out, prev = [f"[test-output-filter: showing {len(idx)} of {len(lines)} lines]"], -1
    for i in idx:
        if prev != -1 and i != prev + 1:
            out.append("...")
        out.append(lines[i])
        prev = i
    return "\n".join(out)


def main() -> int:
    if "--filter" in sys.argv:
        text = sys.stdin.read()
        print(filter_stream(text))
        return 1 if FAILURE.search(text) else 0
    payload = read_payload()
    if payload.get("tool_name") != "Bash" or not in_scope(payload, FILTERED_AGENTS, sys.argv):
        return 0
    cmd = (payload.get("tool_input") or {}).get("command") or ""
    if "test_output_filter.py" in cmd or not is_test_command(cmd):
        return 0
    tail = f"2>&1 | python3 {shlex.quote(__file__)} --filter"
    if "\n" in cmd or "#" in cmd or "<<" in cmd:
        wrapped = f"{{\n{cmd}\n}} {tail}"  # own lines keep trailing comments and heredocs valid
    else:
        wrapped = f"{cmd} {tail}"
    return emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "autolearn: run tests through the output filter so only failures "
                                    "and the summary reach your context. Run exactly this instead:\n" + wrapped,
    }})


if __name__ == "__main__":
    sys.exit(main())
