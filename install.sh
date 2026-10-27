#!/usr/bin/env bash
# autolearn installer: copies agents, skills and hooks into ~/.claude, seeds the data directory
# ~/.claude/autolearn (never overwritten), and registers three global hooks in settings.json.
#
#   ./install.sh                   install or upgrade; stops before changing anything if one of
#                                  your own files has the same name as an autolearn file
#   ./install.sh --dry-run         show what would happen
#   ./install.sh --force           replace such files too; your originals are backed up and put
#                                  back by ./uninstall.sh
#   ./install.sh --with-claude-md  also add the routing-table block to ~/.claude/CLAUDE.md
#   ./install.sh --deny-push       also deny `git push` for the agent in settings.json
#   ./install.sh --no-settings     do not touch settings.json
#   ./install.sh --claude-dir D    install somewhere else (for testing)
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
DRY=0 FORCE=0 WITH_MD=0 DENY_PUSH=0 SETTINGS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --force) FORCE=1 ;;
    --with-claude-md) WITH_MD=1 ;;
    --deny-push) DENY_PUSH=1 ;;
    --no-settings) SETTINGS=0 ;;
    --claude-dir) CLAUDE_DIR="$2"; shift ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

command -v python3 >/dev/null || { echo "python3 is required (hooks and scripts use the standard library only)" >&2; exit 1; }

AL="$CLAUDE_DIR/autolearn"
HOOKS_DST="$CLAUDE_DIR/hooks/autolearn"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$CLAUDE_DIR/backups/autolearn-$STAMP"
MANIFEST="$AL/.install-manifest"
NEW_ENTRIES=""

say() { printf '%s\n' "$*"; }
do_() { if [ "$DRY" = 1 ]; then say "  [dry-run] $*"; else "$@"; fi; }
record() { NEW_ENTRIES="${NEW_ENTRIES}$1"$'\n'; }
ours() { [ -e "$MANIFEST" ] && grep -qxF "file	$1" "$MANIFEST"; }  # installed by a previous run?

backup() {  # copy the real content (symlinks dereferenced) into the backup dir; echo its path
  local path="$1" rel="${1#"$CLAUDE_DIR"/}"
  do_ mkdir -p "$BACKUP/$(dirname "$rel")" || return 1
  do_ cp -RL "$path" "$BACKUP/$rel" || { echo "backup of $rel failed; nothing was replaced" >&2; return 1; }
  printf '%s' "$BACKUP/$rel"
}
exists() { [ -e "$1" ] || [ -L "$1" ]; }  # a dangling symlink counts as existing

# 1. collisions first: nothing changes if one of your files would be replaced without --force
targets=()
for f in "$SRC"/agents/*.md; do targets+=("agents/$(basename "$f")|$f"); done
for d in "$SRC"/skills/*/; do targets+=("skills/$(basename "$d")|${d%/}"); done
conflicts=()
for t in "${targets[@]}"; do
  rel="${t%%|*}" src="${t#*|}" dst="$CLAUDE_DIR/${t%%|*}"
  if exists "$dst" && ! diff -rq -x __pycache__ "$src" "$dst" >/dev/null 2>&1 && ! ours "$rel"; then
    conflicts+=("$rel")
  fi
