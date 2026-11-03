#!/usr/bin/env python3
"""PostToolUse hook (Write|Edit|MultiEdit): keeps the RAG vector store current without anyone
calling a skill. A write into the knowledge base (`$AUTOLEARN_HOME/knowledge`) or into any
folder named `KB` (a research knowledge base) starts an incremental `kb_rag.py index` in the
background, so the next search already sees the new chunks. Never blocks, prints nothing.

AUTOLEARN_AUTOINDEX=0 disables it; AUTOLEARN_AUTOINDEX=sync waits for the indexer (tests).
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _common import home, read_payload  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
ENGINE_CANDIDATES = [
    HERE.parent / "skills" / "research" / "scripts" / "kb_rag.py",           # plugin / repo layout
    HERE.parent.parent / "skills" / "research" / "scripts" / "kb_rag.py",    # install.sh layout
]


def kb_root(path: pathlib.Path):
    """(is_global_kb, research_kb_dir or None) for a written file."""
    kb = (home() / "knowledge").resolve()
    if path == kb or kb in path.parents:
        return True, None
    for parent in path.parents:
        if parent.name == "KB":
            return False, parent
    return False, None


def main() -> int:
    mode = os.environ.get("AUTOLEARN_AUTOINDEX", "1")
    if mode == "0":
        return 0
    payload = read_payload()
    raw = (payload.get("tool_input") or {}).get("file_path") or ""
    if not raw or pathlib.Path(raw).suffix not in (".md", ".tsv", ".txt"):
        return 0
    is_global, research = kb_root(pathlib.Path(raw).expanduser().resolve())
    if not is_global and research is None:
        return 0
    engine = next((p for p in ENGINE_CANDIDATES if p.exists()), None)
    if engine is None:
        return 0
    cmd = [sys.executable, str(engine), "index"] + (["--root", str(research)] if research else [])
    try:
        if mode == "sync":
            subprocess.run(cmd, capture_output=True, timeout=600)
        else:
            flags = {"creationflags": 0x00000008 | 0x00000200} if os.name == "nt" \
                else {"start_new_session": True}  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True, **flags)
    except (OSError, subprocess.SubprocessError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
