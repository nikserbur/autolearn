#!/usr/bin/env python3
"""Inventory of installed Claude Code capabilities. Read-only, standard library only.

    inventory.py [--project DIR] [--claude-dir ~/.claude]

Lists skills and agents (user and project level) with the first line of their description,
installed plugins, and MCP server NAMES. MCP configuration values are never printed: they can
hold tokens. Skill usage counts are shown when Claude Code records them.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys


def frontmatter(path: pathlib.Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    m = re.match(r"^---\n(.*?)\n---", text, re.S)
    if not m:
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            out[key] = "" if val in (">", "|", ">-", "|-") else val
        elif key and line.startswith(" "):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def short(text: str, n: int = 90) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1] + "..."


def load_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--project", type=pathlib.Path, default=pathlib.Path.cwd())
    ap.add_argument("--claude-dir", type=pathlib.Path, default=pathlib.Path.home() / ".claude")
    a = ap.parse_args()
    state = load_json(a.claude_dir.parent / ".claude.json")
    usage = state.get("skillUsage") if isinstance(state.get("skillUsage"), dict) else {}

    def uses(name):
        v = usage.get(name)
        if isinstance(v, dict):
            v = v.get("usageCount", v.get("count"))
        return f" (used {v}x)" if isinstance(v, int) else ""

    for scope, base in (("user", a.claude_dir), ("project", a.project / ".claude")):
        skills = sorted(base.glob("skills/*/SKILL.md"))
        print(f"## Skills ({scope}, {len(skills)})")
        for s in skills:
            fm = frontmatter(s)
            name = fm.get("name") or s.parent.name
            print(f"- {name}{uses(name)}: {short(fm.get('description', ''))}")
        agents = sorted(base.glob("agents/*.md"))
        print(f"\n## Agents ({scope}, {len(agents)})")
        for g in agents:
            fm = frontmatter(g)
            print(f"- {fm.get('name') or g.stem} [{fm.get('model', 'inherit')}]: {short(fm.get('description', ''))}")
        print()

    installed = load_json(a.claude_dir / "plugins" / "installed_plugins.json")
    plugins = sorted((installed.get("plugins") or installed or {}).keys()) if isinstance(installed, dict) else []
    print(f"## Plugins ({len(plugins)})")
    print("\n".join(f"- {p}" for p in plugins) if plugins else "- none found (or run: claude plugin list)")

    servers = set((state.get("mcpServers") or {}).keys())
    proj = (state.get("projects") or {}).get(str(a.project.resolve()), {})
    servers |= set((proj.get("mcpServers") or {}).keys()) if isinstance(proj, dict) else set()
    servers |= set((load_json(a.project / ".mcp.json").get("mcpServers") or {}).keys())
    print(f"\n## MCP servers ({len(servers)}; names only)")
    print("\n".join(f"- {s}" for s in sorted(servers)) if servers else "- none configured locally "
          "(connectors added in the app may not appear here)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
