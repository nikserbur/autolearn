#!/usr/bin/env bash
# Replace the YOUR_GITHUB_USER / YOUR_NAME placeholders before publishing.
#   ./scripts/set-owner.sh <github-user> "<Your Name>"
set -euo pipefail
[ $# -eq 2 ] || { echo "usage: $0 <github-user> \"<Your Name>\"" >&2; exit 2; }
user="$1" name="$2"
cd "$(dirname "$0")/.."
grep -rl --exclude-dir=.git -e YOUR_GITHUB_USER -e YOUR_NAME . | grep -vE 'scripts/(set-owner\.sh|validate\.py)|CONTRIBUTING\.md' | while read -r f; do
  python3 - "$f" "$user" "$name" <<'PY'
import pathlib, sys
p, user, name = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
p.write_text(p.read_text().replace("YOUR_GITHUB_USER", user).replace("YOUR_NAME", name))
PY
  echo "updated $f"
done
