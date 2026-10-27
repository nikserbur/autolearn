"""Shared helpers for autolearn hooks. Standard library only, Python 3.9+."""
from __future__ import annotations

import json
import os
import pathlib
import re
import shlex
import sys

PLUGIN_NAMESPACE = "autolearn"
GATED_AGENTS = {"al-implementer", "al-test-writer"}
FILTERED_AGENTS = {"al-implementer", "al-test-writer"}
PLANNER_AGENTS = {"al-planner"}
WRAPPERS = {"sudo", "env", "time", "nice", "nohup", "command", "exec"}


def home() -> pathlib.Path:
    """Data directory: $AUTOLEARN_HOME or ~/.claude/autolearn."""
    env = os.environ.get("AUTOLEARN_HOME")
    return pathlib.Path(env).expanduser() if env else pathlib.Path.home() / ".claude" / "autolearn"


def read_payload() -> dict:
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def emit(obj: dict) -> int:
    print(json.dumps(obj, ensure_ascii=False))
    return 0


def agent_name(payload: dict) -> str:
    """Agent type of the subagent that fired the hook, or '' in the main session.
    Plugin agents arrive namespaced ('autolearn:al-implementer'); other namespaces are not ours."""
    raw = payload.get("agent_type") or ""
    if ":" in raw:
        namespace, _, name = raw.partition(":")
        return name if namespace == PLUGIN_NAMESPACE else ""
    return raw


def in_scope(payload: dict, allowed: set, argv: list) -> bool:
    """`--scope agent`: the hook is declared in the agent's own frontmatter, always act.
    Default (global, plugin mode): act only when the hook input names one of our agents."""
    if "--scope" in argv:
        i = argv.index("--scope")
        if i + 1 < len(argv) and argv[i + 1] == "agent":
            return True
    return agent_name(payload) in allowed


def repo_key(payload: dict) -> str:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()
    return pathlib.Path(root).name


def segments(command: str) -> list:
    """Split a shell command into simple commands (on &&, ||, ;, |, &, newlines) and return each
    as a token list with leading VAR=value assignments and wrappers (sudo, env, time...) removed."""
    out = []
    for part in re.split(r"&&|\|\||;|\|(?!&)|(?<![<>&|])&(?![>&])|\n", command):
        try:
            tok = shlex.split(part, comments=True)
        except ValueError:
            tok = part.split()
        while tok and (re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok[0]) or tok[0] in WRAPPERS
                       or tok[0] in ("(", "{", "!")):
            tok = tok[1:]
        if tok:
            out.append(tok)
    return out


def head(tokens: list) -> str:
    """'pytest', 'uv run', 'python3 -m', 'npm test'... : the command name (plus subcommand)."""
    if not tokens:
        return ""
    name = pathlib.Path(tokens[0]).name
    if len(tokens) > 1 and name in {"uv", "npm", "pnpm", "yarn", "bun", "cargo", "go", "make", "poetry", "pipx"}:
        return f"{name} {tokens[1]}"
    return name


GIT_GLOBAL_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path"}


def git_call(tokens: list, cwd: str):
    """For a `git ...` token list: (repo_dir, subcommand_args), skipping global options such as
    -C dir, -c k=v, --no-pager, --git-dir=x. (None, []) if this is not a git command."""
    if not tokens or tokens[0].rsplit("/", 1)[-1] != "git":
        return None, []
    args, repo = tokens[1:], cwd
    while args and args[0].startswith("-"):
        opt = args[0]
        if opt in GIT_GLOBAL_WITH_VALUE and len(args) > 1:
            if opt == "-C":
                repo = os.path.join(repo, os.path.expanduser(args[1]))
            args = args[2:]
        else:
            args = args[1:]  # --no-pager, -P, --git-dir=x, --bare, ...
    return os.path.normpath(repo), args


# Patterns that block a commit: specific, low false-positive rate.
SECRET_PATTERNS = {
    "AWS access key id": re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b"),
    "AWS secret key": re.compile(r"aws_secret_access_key\s*[=:]\s*\S{20,}", re.I),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "Anthropic/OpenAI key": re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{32,}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "token/password literal": re.compile(
        r"""(password|passwd|secret|token|api_key|apikey)\s*[=:]\s*["'][^"'\s]{8,}["']""", re.I
    ),
}

# Patterns redacted from captured text: broader than the commit guard; false positives are harmless.
REDACTIONS = [(rx, "[REDACTED]") for rx in SECRET_PATTERNS.values()] + [
    (re.compile(r"(?i)(?<![A-Za-z0-9])(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)(\s*[=:]\s*)\S{6,}"),
     r"\1\2[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "[REDACTED-JWT]"),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^/\s:@]+):[^@\s]+@"), r"\1:[REDACTED]@"),
]


def redact(text: str) -> str:
    for rx, repl in REDACTIONS:
        text = rx.sub(repl, text)
    return text
