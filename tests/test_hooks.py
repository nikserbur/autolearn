"""Behavioural tests for hooks/*.py. Standard library only:

    python3 -m unittest discover -s tests -v

Every hook runs as a subprocess with a JSON payload on stdin, exactly as Claude Code calls it,
against a throwaway $AUTOLEARN_HOME. Secret-looking strings are assembled at runtime so this
file never trips a secret scanner itself.
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

HOOKS = pathlib.Path(__file__).resolve().parent.parent / "hooks"


class HookCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = pathlib.Path(self.tmp.name) / "autolearn"
        self.home.mkdir()
        self.repo = pathlib.Path(self.tmp.name) / "demo-repo"
        self.repo.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def run_hook(self, name, payload, *args, stdin=None, expect=0):
        env = dict(os.environ, AUTOLEARN_HOME=str(self.home), CLAUDE_PROJECT_DIR=str(self.repo))
        proc = subprocess.run(
            [sys.executable, str(HOOKS / name), *args],
            input=stdin if stdin is not None else json.dumps(payload),
            capture_output=True, text=True, env=env, timeout=60,
        )
        self.assertEqual(proc.returncode, expect, proc.stderr)
        return json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else proc.stdout


class CheckSecrets(HookCase):
    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def commit_payload(self):
        return {"tool_name": "Bash", "cwd": str(self.repo),
                "tool_input": {"command": f"cd {self.repo} && git commit -m wip"}}

    def test_denies_staged_aws_key(self):
        self.git("init", "-q")
        (self.repo / "cfg.py").write_text("KEY = '" + "AKIA" + "ABCDEFGHIJKLMNOP" + "'\n")
        self.git("add", "cfg.py")
        out = self.run_hook("check_secrets.py", self.commit_payload())
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("AWS access key id", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_allows_clean_commit(self):
        self.git("init", "-q")
        (self.repo / "ok.py").write_text("print('hello')\n")
        self.git("add", "ok.py")
        self.assertEqual(self.run_hook("check_secrets.py", self.commit_payload()), "")

    def test_add_and_commit_in_one_command(self):
        self.git("init", "-q")
        (self.repo / "new.py").write_text("KEY = '" + "AKIA" + "ABCDEFGHIJKLMNOP" + "'\n")
        payload = {"tool_name": "Bash", "cwd": str(self.repo),
                   "tool_input": {"command": "git add new.py && git commit -m wip"}}
        self.assertEqual(self.run_hook("check_secrets.py", payload)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_commit_all_flag_and_newlines(self):
        self.git("init", "-q")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
        (self.repo / "cfg.py").write_text("x = 1\n")
        self.git("add", "cfg.py")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
        (self.repo / "cfg.py").write_text("x = 1\ntoken = '" + "abcdefgh" + "12345678'\n")
        payload = {"tool_name": "Bash", "cwd": str(self.repo),
                   "tool_input": {"command": f"cd {self.repo}\ngit commit -am 'tweak'"}}
        self.assertEqual(self.run_hook("check_secrets.py", payload)["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_git_global_options_and_relative_cd(self):
        self.git("init", "-q")
        sub = self.repo / "sub"
        sub.mkdir()
        (self.repo / "k.py").write_text("KEY = '" + "AKIA" + "ABCDEFGHIJKLMNOP" + "'\n")
        self.git("add", "k.py")
        for cmd in (f"git -c user.name=x --no-pager -C {self.repo} commit -m wip",
                    f"cd {sub} && cd .. && git commit -m wip"):
            out = self.run_hook("check_secrets.py", {"tool_name": "Bash", "cwd": "/",
                                                     "tool_input": {"command": cmd}})
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", cmd)

    def test_ignores_non_commit_commands(self):
        out = self.run_hook("check_secrets.py", {"tool_name": "Bash", "tool_input": {"command": "ls"}})
        self.assertEqual(out, "")


class SessionBrief(HookCase):
    def test_brief_counts_and_pointers(self):
        (self.home / "knowledge").mkdir()
        (self.home / "knowledge" / "topic-map.md").write_text(
            "# Topic map\n\n## Releases\nkeywords: release\n\n## Fill model\nkeywords: fills\n")
        out = self.run_hook("session_brief.py", {"cwd": str(self.repo)})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Knowledge base: 2 topics", ctx)
        self.assertNotIn("No knowledge page", ctx)  # demo-repo is not a git repo
        self.assertLess(len(ctx), 700)

    def test_suggests_knowledge_page_for_unknown_git_repo(self):
        (self.repo / ".git").mkdir()
        ctx = self.run_hook("session_brief.py", {"cwd": str(self.repo)})["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"No knowledge page for {self.repo.name}", ctx)
        (self.home / ".brief-state.json").unlink()
        (self.home / "knowledge" / "projects").mkdir(parents=True)
        (self.home / "knowledge" / "projects" / f"{self.repo.name}.md").write_text("# page")
        out = self.run_hook("session_brief.py", {"cwd": str(self.repo)})
        self.assertNotIn("No knowledge page", json.dumps(out))

    def test_offers_embedding_model_when_store_uses_hashing(self):
        rag = self.home / "knowledge" / ".rag"
        rag.mkdir(parents=True)
        (rag / "meta.json").write_text(json.dumps({"backend": "hash-512", "files": {}}))
        ctx = self.run_hook("session_brief.py", {"cwd": str(self.repo)})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("kb_rag.py setup", ctx)
        (self.home / ".brief-state.json").unlink()
        (rag / "meta.json").write_text(json.dumps({"backend": "fastembed:m", "files": {}}))
        out = self.run_hook("session_brief.py", {"cwd": str(self.repo)})
        self.assertNotIn("fastembed", json.dumps(out))

    def test_silent_without_data_dir(self):
        self.home.rmdir()
        self.assertEqual(self.run_hook("session_brief.py", {}), "")

    def test_plugin_mode_seeds_data_dir(self):
        self.home.rmdir()
        templates = HOOKS.parent / "templates"
        out = self.run_hook("session_brief.py", {"cwd": str(self.repo)}, "--seed", str(templates))
        self.assertIn("created its data directory", out["hookSpecificOutput"]["additionalContext"])
        for rel in ("knowledge/topic-map.md", "knowledge/topics", "knowledge/projects", ".gitignore"):
            self.assertTrue((self.home / rel).exists(), rel)
        again = self.run_hook("session_brief.py", {"cwd": str(self.repo)}, "--seed", str(templates))
        self.assertNotIn("created its data directory", json.dumps(again))
        self.assertNotIn("Knowledge base:", json.dumps(again))  # the skeleton map has no topics yet

    def test_seeds_even_if_another_hook_created_the_dir_first(self):
        (self.home / ".brief-state.json").write_text("{}")
        self.run_hook("session_brief.py", {"cwd": str(self.repo)}, "--seed", str(HOOKS.parent / "templates"))
        self.assertTrue((self.home / "knowledge" / "topic-map.md").exists())

    def test_suggestions_are_throttled(self):
        (self.repo / ".git").mkdir()
        first = self.run_hook("session_brief.py", {"cwd": str(self.repo)})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("No knowledge page", first)
        self.assertEqual(self.run_hook("session_brief.py", {"cwd": str(self.repo)}), "")


class KbAutoindex(HookCase):
    def write_payload(self, path):
        return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}

    def run_sync(self, path):
        os.environ["AUTOLEARN_AUTOINDEX"] = "sync"
        os.environ["AUTOLEARN_EMBED"] = "hash"
        try:
            return self.run_hook("kb_autoindex.py", self.write_payload(path))
        finally:
            del os.environ["AUTOLEARN_AUTOINDEX"], os.environ["AUTOLEARN_EMBED"]

    def test_write_into_research_kb_is_embedded(self):
        kb = self.repo / "research" / "demo" / "KB"
        kb.mkdir(parents=True)
        page = kb / "KNOWLEDGE.md"
        page.write_text("# Knowledge\n\n## E01\n- Queue imbalance adds 0.01 corr.\n")
        self.assertEqual(self.run_sync(page), "")
        meta = json.loads((self.home / "knowledge" / ".rag" / "meta.json").read_text())
        self.assertIn(str(page.resolve()), meta["files"])

    def test_other_files_are_ignored(self):
        src = self.repo / "main.py"
        src.write_text("print(1)\n")
        self.run_sync(src)
        notes = self.repo / "notes.md"
        notes.write_text("# Notes\n")
        self.run_sync(notes)
        self.assertFalse((self.home / "knowledge" / ".rag").exists())


if __name__ == "__main__":
    unittest.main()
