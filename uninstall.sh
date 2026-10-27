#!/usr/bin/env bash
# autolearn uninstaller. Nothing is deleted: what is removed is moved to
# ~/.claude/backups/autolearn-uninstall-<timestamp>/, and your own files that an install with
# --force replaced are put back.
#
#   ./uninstall.sh               remove agents, skills, hooks, settings entries, CLAUDE.md block
#   ./uninstall.sh --purge-data  also move the data directory (~/.claude/autolearn) to the backup
#   ./uninstall.sh --claude-dir D
set -euo pipefail

CLAUDE_DIR="${HOME}/.claude"
PURGE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --purge-data) PURGE=1 ;;
    --claude-dir) CLAUDE_DIR="$2"; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

AL="$CLAUDE_DIR/autolearn"
MANIFEST="$AL/.install-manifest"
BACKUP="$CLAUDE_DIR/backups/autolearn-uninstall-$(date +%Y%m%d-%H%M%S)"
[ -e "$MANIFEST" ] || { echo "no install manifest at $MANIFEST - nothing to do" >&2; exit 1; }
mkdir -p "$BACKUP"

stash() {
  local rel="$1"
  [ -e "$CLAUDE_DIR/$rel" ] || [ -L "$CLAUDE_DIR/$rel" ] || return 0
  mkdir -p "$BACKUP/$(dirname "$rel")"
  mv "$CLAUDE_DIR/$rel" "$BACKUP/$rel"
  echo "  moved $rel"
}

# 1. our files out
while IFS=$'\t' read -r kind rel _; do
  [ "$kind" = "file" ] && stash "$rel"
done < <(grep -v '^#' "$MANIFEST")

# 2. your originals back (replaced by an install with --force)
while IFS=$'\t' read -r kind rel saved; do
  [ "$kind" = "restore" ] || continue
  if [ -e "$saved" ] && [ ! -e "$CLAUDE_DIR/$rel" ]; then
    mkdir -p "$(dirname "$CLAUDE_DIR/$rel")"
    cp -R "$saved" "$CLAUDE_DIR/$rel"
    echo "  restored your $rel"
  fi
done < <(grep -v '^#' "$MANIFEST")

# 3. settings.json hooks and the CLAUDE.md block
if grep -qx 'settings' "$MANIFEST" && [ -e "$CLAUDE_DIR/settings.json" ]; then
  cp -L "$CLAUDE_DIR/settings.json" "$BACKUP/settings.json"
  python3 - "$CLAUDE_DIR/settings.json" <<'PY'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1]); cfg = json.loads(p.read_text())
for event, groups in list(cfg.get("hooks", {}).items()):
    for g in groups:
        g["hooks"] = [h for h in g.get("hooks", []) if "hooks/autolearn/" not in h.get("command", "")]
    cfg["hooks"][event] = [g for g in groups if g.get("hooks")]
    if not cfg["hooks"][event]:
        del cfg["hooks"][event]
perms = cfg.get("permissions", {})
if "allow" in perms:
    perms["allow"] = [r for r in perms["allow"] if "hooks/autolearn/" not in r]
p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
PY
  echo "  removed autolearn hooks from settings.json (a git push deny rule, if added, is kept)"
fi
if grep -qx 'claude-md' "$MANIFEST" && [ -e "$CLAUDE_DIR/CLAUDE.md" ]; then
  cp -L "$CLAUDE_DIR/CLAUDE.md" "$BACKUP/CLAUDE.md"
  python3 - "$CLAUDE_DIR/CLAUDE.md" <<'PY'
import pathlib, re, sys
p = pathlib.Path(sys.argv[1])
p.write_text(re.sub(r"\n?<!-- autolearn:start.*?<!-- autolearn:end -->\n?", "\n", p.read_text(), flags=re.S))
PY
  echo "  removed the routing block from CLAUDE.md"
fi

if [ "$PURGE" = 1 ]; then stash "autolearn"; else rm -f "$MANIFEST"; echo "  data directory kept: $AL"; fi
echo "backup: $BACKUP"
