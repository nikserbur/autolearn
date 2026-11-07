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
TOY = ROOT / "tests" / "fixtures" / "research" / "toy_sim.py"


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
        out = self.lab("env", str(self.dir / "ENVIRONMENT.md"), "--stage", "sim", expect=1)
        self.assertIn("0 of 7 requirements proven", out)
        self.assertNotIn("V1", out)

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

    def test_self_reported_or_missing_inputs_ts_is_no_evidence(self):
        self.assertIn("inputs_ts equals ts on every row", self.lab("trace", self.write("t.tsv",
                      "ts\tstage\taction\tinputs\tinputs_ts\n1\tsignal\tlong\tx\t1\n")))
        self.assertIn("no inputs_ts column", self.lab("trace", self.write("t2.tsv", "ts\tstage\taction\n1\tsignal\tlong\n")))
        out = self.lab("trace", str(self.dir / "t2.tsv"), "--min-delay", "1", expect=1)
        self.assertIn("ERROR no inputs_ts column", out)
        out = self.lab("trace", str(self.dir / "t.tsv"), "--min-delay", "0.5", expect=1)
        self.assertIn("less than 0.5 s before the decision", out)

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

    def test_prices_do_not_make_every_fill_rare(self):
        rows = "ts\tstage\taction\toutcome\n" + "".join(f"{i}\tfill\tbuy 1 @ {5000 + i}.25\t\n" for i in range(10)) \
            + "11\tfill\tsell 1 @ 4999.75\t\n"
        whys = [line.split("\t")[0] for line in self.lab("sample", self.write("t.tsv", rows), "--n", "2").splitlines()[1:]]
        self.assertEqual(whys.count("rare"), 1)


class Repro(LabCase):
    def test_same_and_different_outputs(self):
        same = self.write("same.py", "open('out.tsv', 'w').write('a\\t1\\n')\n")
        out = self.lab("repro", "--out", "out.tsv", "--", sys.executable, same)
        self.assertIn("same       out.tsv", out)
        noisy = self.write("noisy.py", "import random\nopen('out.tsv', 'w').write(str(random.random()))\n")
        self.assertIn("DIFFERENT  out.tsv", self.lab("repro", "--out", "out.tsv", "--", sys.executable, noisy, expect=1))

    def test_a_file_left_from_before_is_not_a_reproduction(self):
        self.write("out.tsv", "old\n")
        skip = self.write("skip.py", "import os\nif not os.path.exists('out.tsv'):\n    pass\n")
        self.assertIn("not written", self.lab("repro", "--out", "out.tsv", "--", sys.executable, skip, expect=1))
        self.assertFalse((self.dir / "out.tsv.repro-before").exists())

    def test_failing_command_and_missing_output(self):
        self.assertIn("failed", self.lab("repro", "--out", "x", "--", sys.executable, "-c", "raise SystemExit(3)", expect=1))
        self.assertIn("not written", self.lab("repro", "--out", "nothing.tsv", "--", sys.executable, "-c", "pass", expect=1))


