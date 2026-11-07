"""install.sh and uninstall.sh in a sandbox, including the move from the project's old name (autolearn).

    python3 -m unittest discover -s tests -v
"""
import json
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("bash"), "needs bash")
class Install(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.claude = pathlib.Path(self.tmp.name) / "claude"
        self.claude.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def sh(self, script, *args):
        proc = subprocess.run(["bash", str(ROOT / script), "--claude-dir", str(self.claude), *args],
                              capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def put(self, rel, text):
        path = self.claude / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def old_install(self):
        """What an autolearn 0.3 install left behind, with one of the user's skills it had replaced."""
        old_hooks = self.claude / "hooks" / "autolearn"
        saved = self.put("backups/autolearn-1/skills/learn/SKILL.md", "mine").parent
        self.put("skills/learn/SKILL.md", "old learn")
        self.put("skills/research/SKILL.md", "old research")
        self.put("skills/kb-router/SKILL.md", "old router")
        self.put("agents/al-planner.md", "old planner")
        self.put("hooks/autolearn/session_brief.py", "")
        self.put("autolearn/knowledge/topic-map.md", "# Topic map\n\n## Fills\nkeywords: fills\n")
        self.put("autolearn/model-python", str(self.claude / "autolearn" / "venv" / "bin" / "python"))
        self.put("autolearn/.install-manifest", "\n".join([
            "# autolearn install manifest", f"restore\tskills/learn\t{saved}", "file\tskills/learn",
            "file\tskills/research", "file\tskills/kb-router", "file\tagents/al-planner.md",
            "file\thooks/autolearn", "settings", "claude-md"]) + "\n")
        self.put("settings.json", json.dumps({
            "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": f'python3 "{old_hooks}/session_brief.py"'}]}],
                      "Stop": [{"hooks": [{"type": "command", "command": "my-own-hook"}]}]},
            "permissions": {"allow": [f"Bash(python3 {old_hooks}/test_output_filter.py --filter)", "Bash(ls)"]}}))
        self.put("CLAUDE.md", "intro\n<!-- autolearn:start -->\nold table\n<!-- autolearn:end -->\nmine after\n")

    def test_round_trip(self):
        self.sh("install.sh", "--with-claude-md")
        self.sh("install.sh")  # idempotent
        self.assertTrue((self.claude / "skills" / "knowledge-search" / "scripts" / "kb_rag.py").exists())
        self.assertTrue((self.claude / "agents" / "rl-hypothesis-reviewer.md").exists())
        self.assertIn("hooks/research-lab/", (self.claude / "settings.json").read_text())
        self.sh("uninstall.sh")
        self.assertNotIn("hooks/research-lab/", (self.claude / "settings.json").read_text())
        self.assertNotIn("research-lab:start", (self.claude / "CLAUDE.md").read_text())
        self.assertFalse((self.claude / "skills" / "research").exists())

    def test_upgrade_from_autolearn_moves_data_and_clears_the_old_install(self):
        self.old_install()
        self.sh("install.sh")
        c = self.claude
        for gone in ("agents/al-planner.md", "skills/kb-router", "hooks/autolearn", "autolearn"):
            self.assertFalse((c / gone).exists(), gone)
        self.assertEqual((c / "skills" / "learn" / "SKILL.md").read_text(), "mine")
        self.assertNotEqual((c / "skills" / "research" / "SKILL.md").read_text(), "old research")
        home = c / "research-lab"
        self.assertIn("## Fills", (home / "knowledge" / "topic-map.md").read_text())
        self.assertEqual((home / "model-python").read_text(), str(home / "venv" / "bin" / "python"))
        settings = json.loads((c / "settings.json").read_text())
        commands = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
        self.assertFalse([x for x in commands if "hooks/autolearn/" in x])
        self.assertIn("my-own-hook", commands)
        self.assertEqual(settings["permissions"]["allow"], ["Bash(ls)"])
        md = (c / "CLAUDE.md").read_text()
        self.assertNotIn("autolearn:start", md)
        self.assertIn("research-lab:start", md)
        self.assertTrue(md.startswith("intro\n") and md.rstrip().endswith("mine after"))
        self.assertTrue(list((c / "backups").glob("research-lab-*/agents/al-planner.md")))
        self.sh("uninstall.sh")
        self.assertEqual((c / "skills" / "learn" / "SKILL.md").read_text(), "mine")
        self.assertNotIn("research-lab:start", (c / "CLAUDE.md").read_text())

    def test_plugin_mode_moves_the_old_data_directory(self):
        old = pathlib.Path(self.tmp.name) / "old"
        (old / "knowledge").mkdir(parents=True)
        (old / "knowledge" / "topic-map.md").write_text("# Topic map\n\n## Fills\nkeywords: fills\n")
        new = pathlib.Path(self.tmp.name) / "new"
        env = {"RESEARCH_LAB_HOME": str(new), "AUTOLEARN_HOME": str(old), "PATH": "/usr/bin:/bin",
               "HOME": self.tmp.name}
        proc = subprocess.run([sys.executable, str(ROOT / "hooks" / "session_brief.py"), "--seed", str(ROOT / "templates")],
                              input="{}", capture_output=True, text=True, env=env, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("moved the data directory", proc.stdout)
        self.assertFalse(old.exists())
        self.assertIn("## Fills", (new / "knowledge" / "topic-map.md").read_text())


if __name__ == "__main__":
    unittest.main()
