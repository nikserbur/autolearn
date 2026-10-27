#!/usr/bin/env python3
"""Structural checks for the repo, run in CI and before every release. Standard library only.

    python3 scripts/validate.py

Checks agent and skill frontmatter, that every referenced file exists (hook scripts in agent
frontmatter, local links and scripts in SKILL.md, hooks.json commands), plugin and marketplace
manifests, and that all Python compiles. Exit 1 on any error.
"""
from __future__ import annotations

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODELS = {"opus", "sonnet", "haiku", "fable", "inherit"}
errors, warnings = [], []


def frontmatter(path: pathlib.Path) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", path.read_text(encoding="utf-8"), re.S)
    if not m:
        errors.append(f"{path.relative_to(ROOT)}: no YAML frontmatter")
        return {}
    out, key = {}, None
    for line in m.group(1).splitlines():
        kv = re.match(r"^([A-Za-z_-]+):\s*(.*)$", line)
        if kv:
            key, val = kv.group(1), kv.group(2).strip()
            out[key] = "" if val in (">", "|") else val.strip("'\"")
        elif key and line.startswith(" "):
            out[key] = (out[key] + " " + line.strip()).strip()
    return out


def check_agents() -> None:
    for f in sorted((ROOT / "agents").glob("*.md")):
        fm, rel = frontmatter(f), f.relative_to(ROOT)
        if fm.get("name") != f.stem:
            errors.append(f"{rel}: name '{fm.get('name')}' != file name")
        if not f.stem.startswith("al-"):
            errors.append(f"{rel}: agent names start with 'al-' so they never collide with other setups")
        if not fm.get("description"):
            errors.append(f"{rel}: missing description")
        if fm.get("model") and fm["model"] not in MODELS:
            errors.append(f"{rel}: unknown model '{fm['model']}'")
        for script in re.findall(r"hooks/autolearn/(\w+\.py)", f.read_text(encoding="utf-8")):
            if not (ROOT / "hooks" / script).exists():
                errors.append(f"{rel}: hook script {script} not in hooks/")


def check_skills() -> None:
    agents = {f.stem for f in (ROOT / "agents").glob("*.md")}
    builtin = {"Explore", "general-purpose", "Plan"}
    for d in sorted(p for p in (ROOT / "skills").iterdir() if p.is_dir()):
        f, rel = d / "SKILL.md", d.relative_to(ROOT)
        if not f.exists():
            errors.append(f"{rel}: no SKILL.md")
            continue
        fm, text = frontmatter(f), f.read_text(encoding="utf-8")
        if fm.get("name") != d.name:
            errors.append(f"{rel}: name '{fm.get('name')}' != directory name")
        desc = fm.get("description", "")
        if not desc:
            errors.append(f"{rel}: missing description")
        elif len(desc) > 1024:
            errors.append(f"{rel}: description is {len(desc)} chars (> 1024)")
        for link in re.findall(r"\]\(([^)#:]+\.md)\)", text):
            if not (d / link).exists():
                errors.append(f"{rel}: broken link {link}")
        for script in re.findall(r'python3 "\$\{CLAUDE_SKILL_DIR\}/((?:\.\./[\w-]+/)?scripts/\w+\.py)"', text):
            if not (d / script).resolve().exists():
                errors.append(f"{rel}: referenced script {script} does not exist")
        if re.search(r"python3 (\.\./|scripts/)", text):
            errors.append(f"{rel}: script path without ${{CLAUDE_SKILL_DIR}} (breaks in plugin mode)")
        for agent in re.findall(r"Agent -> `([\w-]+)`", text):
            if agent not in agents | builtin:
                errors.append(f"{rel}: delegates to unknown agent '{agent}'")


def check_hooks_json() -> None:
    path = ROOT / "hooks" / "hooks.json"
    try:
        cfg = json.loads(path.read_text())
    except ValueError as e:
        errors.append(f"hooks/hooks.json: invalid JSON ({e})")
        return
    for event, groups in cfg.get("hooks", {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                for script in re.findall(r"hooks/(\w+\.py)", h.get("command", "")):
                    if not (ROOT / "hooks" / script).exists():
                        errors.append(f"hooks/hooks.json: {event} -> missing {script}")


def check_manifests() -> None:
    try:
        plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())
        market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    except (OSError, ValueError) as e:
        errors.append(f".claude-plugin: {e}")
        return
    name = plugin.get("name", "")
    if not re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) or name.startswith(("claude-", "anthropic-")):
        errors.append(f"plugin.json: invalid plugin name '{name}'")
    entry = next((p for p in market.get("plugins", []) if p.get("name") == name), None)
    if not entry:
        errors.append("marketplace.json: no plugin entry with the plugin's name")
    elif "version" in entry:
        warnings.append("marketplace.json: keep the version only in plugin.json")
    if not plugin.get("version"):
        errors.append("plugin.json: missing version")
    for key in ("name", "owner", "plugins"):
        if key not in market:
            errors.append(f"marketplace.json: missing '{key}'")
    if "YOUR_GITHUB_USER" in json.dumps(plugin) + json.dumps(market):
        warnings.append("manifests still contain YOUR_GITHUB_USER: run scripts/set-owner.sh before publishing")


def check_english_only() -> None:
    """The repo is English-only, except the multilingual templates/feedback-patterns.json."""
    for f in sorted(ROOT.rglob("*")):
        if ".git" in f.parts or not f.is_file() or f.name == "feedback-patterns.json":
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if re.search(r"[\u0400-\u04FF]", line):
                errors.append(f"{f.relative_to(ROOT)}:{n}: Cyrillic text (keep the repo English-only)")


def check_feedback_patterns() -> None:
    path = ROOT / "templates" / "feedback-patterns.json"
    try:
        langs = json.loads(path.read_text(encoding="utf-8"))["languages"]
    except (OSError, ValueError, KeyError) as e:
        errors.append(f"templates/feedback-patterns.json: {e}")
        return
    kinds = ("correction", "rule", "approval", "escaped_bug")
    for code, lang in langs.items():
        ex = lang.get("examples", {})
        for kind in kinds + ("neutral",):
            if not ex.get(kind):
                errors.append(f"feedback-patterns.json: {code} has no '{kind}' example")
        for kind, rxs in lang.get("patterns", {}).items():
            if kind not in kinds:
                errors.append(f"feedback-patterns.json: {code} has unknown kind '{kind}'")
            for rx in rxs:
                try:
                    re.compile(rx)
                except re.error as e:
                    errors.append(f"feedback-patterns.json: {code}/{kind}: bad regex {rx!r} ({e})")
    if len(langs) < 15:
        warnings.append(f"feedback-patterns.json: {len(langs)} languages (README promises 15)")


def check_python() -> None:
    for f in sorted(ROOT.rglob("*.py")):
        if ".git" in f.parts:
            continue
        try:
            compile(f.read_text(encoding="utf-8"), str(f), "exec")
        except SyntaxError as e:
            errors.append(f"{f.relative_to(ROOT)}:{e.lineno}: {e.msg}")


def main() -> int:
    check_agents()
    check_skills()
    check_hooks_json()
    check_manifests()
    check_python()
    check_english_only()
    check_feedback_patterns()
    for w in warnings:
        print(f"WARN   {w}")
    for e in errors:
        print(f"ERROR  {e}")
    agents = len(list((ROOT / "agents").glob("*.md")))
    skills = len([p for p in (ROOT / "skills").iterdir() if p.is_dir()])
    print(f"{agents} agents, {skills} skills checked: {len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
