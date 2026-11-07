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
        self.home = pathlib.Path(self.tmp.name) / "research-lab"
        self.kb = self.home / "knowledge"
        shutil.copytree(ROOT / "docs" / "examples" / "knowledge", self.kb)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, rel, *args, expect=0):
        env = dict(os.environ, RESEARCH_LAB_HOME=str(self.home), RESEARCH_LAB_EMBED="hash")
        proc = subprocess.run([sys.executable, str(SKILLS / rel), *args], capture_output=True,
                              text=True, env=env, timeout=60, cwd=self.tmp.name)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return proc.stdout


class KbRoute(ScriptCase):
    def test_routes_keywords_to_sections_and_logs_hit(self):
        out = self.run_script("knowledge-search/scripts/kb_route.py", "hotfix", "tag")
        self.assertIn("## Example: release process", out)
        self.assertIn("topics/release-process.md#how-to: lines", out)
        self.assertIn("sed -n", out)
        log = (self.kb / ".router-log.tsv").read_text()
        self.assertIn("\thit\n", log)

    def test_miss_lists_headings_and_logs_miss(self):
        out = self.run_script("knowledge-search/scripts/kb_route.py", "kubernetes")
        self.assertIn("No topic matches", out)
        self.assertIn("\tmiss\n", (self.kb / ".router-log.tsv").read_text())

    def test_budget_marks_oversized_sections(self):
        out = self.run_script("knowledge-search/scripts/kb_route.py", "--budget", "50", "--no-log", "release")
        self.assertIn("over budget -> delegate", out)
        self.assertFalse((self.kb / ".router-log.tsv").exists())


class KbRag(ScriptCase):
    def test_search_returns_chunks_with_lines_and_logs_hit(self):
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "hotfix", "tag")
        self.assertIn("=== topics/release-process.md:", out)
        self.assertIn("Hotfixes branch from the last tag", out)
        self.assertTrue((self.kb / ".rag" / "meta.json").exists())
        self.assertTrue((self.kb / ".rag" / "vectors.f32").exists())
        self.assertIn("\thit\n", (self.kb / ".router-log.tsv").read_text())

    def test_extra_root_indexes_journal_rows(self):
        res = pathlib.Path(self.tmp.name) / "research" / "KB"
        res.mkdir(parents=True)
        (res / "EXPERIMENTS.tsv").write_text(
            "id\thypothesis\tdecision\nE01\tqueue imbalance features\tKEEP\n"
            "E02\tlonger training window\tDISCARD\n")
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "--root", str(res), "training", "window")
        self.assertIn("EXPERIMENTS.tsv:3-3", out)
        self.assertIn("DISCARD", out)
        self.assertNotIn("queue imbalance", out)

    def test_index_refreshes_changed_files(self):
        self.run_script("knowledge-search/scripts/kb_rag.py", "index")
        (self.kb / "topics" / "new.md").write_text("# Flaky tests\n\n## Facts\n- Retry quarantine lives in ci/flaky.yml\n")
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "--no-log", "quarantine")
        self.assertIn("topics/new.md:3-4", out)

    def test_vectors_find_paraphrase_without_shared_words(self):
        (self.kb / "topics" / "deploys.md").write_text(
            "# Deploys\n\n## Rollback\n- Rollbacks redeploy the previous container image.\n")
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "--no-log", "rolling", "redeployment")
        self.assertIn("topics/deploys.md:3-4", out)
        self.assertIn("cos", out)

    def test_status_reports_backend_and_store_survives_other_roots(self):
        res = pathlib.Path(self.tmp.name) / "old-project" / "KB"
        res.mkdir(parents=True)
        (res / "KNOWLEDGE.md").write_text("# Knowledge\n\n## Fill model\n- Back of queue fills are toxic.\n")
        self.run_script("knowledge-search/scripts/kb_rag.py", "index", "--root", str(res))
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "--no-log", "toxic", "fills")
        self.assertNotIn("KNOWLEDGE.md", out)  # a research KB stays out of the background search
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "--no-log", "--root", str(res), "toxic", "fills")
        self.assertIn("KNOWLEDGE.md:3-4", out)
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "--no-log", "--everywhere", "toxic", "fills")
        self.assertIn("KNOWLEDGE.md:3-4", out)
        self.assertIn("backend hash-512", self.run_script("knowledge-search/scripts/kb_rag.py", "status"))

    def test_identifier_in_query_wins(self):
        (self.kb / "topics" / "runs.md").write_text(
            "# Runs\n\n## Round 3\n- E99 queue model adds 0.02 per lot.\n\n"
            "## Experiment protocol\n- Every experiment is registered before the run.\n")
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "--no-log", "--k", "1",
                              "what", "did", "experiment", "E99", "give")
        self.assertIn("topics/runs.md:3-5", out)

    def test_model_store_is_never_downgraded(self):
        self.run_script("knowledge-search/scripts/kb_rag.py", "index")
        meta_path = self.kb / ".rag" / "meta.json"
        meta = json.loads(meta_path.read_text())
        meta["backend"] = "fastembed:some/model"
        meta_path.write_text(json.dumps(meta))
        env_backup = os.environ.pop("RESEARCH_LAB_EMBED", None)
        try:
            env = dict(os.environ, RESEARCH_LAB_HOME=str(self.home))
            proc = subprocess.run([sys.executable, str(SKILLS / "knowledge-search/scripts/kb_rag.py"), "search",
                                   "--no-log", "hotfix"], capture_output=True, text=True, env=env, timeout=60)
        finally:
            if env_backup is not None:
                os.environ["RESEARCH_LAB_EMBED"] = env_backup
        self.assertIn("words only", proc.stdout)
        self.assertIn("release-process.md", proc.stdout)
        self.assertEqual(json.loads(meta_path.read_text())["backend"], "fastembed:some/model")

    def test_miss_and_budget(self):
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "kubernetes")
        self.assertIn("No chunk matches", out)
        self.assertIn("\tmiss\n", (self.kb / ".router-log.tsv").read_text())
        out = self.run_script("knowledge-search/scripts/kb_rag.py", "search", "--no-log", "--budget", "50", "release")
        self.assertIn("over budget", out)


