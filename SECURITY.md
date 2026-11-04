# Security policy

## Reporting a vulnerability

Please report security issues privately through GitHub's
[security advisories](https://github.com/nikserbur/autolearn/security/advisories/new)
rather than a public issue. You will get an answer within a week.

## Threat model in brief

autolearn runs hooks and scripts on your machine with your permissions and lets agents write
to `~/.claude/autolearn/`. The design keeps that surface small:

| Risk | Mitigation |
|---|---|
| Hooks doing more than they say | stdlib-only Python, no network of their own, short and tested; read them before installing. `kb_autoindex` starts the indexer, which may fetch a model you installed support for and, by default, talk to an Ollama server only on this machine |
| Notes leaving your machine | only with `AUTOLEARN_LLM=claude`, or `ollama` pointed at another host; `auto` refuses non-local hosts |
| Self-modification going wrong | agents write only to the data directory; prompts, skills, settings and `CLAUDE.md` change only after you approve a proposal; the data directory is a git repo |
| Prompt injection through the knowledge base, web pages or third-party skills | all of it is treated as data; `al-skill-scout` flags instruction-like text as a red flag; nothing is installed automatically. The optional index LLM gets documents fenced as data and no tools, and its validated answers become search text and graph edges only |
| Hostile `kb_rag.py events` input | ids are slugged and hashed, so they cannot leave `<KB>/.stream/`; documents over 1 MB and malformed events are rejected and reported |
| Leaking secrets | `check_secrets.py` blocks commits with key-like strings; the feedback hook redacts excerpts; `inventory.py` never prints MCP configuration |
| Private prompts on disk | the inbox stores short redacted excerpts locally only; `AUTOLEARN_CAPTURE=0` disables capture; `inbox/` is git-ignored |
| The verify gate runs a command from a data file | the controller writes it from the PLAN.md you approved; task files older than 6 h are ignored; review verify commands at plan approval |
| Lessons steering future runs | lessons are committed with evidence and shown after every run; lesson text comes only from your own messages and run artifacts; pipeline rules and extra reviewers change only through approved proposals |
| Installer clobbering your setup | the installer stops before changing anything on a name clash unless `--force`; replaced files are backed up (symlinks dereferenced) and restored by `uninstall.sh` |

## Supported versions

The latest release receives fixes.
