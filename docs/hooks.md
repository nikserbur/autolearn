# Hooks

All hooks are standard-library Python, read JSON on stdin, print JSON on stdout, exit 0, make
no network calls, and are covered by `tests/test_hooks.py`. They are the background mode's
machinery: they run on their events whatever Claude decides.

| Hook | Event | Effect |
|---|---|---|
| `session_brief.py` | SessionStart (`startup\|resume\|clear\|compact`) | Adds a pointers-only brief: how many topics the knowledge base has, a missing project page for this repo, and (weekly) an offer to install an embedding model while RAG runs on the hashing fallback. With `--seed` (plugin mode) it creates the data directory on the first session, and moves `~/.claude/autolearn` there once if that exists (the project's old name) |
| `kb_autoindex.py` | PostToolUse, matcher `Write\|Edit\|MultiEdit` | A write to `.md` / `.tsv` / `.txt` under the knowledge base or any folder named `KB` (a research KB) starts `kb_rag.py index` detached in the background, so the store (vectors, entity graph, context) is current without calling a skill; only changed chunks are embedded again, and a busy indexer picks the change up from a note. The indexer itself may load an embedding model if one is installed (the only exception to "no network": a first model download). `RESEARCH_LAB_PYTHON` picks the interpreter that has the model; `RESEARCH_LAB_AUTOINDEX=0` disables, `=sync` waits (tests) |
| `check_secrets.py` | PreToolUse, matcher `Bash` | For commands containing `git ... commit`: scans added lines of `git diff --cached` in each target repo (handles `cd X &&` and `git -C X`) for AWS keys, private keys, GitHub / Slack / Anthropic / OpenAI / Google keys and password-like literals; denies the call with the list |

The plugin registers them in `hooks/hooks.json`; `install.sh` adds the same three to
`~/.claude/settings.json` (idempotent, backed up) and `uninstall.sh` removes them.

## Environment variables

| Variable | Default | Used by |
|---|---|---|
| `RESEARCH_LAB_HOME` | `~/.claude/research-lab` | all hooks and scripts (skills assume the default; use it for testing) |
| `RESEARCH_LAB_PYTHON` | the python from `kb_rag.py setup`, else this one | `kb_rag.py` moves into it on start, so a skill's `python3` and the hook agree |
| `RESEARCH_LAB_MAX_FILE_MB` | `2` | `kb_rag.py`: larger files are not indexed and are reported |
| `RESEARCH_LAB_AUTOINDEX` | `1` | `kb_autoindex.py` (`0` disables, `sync` waits) |
| `RESEARCH_LAB_EMBED`, `RESEARCH_LAB_EMBED_MODEL` | `auto`, multilingual MiniLM | `kb_rag.py`: `fastembed`, `sentence-transformers` or `hash` |
| `RESEARCH_LAB_RERANK`, `RESEARCH_LAB_RERANK_MODEL` | `auto`, small English model | `kb_rag.py`: cross-encoder, `off` for the built-in reranker only |
| `RESEARCH_LAB_LATE` | `auto` | `kb_rag.py`: `off` disables late chunking |
| `RESEARCH_LAB_LLM`, `RESEARCH_LAB_LLM_MODEL`, `RESEARCH_LAB_CLAUDE_BIN` | `auto` | `kb_rag.py` index context: Ollama on this machine, `claude` opt-in, `off` |
| `AUTOLEARN_HOME` | `~/.claude/autolearn` | read once, to find the old data directory to move |

## Testing a hook by hand

```bash
echo '{"tool_name":"Write","tool_input":{"file_path":"'"$HOME"'/.claude/research-lab/knowledge/topics/x.md"}}' \
  | RESEARCH_LAB_AUTOINDEX=sync python3 hooks/kb_autoindex.py
```
