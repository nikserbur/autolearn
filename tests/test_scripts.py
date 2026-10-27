"""Behavioural tests for the scripts bundled with skills. Standard library only:

    python3 -m unittest discover -s tests -v
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SKILLS = ROOT / "skills"


class ScriptCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / "autolearn"
        self.kb = self.home / "knowledge"
        shutil.copytree(ROOT / "docs" / "examples" / "knowledge", self.kb)
        shutil.copy(ROOT / "templates" / "knowledge" / "required-docs.md", self.kb)
        shutil.copytree(ROOT / "docs" / "examples" / "lessons", self.home / "lessons")

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, rel, *args, expect=0):
        env = dict(os.environ, AUTOLEARN_HOME=str(self.home))
        proc = subprocess.run([sys.executable, str(SKILLS / rel), *args], capture_output=True,
                              text=True, env=env, timeout=60, cwd=self.tmp.name)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return proc.stdout


class KbRoute(ScriptCase):
    def test_routes_keywords_to_sections_and_logs_hit(self):
        out = self.run_script("kb-router/scripts/kb_route.py", "hotfix", "tag")
        self.assertIn("## Example: release process", out)
        self.assertIn("topics/release-process.md#how-to: lines", out)
        self.assertIn("sed -n", out)
        log = (self.kb / ".router-log.tsv").read_text()
        self.assertIn("\thit\n", log)

    def test_miss_lists_headings_and_logs_miss(self):
        out = self.run_script("kb-router/scripts/kb_route.py", "kubernetes")
        self.assertIn("No topic matches", out)
        self.assertIn("\tmiss\n", (self.kb / ".router-log.tsv").read_text())

    def test_budget_marks_oversized_sections(self):
        out = self.run_script("kb-router/scripts/kb_route.py", "--budget", "50", "--no-log", "release")
        self.assertIn("over budget -> delegate", out)
        self.assertFalse((self.kb / ".router-log.tsv").exists())


class KbLint(ScriptCase):
    def test_examples_are_clean(self):
        out = self.run_script("kb-ingest/scripts/kb_lint.py")
        self.assertIn("0 errors", out)

    def test_seeded_skeleton_is_clean(self):
        shutil.rmtree(self.kb)
        shutil.copytree(ROOT / "templates" / "knowledge", self.kb)
        self.assertIn("0 errors", self.run_script("kb-ingest/scripts/kb_lint.py"))

    def test_unroutable_page_and_broken_pointer_fail(self):
        (self.kb / "topics" / "orphan.md").write_text("# Orphan\n")
        with open(self.kb / "topic-map.md", "a") as f:
            f.write("\n## Broken\nkeywords: x\nread first: topics/missing.md · topics/release-process.md#nope\n")
        out = self.run_script("kb-ingest/scripts/kb_lint.py", expect=1)
        self.assertIn("orphan.md: no topic block points here", out)
        self.assertIn("topics/missing.md' -> missing file", out)
        self.assertIn("#nope' -> no such section", out)


class Lessons(ScriptCase):
    def test_reviewers_match_globs_and_keywords(self):
        patch = pathlib.Path(self.tmp.name) / "round-1.patch"
        patch.write_text("diff --git a/src/reports/daily.py b/src/reports/daily.py\n"
                         "+++ b/src/reports/daily.py\n+from datetime import datetime\n")
        out = self.run_script("learn/scripts/lessons.py", "reviewers", str(patch), "--repo", "example-repo")
        self.assertIn("al-tz-reviewer", out)
        patch.write_text("diff --git a/docs/x.md b/docs/x.md\n+++ b/docs/x.md\n+hello\n")
        out = self.run_script("learn/scripts/lessons.py", "reviewers", str(patch), "--repo", "example-repo")
        self.assertIn("no extra reviewers match", out)

    def test_signal_inbox_advance_cycle(self):
        self.run_script("learn/scripts/lessons.py", "signal", "merge_gate", "missing test for null input",
                        "--project", "api")
        out = self.run_script("learn/scripts/lessons.py", "inbox")
        self.assertIn("[api] merge_gate: missing test for null input", out)
        self.assertIn("1 new of 1 total", out)
        self.run_script("learn/scripts/lessons.py", "advance", "end")
        self.assertIn("0 new of 1 total", self.run_script("learn/scripts/lessons.py", "inbox"))

    def test_proposals_filter_by_status(self):
        d = self.home / "proposals"
        d.mkdir()
        (d / "2026-01-20-tz.md").write_text("# al-tz-reviewer\nStatus: pending\nRepo: example-repo\n")
        (d / "2026-01-02-old.md").write_text("# old idea\nStatus: rejected - noisy\n")
        out = self.run_script("learn/scripts/lessons.py", "proposals", "--status", "pending")
        self.assertIn("al-tz-reviewer", out)
        self.assertNotIn("old idea", out)

    def test_stats_hints(self):
        header = ("date\trepo\tslug\tmode\ttasks\tcritic_rounds\tcritic_blocking\treview_rounds\t"
                  "findings_confirmed\tfindings_refuted\tgate_blocks\tmerge_gate_findings\toutcome\n")
        row = "2026-01-{d:02d}\tapi\ts{d}\tlight\t2\t1\t0\t1\t0\t2\t1\t1\tok\n"
        (self.home / "lessons" / "runs.tsv").write_text(header + "".join(row.format(d=d) for d in range(1, 6)))
        out = self.run_script("learn/scripts/lessons.py", "stats", "--repo", "api")
        self.assertIn("effort: low", out)
        self.assertIn("tighten the code-reviewer", out)
        self.assertIn("/retro", out)


class Radar(ScriptCase):
    def test_scan_sessions_counts_and_filters(self):
        proj = pathlib.Path(self.tmp.name) / "projects" / "-home-me-api"
        proj.mkdir(parents=True)
        lines = []
        for i in range(3):
            lines += [
                {"type": "user", "message": {"content": "please draft the weekly report from tickets"}},
                {"type": "user", "message": {"content": "This session is being continued from a previous one"}},
                {"type": "assistant", "message": {"content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "uv run pytest -q"}},
                    {"type": "tool_use", "id": f"g{i}", "name": "Bash", "input": {"command": "grep -rn x ."}},
                    {"type": "tool_use", "id": f"s{i}", "name": "Skill", "input": {"skill": "feature"}}]}},
                {"type": "user", "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True, "content": "boom"}]}},
            ]
        for i in range(3):
            (proj / f"s{i}.jsonl").write_text("\n".join(json.dumps(l) for l in lines[i * 4:(i + 1) * 4]))
        out = self.run_script("skill-radar/scripts/scan_sessions.py", "--projects-dir",
                              str(proj.parent), "--days", "1")
        self.assertIn("3 transcripts across 1 projects", out)
        self.assertIn("uv run", out)
        self.assertNotIn("grep", out.split("## Tools and MCP")[0])
        self.assertIn("please draft the weekly report", out)
        self.assertNotIn("this session is being", out)
        self.assertRegex(out, r"3\s+feature")

    def test_inventory_never_prints_mcp_config(self):
        claude_dir = pathlib.Path(self.tmp.name) / ".claude"
        (claude_dir / "skills" / "demo").mkdir(parents=True)
        (claude_dir / "skills" / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: >\n  Does demo things.\n---\n")
        secret = "tok" + "en-value-123456"
        (claude_dir.parent / ".claude.json").write_text(json.dumps(
            {"mcpServers": {"tracker": {"env": {"API": secret}}}, "skillUsage": {"demo": {"usageCount": 4}}}))
        out = self.run_script("skill-radar/scripts/inventory.py", "--claude-dir", str(claude_dir),
                              "--project", self.tmp.name)
        self.assertIn("- demo (used 4x): Does demo things.", out)
        self.assertIn("- tracker", out)
        self.assertNotIn(secret, out)


if __name__ == "__main__":
    unittest.main()