class Protocol(LabCase):
    """register -> run -> judge -> check, and the holdout, through the ledger."""

    def setUp(self):
        super().setUp()
        self.labdir = self.dir / "research" / "toy"
        self.row("E1", "net_per_trade > 0 & trades >= 30")
        self.write("research/toy/KB/expect/E1.md", "| Stage | Should do |\n|---|---|\n| signal | long on both up |\n")

    def row(self, id, criterion, decision="", result=""):
        path = self.labdir / "KB" / "EXPERIMENTS.tsv"
        head = (ROOT / "skills" / "research" / "EXPERIMENTS-TEMPLATE.tsv").read_text()
        rows = path.read_text() if path.exists() else head
        line = "\t".join([id, "1", "H1.1", "toy", "+0.1 per trade", criterion, "abc", "seed7", "7", result, decision, "", ""])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rows + line + "\n")

    def p(self, *args, expect=0):
        return self.lab(args[0], "--lab", str(self.labdir), *args[1:], expect=expect)

    def run_toy(self, id, *flags, again=False, expect=0):
        out, extra = self.labdir / "results" / id, ["--again"] if again else []
        return self.p("run", id, "--code", str(TOY), *extra, "--", sys.executable, str(TOY), str(out), *flags, expect=expect)

    def test_register_needs_a_row_an_expectation_and_a_readable_criterion(self):
        self.assertIn("no row E9", self.p("register", "E9", expect=1))
        self.row("E2", "net looks good")
        self.write("research/toy/KB/expect/E2.md", "x")
        self.assertIn("cannot read 'net looks good'", self.p("register", "E2", expect=1))
        self.row("E3", "net > 0")
        self.assertIn("no KB/expect/E3.md", self.p("register", "E3", expect=1))
        self.assertIn("registered E1", self.p("register", "E1"))
        self.assertIn("registered already", self.p("register", "E1", expect=1))

    def test_run_judge_and_check(self):
        self.assertIn("not registered", self.run_toy("E1", expect=1))
        self.p("register", "E1")
        self.assertIn("E1: exit 0", self.run_toy("E1"))
        self.assertTrue((self.labdir / "results" / "E1" / "DONE").exists())
        self.assertEqual(json.loads((self.labdir / "results" / "E1" / "RUN.json").read_text())["exit"], 0)
        out = self.p("judge", "E1")
        self.assertIn("E1: PASS  (1 trial so far", out)
        self.assertIn("net_per_trade 0.039 > 0 ok", out)
        self.assertIn("has run already", self.run_toy("E1", expect=1))
        self.run_toy("E1", "--honest", again=True)
        self.assertIn("E1: FAIL  (2 trials", self.p("judge", "E1", expect=1))
        self.assertIn("1 registered, 2 runs (trials), 1 PASS / 1 FAIL", self.p("check"))

    def test_edits_after_registration_are_caught(self):
        self.p("register", "E1")
        self.write("research/toy/KB/expect/E1.md", "| Stage | Should do |\n|---|---|\n| signal | anything |\n")
        self.assertIn("expect/E1.md changed after registration", self.p("check", expect=1))
        self.assertIn("changed after registration", self.run_toy("E1", expect=1))
        path = self.labdir / "KB" / "EXPERIMENTS.tsv"
        path.write_text(path.read_text().replace("net_per_trade > 0", "net_per_trade > -1"))
        self.assertIn("pass_criterion changed after registration", self.p("check", expect=1))

    def test_keep_without_pass_and_changed_metrics_and_a_tampered_ledger(self):
        self.p("register", "E1")
        self.run_toy("E1", "--honest")
        metrics = self.labdir / "results" / "E1" / "metrics.tsv"
        metrics.write_text(metrics.read_text().replace("-0.245", "0.245"))
        self.assertIn("missing or changed after the run", self.p("judge", "E1", expect=1))
        self.row("E1", "net_per_trade > 0 & trades >= 30", decision="KEEP BEST")
        self.assertIn("without a PASS from lab.py judge", self.p("check", expect=1))
        ledger = self.labdir / "LEDGER.jsonl"
        lines = ledger.read_text().splitlines()
        ledger.write_text("\n".join([lines[0].replace("abc", "xyz")] + lines[1:]) + "\n")
        self.assertIn("ledger line 2 (run_start E1) does not follow line 1", self.p("check", expect=1))

    def test_a_crash_is_judged_crash(self):
        self.p("register", "E1")
        self.p("run", "E1", "--", sys.executable, "-c", "raise SystemExit(3)", expect=3)
        self.assertIn("CRASH", self.p("judge", "E1", expect=1))

    def test_holdout_needs_a_pass_has_one_budget_and_is_spent_by_a_failure(self):
        self.p("register", "E1")
        self.run_toy("E1")
        self.assertIn("not judged PASS", self.p("holdout", "open", "E1", expect=1))
        self.p("judge", "E1")
        self.assertIn("look 1 of 2 is open", self.p("holdout", "open", "E1", "--budget", "2"))
        self.assertIn("still open", self.p("holdout", "open", "E1", expect=1))
        holdout = self.dir / "holdout"
        self.lab("repro", "--out", str(holdout / "metrics.tsv"), "--runs", "1", "--",
                 sys.executable, str(TOY), str(holdout), "--honest")
        self.assertIn("E1 on the holdout: FAIL", self.p("holdout", "close", "E1", "--metrics", str(holdout / "metrics.tsv"), expect=1))
        self.assertIn("the holdout is spent", self.p("holdout", "open", "E1", expect=1))
        self.assertIn("fixed at 2", self.p("holdout", "open", "E1", "--budget", "5", expect=1))
        self.assertIn("look 1: E1 FAIL", self.p("holdout", "status"))
        self.assertIn("1 holdout look(s)", self.p("check"))

    def test_the_brief_traces_every_number_to_the_record(self):
        self.p("register", "E1")
        self.run_toy("E1")
        self.p("judge", "E1")
        brief = self.write("research/toy/BRIEF.md", "E1 earned 0.039 per trade over 43 trades, 1.69 in all (3.9%).\n")
        self.assertIn("3 of 3 numbers trace", self.p("brief", brief))
        self.write("research/toy/BRIEF.md", "E1 earned 0.44 per trade.\n")
        self.assertIn("line 1: 0.44 is in no metrics file", self.p("brief", brief, expect=1))


class ToyResearch(LabCase):
    """The planted look-ahead is caught by the truncation test, and only where it is."""

    def runs(self, name, *flags):
        subprocess.run([sys.executable, str(TOY), str(self.dir / name), *flags], check=True)
        return str(self.dir / name / "trace.tsv")

    def test_truncation_finds_the_look_ahead_and_passes_the_honest_version(self):
        out = self.lab("truncate", self.runs("full"), self.runs("cut", "--bars", "200"), "--at", "2026-03-02T12:50:00", expect=1)
        self.assertIn("decision 195 before 2026-03-02T12:50:00 differs", out)
        out = self.lab("truncate", self.runs("honest", "--honest"), self.runs("hcut", "--honest", "--bars", "200"),
                       "--at", "2026-03-02T12:50:00")
        self.assertIn("same 195 decisions", out)


if __name__ == "__main__":
    unittest.main()
