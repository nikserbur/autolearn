"""skills/research/scripts/lab.py: environment requirements, decision traces, samples, holdout looks, repro.

    python3 -m unittest discover -s tests -v
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
LAB = ROOT / "skills" / "research" / "scripts" / "lab.py"


class LabCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def lab(self, *args, expect=0):
        proc = subprocess.run([sys.executable, str(LAB), *args], capture_output=True, text=True,
                              cwd=self.dir, timeout=60)
        self.assertEqual(proc.returncode, expect, proc.stdout + proc.stderr)
        return proc.stdout

    def write(self, name, text):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return str(path)


class Env(LabCase):
    def test_template_is_unproven_until_every_row_has_evidence(self):
        template = (ROOT / "skills" / "research" / "ENVIRONMENT-TEMPLATE.md").read_text(encoding="utf-8")
        out = self.lab("env", self.write("ENVIRONMENT.md", template), expect=1)
        self.assertIn("S1", out)
        self.assertIn("V5", out)
        self.assertIn("0 of 12 requirements proven", out)

    def test_done_needs_evidence_and_na_needs_a_reason(self):
        table = ("| # | Requirement | Status | Evidence |\n|---|---|---|---|\n"
                 "| S1 | Point in time | ✅ | results/E0/trace check: 0 look-ahead rows |\n"
                 "| S2 | Costs | ✅ | |\n| S3 | Repro | n/a | |\n| S4 | Isolated | ❌ | |\n"
                 "| S5 | Controls | n/a | no model in this study |\n")
        out = self.lab("env", self.write("E.md", table), expect=1)
        self.assertIn("S2 Costs: marked done without evidence", out)
        self.assertIn("S3 Repro: n/a without a reason", out)
        self.assertIn("S4 Isolated: not met", out)
        self.assertIn("2 of 5 requirements proven", out)

    def test_all_proven_passes(self):
        table = "| # | Requirement | Status | Evidence |\n|---|---|---|---|\n| S1 | Point in time | ✅ | lab.py trace |\n"
        self.assertIn("1 of 1 requirements proven", self.lab("env", self.write("E.md", table)))


class Trace(LabCase):
    GOOD = ("ts\tstream\tstage\taction\tinputs\tinputs_ts\treason\toutcome\n"
            "2026-03-02T10:00:00\tES\tsignal\tlong\timbalance=0.4\t2026-03-02T09:59:59\tqueue thin\t\n"
            "2026-03-02T10:00:01\tES\torder\tbuy 1\tbook\t2026-03-02T10:00:00\tsignal\t\n"
            "2026-03-02T09:00:00\tNQ\tsignal\tflat\timbalance=0.1\t2026-03-02T08:59:59\tweak\t\n"
            "2026-03-02T10:00:02\tES\tfill\tbuy 1 @ 5000.25\tbook\t2026-03-02T10:00:01\t\t-0.25\n")

    def test_clean_trace_passes_and_counts_stages(self):
        out = self.lab("trace", self.write("trace.tsv", self.GOOD))
        self.assertIn("4 decisions; stages: signal 2", out)
        self.assertNotIn("ERROR", out)

    def test_look_ahead_and_time_going_back_fail(self):
        bad = self.GOOD + "2026-03-02T10:00:03\tES\texit\tsell 1\tclose\t2026-03-02T10:05:00\ttarget\t1.0\n" \
                          "2026-03-02T09:59:00\tES\tsignal\tlong\tx\t2026-03-02T09:58:00\tx\t\n"
        out = self.lab("trace", self.write("trace.tsv", bad), expect=1)
        self.assertIn("look-ahead: inputs newer than the decision on 1 rows (first: line 6)", out)
        self.assertIn("time goes back within a stream on 1 rows (first: line 7)", out)

    def test_missing_columns_and_blind_decisions(self):
        out = self.lab("trace", self.write("t.tsv", "ts\tstage\n1\tsignal\n"), expect=1)
        self.assertIn("no action column", out)
        out = self.lab("trace", self.write("t2.tsv", "ts\tstage\taction\tinputs\n1\tsignal\tlong\t\n"))
        self.assertIn("1 decisions do not say what they looked at", out)
        self.assertIn("no reason column", out)

    def test_jsonl_and_numeric_time(self):
        rows = [{"ts": 1, "stage": "predict", "action": "0.7", "inputs": "f1", "inputs_ts": 2}]
        out = self.lab("trace", self.write("t.jsonl", "\n".join(json.dumps(r) for r in rows)), expect=1)
        self.assertIn("look-ahead", out)

    def test_stages_compared_with_the_expectation(self):
        expect = ("# Expected behaviour: E7\n\n| Stage | Should look at | Should do |\n|---|---|---|\n"
                  "| signal | imbalance | go long when > 0.3 |\n| order | book | one lot |\n| exit | target | close |\n")
        out = self.lab("trace", self.write("trace.tsv", self.GOOD), "--expect", self.write("E7.md", expect))
        self.assertIn("stage 'exit' is expected but never in the trace", out)
        self.assertIn("stage 'fill' is in the trace but not in the expectation", out)


class Sample(LabCase):
    def test_best_worst_rare_and_random_without_repeats(self):
        rows = "ts\tstage\taction\toutcome\n" + "".join(f"{i}\tfill\tbuy\t{i - 10}\n" for i in range(20)) \
            + "21\texit\tforce-close\t-3\n"
        out = self.lab("sample", self.write("t.tsv", rows), "--n", "2").splitlines()
        self.assertEqual(out[0], "why\trow\tts\tstage\taction\toutcome")
        whys = [line.split("\t")[0] for line in out[1:]]
        self.assertEqual(whys, ["best", "best", "worst", "worst", "rare", "random", "random"])
        self.assertTrue(out[1].endswith("\t9") and out[3].endswith("\t-10"))
        self.assertIn("force-close", out[5])
        self.assertEqual(len({line.split("\t")[1] for line in out[1:]}), 7)
        self.assertEqual(out, self.lab("sample", str(self.dir / "t.tsv"), "--n", "2").splitlines())  # seeded


class Holdout(LabCase):
    def test_second_look_spoils_the_candidate(self):
        ledger = str(self.dir / "KB" / "HOLDOUT.tsv")
        out = self.lab("holdout", ledger, "log", "--candidate", "E12@abc123", "--result", "net +0.8 bp/trade\tPASS")
        self.assertIn("E12@abc123\t1 look(s)\tlast: net +0.8 bp/trade PASS\tok", out)
        out = self.lab("holdout", ledger, "log", "--candidate", "E12@abc123", "--result", "FAIL", expect=1)
        self.assertIn("SPOILED", out)
        self.assertIn("ok", self.lab("holdout", ledger, "status", "--budget", "2"))
        self.assertEqual(len((self.dir / "KB" / "HOLDOUT.tsv").read_text().splitlines()), 3)

    def test_empty_ledger(self):
        self.assertIn("no looks", self.lab("holdout", str(self.dir / "H.tsv"), "status"))


class Repro(LabCase):
    def test_same_and_different_outputs(self):
        same = self.write("same.py", "open('out.tsv', 'w').write('a\\t1\\n')\n")
        out = self.lab("repro", "--out", "out.tsv", "--", sys.executable, same)
        self.assertIn("same       out.tsv", out)
        noisy = self.write("noisy.py", "import random\nopen('out.tsv', 'w').write(str(random.random()))\n")
        self.assertIn("DIFFERENT  out.tsv", self.lab("repro", "--out", "out.tsv", "--", sys.executable, noisy, expect=1))

    def test_failing_command_and_missing_output(self):
        self.assertIn("failed", self.lab("repro", "--out", "x", "--", sys.executable, "-c", "raise SystemExit(3)", expect=1))
        self.assertIn("not written", self.lab("repro", "--out", "nothing.tsv", "--", sys.executable, "-c", "pass", expect=1))


if __name__ == "__main__":
    unittest.main()
