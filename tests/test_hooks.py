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


class VerifyGate(HookCase):
    def write_task(self, verify, max_blocks=2, created=None):
        import time
        d = self.home / "runs" / self.repo.name
        d.mkdir(parents=True, exist_ok=True)
        (d / ".current-task.json").write_text(json.dumps(
            {"task": "Task 1", "verify": verify, "max_blocks": max_blocks, "timeout": 30,
             "created": created if created is not None else time.time()}))
        return d / ".current-task.state.json"

    def stop(self, *args, agent="al-implementer", message="done"):
        return self.run_hook("verify_gate.py", {"agent_type": agent, "cwd": str(self.repo),
                                                "hook_event_name": "SubagentStop",
                                                "last_assistant_message": message}, *args)

    def test_no_task_file_is_a_no_op(self):
        self.assertEqual(self.stop(), "")

    def test_blocks_on_failing_verify_then_releases(self):
        state = self.write_task("exit 1", max_blocks=1)
        first = self.stop()
        self.assertEqual(first["decision"], "block")
        self.assertIn("failure 1; you will be released after 2", first["reason"])
        second = self.stop()  # released: no decision, no additionalContext (which would continue the agent)
        self.assertNotIn("decision", second)
        self.assertNotIn("hookSpecificOutput", second)
        self.assertIn("released as FAILED", second["systemMessage"])
        st = json.loads(state.read_text())
        self.assertFalse(st["passed"])
        self.assertEqual(st["released"], "max_blocks")

    def test_passes_on_green_verify(self):
        state = self.write_task("true")
        self.assertEqual(self.stop(), "")
        self.assertTrue(json.loads(state.read_text())["passed"])

    def test_blocked_and_failed_status_are_respected(self):
        state = self.write_task("exit 1")
        self.assertEqual(self.stop(message="STATUS: BLOCKED - needs a schema change"), "")
        self.assertEqual(json.loads(state.read_text())["released"], "blocked")
        self.assertEqual(self.stop(message="STATUS: FAILED - out of ideas"), "")
        self.assertFalse(json.loads(state.read_text())["passed"])

    def test_stale_task_file_is_ignored(self):
        self.write_task("exit 1", created=1)
        self.assertEqual(self.stop(), "")
        self.write_task("exit 1", created=1000)  # milliseconds since epoch are stale too
        self.assertEqual(self.stop(), "")

    def test_bad_created_value_falls_back_to_mtime(self):
        self.write_task("exit 1", created="2026-10-27T10:00:00")
        self.assertEqual(self.stop()["decision"], "block")

    def test_runs_without_login_shell(self):
        self.write_task("shopt -q login_shell && exit 1 || exit 0")  # bash -lc would fail this
        self.assertEqual(self.stop(), "")

    def test_global_scope_ignores_other_agents(self):
        self.write_task("exit 1")
        self.assertEqual(self.stop(agent="al-code-reviewer"), "")

    def test_same_name_from_another_plugin_is_not_gated(self):
        self.write_task("exit 1")
        self.assertEqual(self.stop(agent="other-plugin:al-implementer"), "")

    def test_namespaced_plugin_agent_is_gated(self):
        self.write_task("exit 1")
        self.assertEqual(self.stop(agent="autolearn:al-implementer")["decision"], "block")

    def test_agent_scope_flag_gates_without_agent_type(self):
        self.write_task("exit 1")
        self.assertEqual(self.stop("--scope", "agent", agent="")["decision"], "block")