done
if [ ${#conflicts[@]} -gt 0 ] && [ "$FORCE" = 0 ]; then
  say "Stopped before changing anything: these already exist in $CLAUDE_DIR and are not from autolearn:"
  printf '  %s\n' "${conflicts[@]}"
  say "Rename yours, or rerun with --force (your originals are backed up and ./uninstall.sh restores them)."
  exit 1
fi

say "autolearn -> $CLAUDE_DIR"

# 2. agents and skills: back up everything that will be replaced first, then copy.
#    If any backup fails, nothing has been copied or removed yet.
replace=()
for t in "${targets[@]}"; do
  rel="${t%%|*}" src="${t#*|}" dst="$CLAUDE_DIR/${t%%|*}"
  if exists "$dst" && ! diff -rq -x __pycache__ "$src" "$dst" >/dev/null 2>&1; then
    saved="$(backup "$dst")" || exit 1                 # $(...) ignores set -e: check explicitly
    ours "$rel" || record "restore	$rel	$saved"   # a file of yours: uninstall puts it back
    replace+=("$t")
  elif ! exists "$dst"; then
    replace+=("$t")
  fi
  record "file	$rel"
done
for t in ${replace[@]+"${replace[@]}"}; do
  src="${t#*|}" dst="$CLAUDE_DIR/${t%%|*}"
  do_ rm -rf "$dst"
  do_ mkdir -p "$(dirname "$dst")"
  do_ cp -R "$src" "$dst"
done

# 3. hooks live in their own namespace and are refreshed on every run
changed=0
for f in "$SRC"/hooks/*.py; do cmp -s "$f" "$HOOKS_DST/$(basename "$f")" || changed=1; done
if [ -d "$HOOKS_DST" ] && [ "$changed" = 1 ]; then backup "$HOOKS_DST" >/dev/null || exit 1; fi
do_ mkdir -p "$HOOKS_DST"
do_ cp "$SRC"/hooks/*.py "$HOOKS_DST/"
record "file	hooks/autolearn"

# 4. data directory: seeded once from skeleton templates, never overwritten, under git
if [ "$DRY" = 1 ]; then say "  [dry-run] seed $AL from templates (existing files are kept)"
else python3 "$SRC/hooks/_seed.py" "$AL" "$SRC/templates"; fi

# 5. settings.json: three global hooks (+ optional push deny), idempotent, with a backup
if [ "$SETTINGS" = 1 ]; then
  SETTINGS_FILE="$CLAUDE_DIR/settings.json"
  if exists "$SETTINGS_FILE"; then backup "$SETTINGS_FILE" >/dev/null || exit 1; fi
  if [ "$DRY" = 1 ]; then
    say "  [dry-run] register SessionStart, UserPromptSubmit and PreToolUse(Bash) hooks in $SETTINGS_FILE"
  else
    CUSTOM_HOME=""; [ "$CLAUDE_DIR" != "$HOME/.claude" ] && CUSTOM_HOME="$AL"
    python3 - "$SETTINGS_FILE" "$HOOKS_DST" "$DENY_PUSH" "$CUSTOM_HOME" <<'PY'
import json, pathlib, shlex, sys
path, hooks, deny_push, custom = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "1", sys.argv[4]
try:
    text = path.read_text() if path.exists() else ""
    cfg = json.loads(text) if text.strip() else {}
except ValueError:
    sys.exit("  settings.json is not valid JSON; fix it or rerun with --no-settings")
def cmd(name):  # a non-default --claude-dir also needs its data dir passed to the hook
    prefix = f"AUTOLEARN_HOME={shlex.quote(custom)} " if custom else ""
    return f'{prefix}python3 "{hooks}/{name}"'
wanted = [
    ("SessionStart", "startup|resume|clear|compact", cmd("session_brief.py"), 10),
    ("UserPromptSubmit", None, cmd("capture_feedback.py"), 5),
    ("PreToolUse", "Bash", cmd("check_secrets.py"), 20),
]
h = cfg.setdefault("hooks", {})
for event, matcher, command, timeout in wanted:
    groups = h.setdefault(event, [])
    if any(command == x.get("command") for g in groups for x in g.get("hooks", [])):
        continue
    group = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
    if matcher:
        group = {"matcher": matcher, **group}
    groups.append(group)
# let subagents run the test-output filter without a prompt (it only reads stdin)
allow = cfg.setdefault("permissions", {}).setdefault("allow", [])
rule = f"Bash(python3 {hooks}/test_output_filter.py --filter)"
if rule not in allow:
    allow.append(rule)
if deny_push:
    deny = cfg.setdefault("permissions", {}).setdefault("deny", [])
    for rule in ("Bash(git push)", "Bash(git push *)"):
        if rule not in deny:
            deny.append(rule)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")  # writes through a symlink
PY
  fi
  record "settings"
fi

# 6. CLAUDE.md routing-table block (opt-in; only the table, no personal workflow rules)
if [ "$WITH_MD" = 1 ]; then
  MD="$CLAUDE_DIR/CLAUDE.md"
  if [ -e "$MD" ] && grep -q 'autolearn:start' "$MD"; then :
  elif [ -e "$MD" ]; then
    backup "$MD" >/dev/null || exit 1
    if [ "$DRY" = 1 ]; then say "  [dry-run] append the routing block to $MD"
    else printf '\n' >> "$MD"; cat "$SRC/templates/CLAUDE.md" >> "$MD"; fi
  else
    do_ cp "$SRC/templates/CLAUDE.md" "$MD"
  fi
  record "claude-md"
fi

# 7. manifest: merged with earlier runs so uninstall knows everything ever installed
if [ "$DRY" = 0 ]; then
  { if [ -e "$MANIFEST" ]; then grep -v '^#' "$MANIFEST" || true; fi; printf '%s' "$NEW_ENTRIES"; } \
    | awk 'NF && !seen[$0]++' > "$MANIFEST.tmp"
  { printf '# autolearn install manifest (last run %s from %s)\n' "$STAMP" "$SRC"; cat "$MANIFEST.tmp"; } > "$MANIFEST"
  rm -f "$MANIFEST.tmp"
fi

say ""
say "done. backups (if anything was replaced): $BACKUP"
say "next:"
say "  - restart Claude Code (hooks load at session start)"
[ "$WITH_MD" = 0 ] && say "  - optional: ./install.sh --with-claude-md adds the routing table to ~/.claude/CLAUDE.md"
say "  - put your team's checklists into $AL/knowledge/required-docs.md"
say "  - try:  /kb-ingest repo   /feature \"small task\"   /merge-check   /skill-radar"
