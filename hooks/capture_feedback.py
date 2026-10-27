#!/usr/bin/env python3
"""UserPromptSubmit: notice feedback in your prompts and queue it for /learn. Silent, never blocks.

A cheap regex pass (no model call, no network) tags prompts that look like
  correction   "no, ...", "that's wrong", "I said ...", "don't use ..."
  rule         "always ...", "never ...", "from now on ..."
  approval     "perfect", "exactly", "good style", "keep doing"
  escaped_bug  "you missed a bug", "bug in prod", "reviewer missed"
and appends one JSON line (time, session id, project folder name, kinds, a 280-char excerpt with
secrets redacted) to $AUTOLEARN_HOME/inbox/feedback.jsonl.
/learn later turns these signals into lessons with evidence; nothing is learned silently.
Opt out with AUTOLEARN_CAPTURE=0.

English patterns are built in; 14 more languages (Chinese, Spanish, Portuguese, Russian, Japanese,
German, French, Korean, Italian, Turkish, Polish, Ukrainian, Dutch, Vietnamese) come from
$AUTOLEARN_HOME/feedback-patterns.json, seeded from templates/feedback-patterns.json. That file is
yours to edit: add phrasings, or set "enabled": ["ru", "de"] to use only some languages.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import sys

from _common import home, read_payload, redact, repo_key

PATTERNS = {
    "correction": [
        r"^\s*(no|nope|wrong|stop)\b(?![ -](problem|worries|need|rush|doubt))", r"\b(that'?s|this is|it'?s) (wrong|not what|not right)\b",
        r"\bI (said|told you|asked( you)?)\b", r"\bnot like (this|that)\b", r"\bwhy did you\b",
        r"\bdon'?t (do|use|add|touch|write|change|put)\b",
    ],
    "rule": [
        r"\b(always|never(?! mind)|from now on|every time|whenever you)\b",
    ],
    "approval": [
        r"\b(perfect|exactly( right)?|great job|well done|that'?s right|good style|keep doing)\b",
    ],
    "escaped_bug": [
        r"\b(you|reviewers?|review) missed\b", r"\bmissed (a|the) bug\b",
        r"\bbug (slipped|escaped|in prod)", r"\bbroke(n)? in prod",
        r"\bregress(ed|ion)\b.{0,30}\b(prod|production|release|deploy|main|master)\b",
    ],
}
def load_patterns() -> dict:
    """Built-in English patterns plus the user's local ones (other languages, own phrasings)."""
    merged = {k: list(v) for k, v in PATTERNS.items()}
    try:
        extra = json.loads((home() / "feedback-patterns.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        extra = {}
    if not isinstance(extra, dict):
        extra = {}
    sources = [extra]  # flat format: {"correction": [...], ...}
    if isinstance(extra.get("languages"), dict):
        enabled = extra.get("enabled")
        sources = [lang.get("patterns", {}) for code, lang in extra["languages"].items()
                   if isinstance(lang, dict) and (not enabled or code in enabled)]
    for src in sources:
        for kind, rxs in (src.items() if isinstance(src, dict) else []):
            if kind in PATTERNS and isinstance(rxs, list):
                merged[kind].extend(r for r in rxs if isinstance(r, str))
    compiled = {}
    for kind, rxs in merged.items():
        ok = []
        for r in rxs:
            try:
                ok.append(re.compile(r, re.I | re.M))
            except re.error:
                pass  # a broken user pattern must never break the prompt
        compiled[kind] = ok
    return compiled


COMPILED = load_patterns()
SCAN_CHARS = 2000
EXCERPT_CHARS = 280


def classify(prompt: str) -> list:
    head = prompt[:SCAN_CHARS]
    return [kind for kind, rxs in COMPILED.items() if any(rx.search(head) for rx in rxs)]


def main() -> int:
    if os.environ.get("AUTOLEARN_CAPTURE", "1") == "0":
        return 0
    payload = read_payload()
    prompt = (payload.get("prompt") or "").strip()
    if not prompt or prompt.startswith("/"):
        return 0  # slash commands are instructions to a skill, not feedback
    kinds = classify(prompt)
    if not kinds:
        return 0
    record = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "session": payload.get("session_id", ""),
        "project": repo_key(payload),
        "kinds": kinds,
        "excerpt": redact(" ".join(prompt.split())[:EXCERPT_CHARS]),
    }
    inbox = home() / "inbox"
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        with open(inbox / "feedback.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass  # never break the user's prompt over bookkeeping
    return 0


if __name__ == "__main__":
    sys.exit(main())