class TestOutputFilter(HookCase):
    def call(self, command, agent="al-implementer"):
        return self.run_hook("test_output_filter.py", {"tool_name": "Bash", "agent_type": agent,
                                                       "tool_input": {"command": command}})

    def test_suggests_filtered_command_for_test_runs(self):
        out = self.call("uv run pytest tests -q  # quick run")
        hs = out["hookSpecificOutput"]
        self.assertEqual(hs["permissionDecision"], "deny")
        suggested = hs["permissionDecisionReason"].split("Run exactly this instead:\n", 1)[1]
        self.assertIn("--filter", suggested)
        # valid shell even with the trailing comment; failures surface as a non-zero exit
        cmd = suggested.replace("uv run pytest tests -q", "printf 'FAILED t1\\n'; exit 3")
        proc = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("FAILED t1", proc.stdout)

    def test_plain_pipe_for_simple_commands_and_uv_options(self):
        out = self.call("uv run --with pytest pytest -q")
        suggested = out["hookSpecificOutput"]["permissionDecisionReason"].split("instead:\n", 1)[1]
        self.assertTrue(suggested.startswith("uv run --with pytest pytest -q 2>&1 | python3 "))

    def test_filter_exit_code_reflects_failures(self):
        def run(text):
            return subprocess.run([sys.executable, str(HOOKS / "test_output_filter.py"), "--filter"],
                                  input=text, capture_output=True, text=True).returncode
        self.assertEqual(run("== 1 failed, 3 passed =="), 1)
        self.assertEqual(run("test result: ok. 10 passed; 0 failed; 0 ignored"), 0)
        self.assertEqual(run("== 3 passed in 0.1s =="), 0)

    def test_already_filtered_and_non_test_commands_pass(self):
        self.assertEqual(self.call("pytest -q 2>&1 | python3 hooks/test_output_filter.py --filter"), "")
        self.assertEqual(self.call("grep -rn pytest ."), "")
        self.assertEqual(self.call("cat tests/test_x.py"), "")

    def test_leaves_main_session_alone(self):
        out = self.run_hook("test_output_filter.py", {
            "tool_name": "Bash", "tool_input": {"command": "pytest -q"}})
        self.assertEqual(out, "")

    def test_filter_keeps_failures_and_summary(self):
        noise = "\n".join(f"tests/test_a.py::test_{i} PASSED" for i in range(500))
        text = noise + "\nFAILED tests/test_b.py::test_x - AssertionError\n== 1 failed, 500 passed =="
        out = self.run_hook("test_output_filter.py", {}, "--filter", stdin=text, expect=1)
        self.assertIn("FAILED tests/test_b.py::test_x", out)
        self.assertIn("1 failed, 500 passed", out)
        self.assertLess(len(out.splitlines()), 140)


