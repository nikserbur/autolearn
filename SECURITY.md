# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[security advisories](https://github.com/nikserbur/research-lab/security/advisories/new)
rather than a public issue. You will get an answer within a week.

## Threat model in brief

research-lab runs hooks and scripts on your machine with your permissions and lets agents write
to `~/.claude/research-lab/`. The design keeps that surface small:

| Risk | Mitigation |
|---|---|
| Hooks doing more than they say | stdlib-only Python, no network of their own, short and tested; read them before installing. `kb_autoindex` starts the indexer, which may fetch a model you installed support for and, by default, talk to an Ollama server only on this machine |
| Notes leaving your machine | only with `RESEARCH_LAB_LLM=claude`, or `ollama` pointed at another host; `auto` refuses non-local hosts and reaches a local server past `$HTTP_PROXY` |
| A confidential research folder joining other searches | an indexed folder stays in every later search; `kb_rag.py status` lists them and `kb_rag.py forget --root DIR` drops one |
| Experiments touching real systems | the simulation environment requires no live orders or accounts and no network (S4 in `ENVIRONMENT.md`); runs use frozen script copies within resource limits; nothing stops other people's processes |
| Web research leaking internal data | the web is read-only (no sign-ins, forms, posts or downloads) and search queries carry no internal names, data or secrets ([SOURCES.md](skills/knowledge-ingest/SOURCES.md)); in the direct mode only the scout and the reviewer browse |
| Prompt injection through the knowledge base, web pages or third-party skills | all of it is treated as data, and the scout reports a page that tries to instruct it; nothing is installed automatically. Every line a note contributes to `kb_rag.py` output starts with `| `, so that a note cannot pass for an engine line. The optional index LLM gets documents inside a fence with a random name and no tools; its validated answers become search text and graph edges only, and only between things the text names |
| Hostile `kb_rag.py events` input | ids are slugged and hashed, so they cannot leave `<KB>/.stream/`; documents over 1 MB and malformed events are rejected and reported |
| Leaking secrets | `check_secrets.py` blocks commits with key-like strings; knowledge pages never store secrets or personal data |
| Installer clobbering your setup | the installer stops before changing anything on a name clash unless `--force`; replaced files are backed up (symlinks dereferenced) and restored by `uninstall.sh` |

## Supported versions

The latest release receives fixes.