class KbLint(ScriptCase):
    def test_examples_are_clean(self):
        out = self.run_script("knowledge-ingest/scripts/kb_lint.py")
        self.assertIn("0 errors", out)

    def test_seeded_skeleton_is_clean(self):
        shutil.rmtree(self.kb)
        shutil.copytree(ROOT / "templates" / "knowledge", self.kb)
        self.assertIn("0 errors", self.run_script("knowledge-ingest/scripts/kb_lint.py"))

    def test_old_numbers_are_flagged_for_a_recheck(self):
        page = self.kb / "topics" / "release-process.md"
        lines = page.read_text().splitlines() + [
            "", "## Fees", "- Exchange fee is 0.35 per lot (src: https://x.example/fees, read 2020-01-02 ✅)",
            "- Releases go out on Tuesdays (src: CONTRIBUTING.md, read 2020-01-02 ✅)",
            "- Spread is 0.25 (src: https://x.example/spread, read 2099-01-01 ✅)"]
        page.write_text("\n".join(lines) + "\n")
        out = self.run_script("knowledge-ingest/scripts/kb_lint.py")
        self.assertIn(f"release-process.md:{len(lines) - 2}: a number read", out)
        self.assertEqual(out.count("a number read"), 1)
        lines += ["- OAuth2 tokens expire (src: x, read 2020-01-02 ✅)",
                  "- Margin is 12 (src: x, read 2099-01-01, read 2020-01-02 ✅)"]
        page.write_bytes(("\n".join(lines) + "\n").encode("utf-8") + "- Caf\xe9 fee 3 (src: x, read 2020-01-02)\n".encode("latin-1"))
        out = self.run_script("knowledge-ingest/scripts/kb_lint.py")
        self.assertNotIn(f"release-process.md:{len(lines) - 1}:", out)
        self.assertIn(f"release-process.md:{len(lines)}: a number read", out)
        self.assertIn(f"release-process.md:{len(lines) + 1}: a number read", out)

    def test_unroutable_page_and_broken_pointer_fail(self):
        (self.kb / "topics" / "orphan.md").write_text("# Orphan\n")
        with open(self.kb / "topic-map.md", "a") as f:
            f.write("\n## Broken\nkeywords: x\nread first: topics/missing.md · topics/release-process.md#nope\n")
        out = self.run_script("knowledge-ingest/scripts/kb_lint.py", expect=1)
        self.assertIn("orphan.md: no topic block points here", out)
        self.assertIn("topics/missing.md' -> missing file", out)
        self.assertIn("#nope' -> no such section", out)


if __name__ == "__main__":
    unittest.main()