class PlannerGuard(HookCase):
    def call(self, command, agent="al-planner", agent_id="a1", timeout=60000):
        ti = {"command": command}
        if timeout:
            ti["timeout"] = timeout
        return self.run_hook("planner_guard.py", {"tool_name": "Bash", "agent_type": agent,
                                                  "agent_id": agent_id, "tool_input": ti})

    def test_denies_network(self):
        out = self.call("curl https://example.com")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_reading_code_is_unlimited(self):
        for cmd in ["grep -rn foo src/", "sed -n 1,80p src/aws/client.py", 'grep -rn "requests.get" .',
                    "cat infra/terraform/main.tf", "ls src/az", "grep -rn node ."]:
            self.assertEqual(self.call(cmd, timeout=None), "", cmd)

    def test_python_one_liner_with_network_is_denied(self):
        for cmd in ('python3 -c "import requests; requests.get(1)"',
                    "python3 -c 'import os; import requests'",
                    "uv run python -c 'import httpx'",
                    "python3 - <<EOF\nimport requests\nEOF",
                    "python3 -m pip install x",
                    "git -C . fetch"):
            out = self.call(cmd)
            self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny", cmd)

    def test_experiments_need_a_timeout(self):
        out = self.call("python3 bench.py", timeout=None)
        self.assertIn("timeout parameter", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_experiment_budget(self):
        for i in range(3):
            self.assertEqual(self.call("python3 bench.py"), "")
        out = self.call("python3 bench.py")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_other_agents_untouched(self):
        self.assertEqual(self.call("curl https://example.com", agent="al-implementer"), "")
        self.assertEqual(self.call("curl https://example.com", agent="other-plugin:al-planner"), "")


class CaptureFeedback(HookCase):
    def submit(self, prompt):
        self.run_hook("capture_feedback.py", {"prompt": prompt, "session_id": "s1",
                                              "cwd": str(self.repo)})
        f = self.home / "inbox" / "feedback.jsonl"
        return [json.loads(l) for l in f.read_text().splitlines()] if f.exists() else []

    def test_correction_and_rule(self):
        rec = self.submit("No, I said use uv. From now on never call pip directly.")[0]
        self.assertIn("correction", rec["kinds"])
        self.assertIn("rule", rec["kinds"])
        self.assertEqual(rec["project"], self.repo.name)

    def test_escaped_bug_english(self):
        rec = self.submit("the reviewer missed it, bug in prod because of a nil map")[0]
        self.assertIn("escaped_bug", rec["kinds"])

    def test_fifteen_languages_examples(self):
        """Every example triggers its kind with all languages loaded; neutral ones trigger nothing."""
        src = HOOKS.parent / "templates" / "feedback-patterns.json"
        shutil.copy(src, self.home / "feedback-patterns.json")
        langs = json.loads(src.read_text(encoding="utf-8"))["languages"]
        self.assertEqual(len(langs), 15)
        script = ("import json, sys; sys.path.insert(0, sys.argv[1]); import capture_feedback as c; "
                  "ex = json.load(open(sys.argv[2], encoding='utf-8'))['languages']; "
                  "print(json.dumps({k: {kind: c.classify(t) for kind, t in v['examples'].items()} for k, v in ex.items()}))")
        env = dict(os.environ, AUTOLEARN_HOME=str(self.home))
        out = subprocess.run([sys.executable, "-c", script, str(HOOKS), str(src)], capture_output=True,
                             text=True, env=env, check=True).stdout
        for code, kinds in json.loads(out).items():
            for kind, found in kinds.items():
                if kind == "neutral":
                    self.assertEqual(found, [], f"{code} neutral example triggered {found}")
                else:
                    self.assertIn(kind, found, f"{code} {kind} example not recognised (got {found})")

    def test_enabled_limits_languages(self):
        data = json.loads((HOOKS.parent / "templates" / "feedback-patterns.json").read_text(encoding="utf-8"))
        data["enabled"] = ["de"]
        (self.home / "feedback-patterns.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        self.assertIn("correction", self.submit(data["languages"]["de"]["examples"]["correction"])[-1]["kinds"])
        self.assertEqual(len(self.submit(data["languages"]["fr"]["examples"]["rule"])), 1)  # French off: nothing new

    def test_local_language_patterns(self):
        (self.home / "feedback-patterns.json").write_text(json.dumps(
            {"correction": [r"^\s*nein\b"], "rule": [r"\bimmer\b", "([broken"]}))
        rec = self.submit("Nein, bitte immer gofmt laufen lassen")[0]
        self.assertIn("correction", rec["kinds"])
        self.assertIn("rule", rec["kinds"])

    def test_neutral_prompt_not_recorded(self):
        self.assertEqual(self.submit("Add a CSV export to the report page"), [])
        self.assertEqual(self.submit("Fit a linear regression on the sales data"), [])
        self.assertEqual(self.submit("No problem, never mind the colors"), [])

    def test_does_not_store_full_path(self):
        rec = self.submit("No, I said use uv")[0]
        self.assertNotIn("cwd", rec)

    def test_slash_commands_skipped(self):
        self.assertEqual(self.submit("/retro never again"), [])

    def test_secrets_redacted(self):
        key = "AKIA" + "ABCDEFGHIJKLMNOP"
        pw = "Sup3r" + "Secret99"
        url_pw = "S3cret" + "Pass"
        bearer = "eyJhbGciOiJIUzI1NiJ9" + ".eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghij"
        rec = self.submit(f"no, wrong: {key} password={pw} postgres://admin:{url_pw}@db/x Bearer {bearer}")[0]
        for secret in (key, pw, url_pw, bearer):
            self.assertNotIn(secret, rec["excerpt"])

    def test_prefixed_env_secrets_redacted(self):
        vals = ["Db" + "Pass" + "word1", "ghx" + "TokenValue9", "sk" + "StripeValue7", "My" + "Secret" + "Val", "pa/ss" + "word1"]
        rec = self.submit(f"no, wrong: DB_PASSWORD={vals[0]} GITHUB_TOKEN={vals[1]} STRIPE_API_KEY={vals[2]} "
                          f"MY_SECRET={vals[3]} https://u:{vals[4]}@host/x")[0]
        for v in vals:
            self.assertNotIn(v, rec["excerpt"])


class SessionBrief(HookCase):
    def test_brief_counts_and_pointers(self):
        (self.home / "lessons").mkdir()
        (self.home / "lessons" / "pipeline.md").write_text(
            "# Pipeline lessons\n\n## Active pipeline rules\n1. a\n2. b\n\n## Prompt proposals\n- x\n")
        (self.home / "lessons" / f"{self.repo.name}.md").write_text(
            "## Project lessons\n- one\n- two\n\n## Extra reviewers\n"
            "| Agent | Trigger | Added | Evidence |\n|---|---|---|---|\n| null-reviewer | src/** | 2026-01-01 | x |\n")
        (self.home / "inbox").mkdir()
        (self.home / "inbox" / "feedback.jsonl").write_text("{}\n{}\n{}\n")
        (self.home / "inbox" / ".cursor").write_text("1")
        out = self.run_hook("session_brief.py", {"cwd": str(self.repo)})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("2 active pipeline rules", ctx)
        self.assertIn("2 lessons, 1 extra reviewer", ctx)
        self.assertIn("2 new signals", ctx)
        self.assertIn("never run", ctx)
        self.assertNotIn("No knowledge page", ctx)  # demo-repo is not a git repo
        self.assertLess(len(ctx), 900)

    def test_suggests_knowledge_page_for_unknown_git_repo(self):
        (self.repo / ".git").mkdir()
        ctx = self.run_hook("session_brief.py", {"cwd": str(self.repo)})["hookSpecificOutput"]["additionalContext"]
        self.assertIn(f"No knowledge page for {self.repo.name}", ctx)
        (self.home / ".brief-state.json").unlink()
        (self.home / "knowledge" / "projects").mkdir(parents=True)
        (self.home / "knowledge" / "projects" / f"{self.repo.name}.md").write_text("# page")
        ctx = self.run_hook("session_brief.py", {"cwd": str(self.repo)})["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("No knowledge page", ctx)

    def test_silent_without_data_dir(self):
        self.home.rmdir()
        self.assertEqual(self.run_hook("session_brief.py", {}), "")

    def test_plugin_mode_seeds_data_dir(self):
        self.home.rmdir()
        templates = HOOKS.parent / "templates"
        out = self.run_hook("session_brief.py", {"cwd": str(self.repo)}, "--seed", str(templates))
        self.assertIn("created its data directory", out["hookSpecificOutput"]["additionalContext"])
        for rel in ("knowledge/topic-map.md", "knowledge/required-docs.md", "lessons/pipeline.md",
                    "lessons/runs.tsv", "style/messages.md", ".gitignore"):
            self.assertTrue((self.home / rel).exists(), rel)
        ctx = self.run_hook("session_brief.py", {"cwd": str(self.repo)})["hookSpecificOutput"]["additionalContext"]
        self.assertNotIn("active pipeline rules", ctx)  # skeleton templates carry no live rules

    def test_seeds_even_if_another_hook_created_the_dir_first(self):
        self.home.rmdir()
        self.run_hook("capture_feedback.py", {"prompt": "no, always use uv here", "cwd": str(self.repo)})
        self.assertTrue((self.home / "inbox").exists())
        self.run_hook("session_brief.py", {"cwd": str(self.repo)}, "--seed", str(HOOKS.parent / "templates"))
        self.assertTrue((self.home / "lessons" / "pipeline.md").exists())
        self.assertTrue((self.home / "knowledge" / "topic-map.md").exists())

    def test_suggestions_are_throttled(self):
        first = self.run_hook("session_brief.py", {"cwd": str(self.repo)})["hookSpecificOutput"]["additionalContext"]
        self.assertIn("never run", first)
        self.assertEqual(self.run_hook("session_brief.py", {"cwd": str(self.repo)}), "")


if __name__ == "__main__":
    unittest.main()
