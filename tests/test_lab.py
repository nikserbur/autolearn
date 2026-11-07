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
SERIES = ROOT / "tests" / "fixtures" / "research" / "series_sim.py"


class LabCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def lab(self, *args, expect=0):
        proc = subprocess.run([sys.executable, str(LAB), *args], capture_output=True, text=True,
                              cwd=self.dir, timeout=60)
        if expect is not None:
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

    def test_nanosecond_stamps_read_and_nan_does_not(self):
        out = self.lab("trace", self.write("ns.tsv", "ts\tstage\taction\tinputs\tinputs_ts\n"
                       "2026-03-02T10:00:00.123456789\tsignal\tlong\tx\t2026-03-02T10:00:00.223456789\n"), expect=1)
        self.assertIn("look-ahead", out)
        out = self.lab("trace", self.write("nan.tsv", "ts\tstage\taction\tinputs\tinputs_ts\nnan\tsignal\tlong\tx\t1\n"), expect=1)
        self.assertIn("unreadable ts", out)

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

    def test_a_file_left_from_before_is_not_a_reproduction_and_survives(self):
        self.write("out.tsv", "old\n")
        skip = self.write("skip.py", "import os\nif not os.path.exists('out.tsv'):\n    pass\n")
        self.assertIn("not written", self.lab("repro", "--out", "out.tsv", "--", sys.executable, skip, expect=1))
        self.assertEqual((self.dir / "out.tsv").read_text(), "old\n")
        noisy = self.write("noisy2.py", "import random\nopen('out.tsv', 'w').write(str(random.random()))\n")
        self.lab("repro", "--out", "out.tsv", "--", sys.executable, noisy, expect=1)
        self.assertEqual((self.dir / "out.tsv").read_text(), "old\n")
        self.assertEqual(sorted(p.name for p in self.dir.glob("out.tsv*")), ["out.tsv"])
        self.assertIn("at least 2", self.lab("repro", "--out", "out.tsv", "--runs", "1", "--", sys.executable, noisy, expect=2))

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

    def row(self, id, criterion, decision="", result="", review=""):
        path = self.labdir / "KB" / "EXPERIMENTS.tsv"
        head = (ROOT / "skills" / "research" / "EXPERIMENTS-TEMPLATE.tsv").read_text()
        rows = path.read_text() if path.exists() else head
        line = "\t".join([id, "1", "H1.1", "toy", "+0.1 per trade", criterion, "abc", "seed7", "7", result, decision, "", review])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rows + line + "\n")

    def p(self, *args, expect=0):
        return self.lab(args[0], "--lab", str(self.labdir), *args[1:], expect=expect)

    def run_toy(self, id, *flags, again=False, expect=0):
        out, extra = self.labdir / "results" / id, ["--again"] if again else []
        return self.p("run", id, "--code", str(TOY), *extra, "--", sys.executable, str(TOY), str(out), *flags, expect=expect)

    def passed(self, id="E1"):
        self.p("register", id)
        self.run_toy(id)
        self.p("judge", id)

    def proven_verification(self):
        rows = "".join(f"| V{i} | requirement {i} | ✅ | evidence {i} |\n" for i in range(1, 6))
        self.write("research/toy/KB/ENVIRONMENT.md", "| # | Requirement | Status | Evidence |\n|---|---|---|---|\n" + rows)

    def holdout_metrics(self, *flags):
        out = self.labdir / "sealed" / "E1"
        subprocess.run([sys.executable, str(TOY), str(out), *flags], check=True)
        return str(out / "metrics.tsv")

    def test_register_needs_a_row_an_expectation_and_a_readable_criterion(self):
        self.assertIn("no row E9", self.p("register", "E9", expect=1))
        self.row("E2", "net looks good")
        self.write("research/toy/KB/expect/E2.md", "x")
        self.assertIn("cannot read 'net looks good'", self.p("register", "E2", expect=1))
        self.row("E3", "net > 0")
        self.assertIn("no KB/expect/E3.md", self.p("register", "E3", expect=1))
        self.assertIn("registered E1", self.p("register", "E1"))
        self.assertIn("registered already", self.p("register", "E1", expect=1))
        self.assertIn("letters, digits", self.p("register", "../esc", expect=1))

    def test_run_judge_and_check(self):
        self.assertIn("not registered", self.run_toy("E1", expect=1))
        self.p("register", "E1")
        self.assertIn("--code names the files", self.p("run", "E1", "--", sys.executable, "-c", "pass", expect=1))
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

    def test_keep_needs_a_pass_on_the_latest_run_and_the_reviewers_pass(self):
        self.passed()
        self.row("E1", "net_per_trade > 0 & trades >= 30", decision="KEEP")
        self.assertIn("without a reviewer PASS for its latest run", self.p("check", expect=1))
        self.p("review", "E1", "--verdict", "BLOCK", "--round", "R01")
        self.assertIn("without a reviewer PASS", self.p("check", expect=1))
        self.p("review", "E1", "--verdict", "PASS", "--round", "R02")
        self.assertIn("1 PASS", self.p("check"))
        self.run_toy("E1", "--honest", again=True)  # a new attempt that nobody judged
        self.assertIn("without a PASS from lab.py judge on its latest run", self.p("check", expect=1))
        self.assertIn("latest run was not judged PASS", self.p("holdout", "open", "E1", expect=1))

    def test_changed_metrics_multi_row_and_non_finite_values_are_refused(self):
        self.p("register", "E1")
        self.run_toy("E1", "--honest")
        metrics = self.labdir / "results" / "E1" / "metrics.tsv"
        metrics.write_text(metrics.read_text().replace("-0.245", "0.245"))
        self.assertIn("missing or changed after the run", self.p("judge", "E1", expect=1))
        for text, said in (("seed\tnet_per_trade\ttrades\n0\t5\t100\n1\t-9\t100\n", "rows of values"),
                           ("net_per_trade\ttrades\ninf\t100\n", "no finite value for 'net_per_trade'")):
            script = self.write(f"w{len(said)}.py", f"open({str(metrics)!r}, 'w').write({text!r})\n")
            self.p("run", "E1", "--code", script, "--again", "--", sys.executable, script)
            self.assertIn(said, self.p("judge", "E1", expect=1))
        script = self.write("long.py", f"open({str(metrics)!r}, 'w').write('metric\\tvalue\\nnet_per_trade\\t0.5\\ntrades\\t40\\n')\n")
        self.p("run", "E1", "--code", script, "--again", "--", sys.executable, script)
        self.assertIn("PASS", self.p("judge", "E1"))

    def test_a_crash_is_judged_crash(self):
        self.p("register", "E1")
        script = self.write("crash.py", "raise SystemExit(3)\n")
        self.p("run", "E1", "--code", script, "--", sys.executable, script, expect=3)
        self.assertIn("CRASH", self.p("judge", "E1", expect=1))

    def test_the_ledger_catches_edits_a_deleted_last_line_and_bad_lines(self):
        self.passed()
        ledger = self.labdir / "LEDGER.jsonl"
        lines = ledger.read_text().splitlines()
        ledger.write_text("\n".join(lines[:-1]) + "\n")
        self.assertIn("the end of the ledger was edited or removed", self.p("check", expect=1))
        ledger.write_text("\n".join([lines[0].replace("abc", "xyz")] + lines[1:]) + "\n")
        self.assertIn("ledger line 2 (run_start E1) does not follow line 1", self.p("check", expect=1))
        ledger.write_text("\n".join(lines[:1] + ["not json", "[1, 2]"] + lines[1:]) + "\n")
        out = self.p("check", expect=1)
        self.assertIn("line 2 is not a ledger entry", out)
        self.assertNotIn("Traceback", out)

    def test_odd_characters_in_a_command_do_not_break_the_ledger(self):
        self.p("register", "E1")
        out = self.labdir / "results" / "E1"
        self.p("run", "E1", "--code", str(TOY), "--", sys.executable, str(TOY), str(out), "a b\x0cc")
        self.assertIn("E1: PASS", self.p("judge", "E1"))
        self.assertIn("1 registered", self.p("check"))

    def test_concurrent_appends_keep_the_chain(self):
        code = ("import sys, pathlib; sys.path.insert(0, sys.argv[1]); from labkit.ledger import Ledger; "
                "[Ledger(pathlib.Path(sys.argv[2])).append('note', id=sys.argv[3]) for _ in range(5)]")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(LAB.parent), str(self.labdir), f"P{i}"])
                 for i in range(8)]
        for proc in procs:
            proc.wait()
        self.assertEqual([e for e in (self.labdir / "LEDGER.jsonl").read_text().splitlines()].__len__(), 40)
        self.assertIn("0 registered", self.p("check"))

    def series(self, id, mean, seed, again=False, expect=0):
        out, extra = self.labdir / "results" / id, ["--again"] if again else []
        return self.p("run", id, "--code", str(SERIES), *extra, "--", sys.executable, str(SERIES), str(out),
                      str(mean), str(seed), expect=expect)

    def make_best(self, id="E1", mean=0.5, seed=1, expect=0):
        if id != "E1":
            self.row(id, "net_per_trade > 0 & trades >= 30")
            self.write(f"research/toy/KB/expect/{id}.md", "| Stage | Should do |\n|---|---|\n| day | earn |\n")
        self.p("register", id)
        self.series(id, mean, seed)
        self.p("judge", id)
        self.p("review", id, "--verdict", "PASS", "--round", "R01")
        return self.p("best", id, expect=expect)

    def test_holdout_is_bound_to_the_best_candidate_and_spent_by_a_failure(self):
        self.p("register", "E1")
        self.series("E1", 0.5, 1)
        self.assertIn("not judged PASS", self.p("holdout", "open", "E1", expect=1))
        self.p("judge", "E1")
        self.assertIn("not the current BEST", self.p("holdout", "open", "E1", expect=1))
        self.p("review", "E1", "--verdict", "PASS", "--round", "R01")
        self.p("best", "E1")
        self.assertIn("verification requirements are not proven", self.p("holdout", "open", "E1", expect=1))
        self.proven_verification()
        self.assertIn("1 to 3 looks", self.p("holdout", "open", "E1", "--budget", "1000", expect=1))
        self.assertIn("look 1 of 2 is open", self.p("holdout", "open", "E1", "--budget", "2"))
        self.assertIn("still open", self.p("holdout", "open", "E1", expect=1))
        self.assertIn("looked at on the holdout", self.series("E1", 0.5, 1, again=True, expect=1))
        sim = str(self.labdir / "results" / "E1" / "metrics.tsv")
        self.assertIn("written by the holdout run", self.p("holdout", "close", "E1", "--metrics", sim, expect=1))
        copy = self.labdir / "sealed" / "copy.tsv"
        copy.parent.mkdir(parents=True, exist_ok=True)
        copy.write_bytes(pathlib.Path(sim).read_bytes())
        self.assertIn("output of a simulation run", self.p("holdout", "close", "E1", "--metrics", str(copy), expect=1))
        out = self.labdir / "sealed" / "E1"
        subprocess.run([sys.executable, str(SERIES), str(out), "-0.5", "9"], check=True)
        self.assertIn("E1 on the holdout: FAIL",
                      self.p("holdout", "close", "E1", "--metrics", str(out / "metrics.tsv"), expect=1))
        self.assertIn("the holdout is spent", self.p("holdout", "open", "E1", expect=1))
        self.assertIn("fixed at 2", self.p("holdout", "open", "E1", "--budget", "3", expect=1))
        self.assertIn("look 1: E1 FAIL", self.p("holdout", "status"))
        self.assertIn("1 holdout look(s)", self.p("check"))

    def test_settings_are_checked_and_fixed_before_the_first_run(self):
        self.assertIn("at most 0.1", self.p("settings", "--alpha", "0.5", expect=1))
        self.assertIn("from 1 to 250", self.p("settings", "--block", "0", expect=1))
        self.assertIn("from 1 to 250", self.p("settings", "--block", "nan", expect=1))
        self.assertIn("block auto", self.p("settings", "--alpha", "0.05"))
        self.p("register", "E1")
        out = self.series("E1", 0.0, 3)
        self.assertIn("not a discovery", out)
        self.assertIn("1 trials so far", out)
        self.assertEqual(self.p("stats", "E1", expect=1).splitlines(), out.splitlines()[1:])
        self.assertIn("settings are fixed", self.p("settings", "--alpha", "0.1", expect=1))

    def test_every_run_is_a_test_so_fishing_through_noise_finds_nothing(self):
        self.p("register", "E1")
        for seed in range(12):
            self.series("E1", 0.0, seed, again=seed > 0)
        self.run_toy("E1", again=True)  # no daily.tsv: a test with p = 1
        self.assertIn("13 tests (one per run), 0 discoveries", self.p("fdr"))
        self.assertIn("not tested:", self.p("stats", "E1", expect=1))

    def test_repeating_a_run_does_not_shrink_the_noise_benchmark(self):
        self.p("register", "E1")
        self.series("E1", 0.15, 1)
        out = self.series("E1", 0.15, 1, again=True)
        benchmark = float(out.split("best Sharpe of ")[1].split(";")[0])
        self.assertAlmostEqual(benchmark, 0.033, delta=0.002)  # variance floored at 1/n, not the 0 of two copies

    def test_compare_needs_the_same_periods(self):
        self.make_best("E1", 0.5, 1)
        self.row("E2", "net_per_trade > 0 & trades >= 30")
        self.write("research/toy/KB/expect/E2.md", "| Stage | Should do |\n|---|---|\n| day | earn |\n")
        self.p("register", "E2")
        out = self.labdir / "results" / "E2"
        self.p("run", "E2", "--code", str(SERIES), "--", sys.executable, str(SERIES), str(out), "0.9", "2", "200")
        self.assertIn("cover different periods", self.p("compare", "E2", "E1", expect=1))

    def test_best_needs_pass_review_significance_and_to_beat_the_current_best(self):
        self.p("register", "E1")
        out = self.series("E1", 0.5, 1)
        self.assertIn("a discovery", out)
        self.p("judge", "E1")
        self.assertIn("no reviewer PASS for its latest run", self.p("best", "E1", expect=1))
        self.p("review", "E1", "--verdict", "PASS", "--round", "R01")
        self.assertIn("E1 is BEST", self.p("best", "E1"))
        self.assertIn("not compared with the current best E1", self.make_best("E2", 0.55, 2, expect=1))
        first = self.p("compare", "E2", "E1", expect=1)
        self.assertIn("NOT BETTER", first)
        self.assertEqual(first, self.p("compare", "E2", "E1", expect=1))  # recorded once, not redrawn
        self.assertEqual(self.labdir.joinpath("LEDGER.jsonl").read_text().count('"kind": "compare"'), 1)
        self.assertIn("not better than E1 outside the interval", self.p("best", "E2", expect=1))
        self.make_best("E3", 1.5, 4, expect=1)
        self.assertIn("BETTER", self.p("compare", "E3", "E1"))
        self.assertIn("E3 is BEST (was E1)", self.p("best", "E3"))
        self.row("E1", "net_per_trade > 0 & trades >= 30", decision="KEEP BEST")
        self.assertIn("marked BEST but `lab.py best` recorded E3", self.p("check", expect=1))
        out = self.p("fdr")
        self.assertIn("E3\t1\t", out)
        self.assertIn("3 tests", out)

    def test_conclusions_resting_on_an_invalid_run_must_say_so(self):
        self.passed()
        self.row("E1", "net_per_trade > 0 & trades >= 30", decision="INVALID")
        knowledge = ("# Knowledge\n\n## What we know now\n- E1 (INVALID: look-ahead) proves nothing.\n\n"
                     "## Momentum works\n- E1 earned 0.039 per trade.\n\n## Rejected\n- E1-style entries.\n")
        self.write("research/toy/KB/KNOWLEDGE.md", knowledge)
        out = self.p("check", expect=1)
        self.assertIn("'Momentum works' rests on E1, which is INVALID", out)
        self.assertNotIn("What we know now", out)
        self.write("research/toy/KB/KNOWLEDGE.md", knowledge.replace(
            "## Momentum works\n", "## Momentum works\nkind: conclusion · status: invalid\n"))
        self.assertNotIn("rests on", self.p("check"))
        self.write("research/toy/KB/KNOWLEDGE.md", knowledge.replace(
            "## Momentum works\n", "## Momentum works\n- **Status:** superseded by E9\n"))
        self.assertNotIn("rests on", self.p("check"))
        lowered = knowledge.replace("- E1 earned", "- e1 invalidates nothing; it earned")
        self.write("research/toy/KB/KNOWLEDGE.md", lowered)
        self.assertIn("rests on E1", self.p("check", expect=1))

    def test_jobs_and_status_say_where_the_research_stands(self):
        self.assertIn("no runs yet", self.p("jobs"))
        self.make_best("E1", 0.5, 1)
        self.row("E2", "net_per_trade > 0 & trades >= 30")
        self.write("research/toy/KB/expect/E2.md", "| Stage | Should do |\n|---|---|\n| day | earn |\n")
        self.p("register", "E2")
        self.series("E2", 0.2, 5)
        self.p("judge", "E2", expect=None)
        sys.path.insert(0, str(LAB.parent))
        from labkit.ledger import Ledger
        Ledger(self.labdir).append("run_start", id="E3", command=["x"], candidate="c", hashes={}, pid=999999)
        jobs = self.p("jobs")
        self.assertIn("E1\tdone", jobs)
        self.assertIn("E3\tinterrupted", jobs)
        status = self.p("status")
        self.assertTrue((self.labdir / "STATUS.md").exists())
        self.assertIn("- Best: E1, mean", status)
        self.assertIn("E3: interrupted", status)
        self.assertIn("E2: judged, awaiting the reviewer's verdict", status)
        self.assertIn("E1 is the best but has not been looked at on the holdout", status)
        self.assertIn("Trials: 3", status)
        self.assertFalse((self.labdir / "KB" / "STATUS.md").exists())

    def test_a_long_knowledge_file_is_flagged(self):
        self.write("research/toy/KB/KNOWLEDGE.md", "# Knowledge\n\n" + "## Note\n- x\n" * 4000)
        self.assertIn("move older sections to KB/archive/", self.p("check"))

    def test_the_brief_traces_every_number_to_the_record(self):
        self.passed()
        brief = self.write("research/toy/BRIEF.md", "On 2026-11-07, E1 earned 0.039 per trade over 43 trades, "
                           "1.69 in all (3.9%), in 1 trial; see section 2.\n")
        self.assertIn("4 of 4 numbers trace", self.p("brief", brief))
        self.write("research/toy/BRIEF.md", "E1 earned 0.44 per trade over 47 trades; we expected 0.1.\n")
        out = self.p("brief", brief, expect=1)
        for token in ("0.44", "47", "0.1"):
            self.assertIn(f": {token} is in no metrics file", out)
        self.write("research/toy/KB/PROGRAM.md", "Costs: spread 0.50, fees 1,250.75 a month.\n")
        self.write("research/toy/BRIEF.md", "Costs as planned: spread 0.50 and 1,250.75 in fees.\n")
        self.assertIn("2 of 2 numbers trace", self.p("brief", brief))


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

    def test_truncation_does_not_pass_without_comparing(self):
        full = self.runs("full")
        self.assertIn("same file content", self.lab("truncate", full, full, "--at", "2026-03-02T12:50:00", expect=1))
        cut = self.runs("cut", "--bars", "200")
        self.assertIn("no decision before", self.lab("truncate", full, cut, "--at", "2026-03-02T09:00:00", expect=1))
        self.assertIn("different clocks", self.lab("truncate", full, cut, "--at", "1772456400", expect=1))
        a = self.write("a.tsv", "ts\tstage\taction\tinputs\n0\tsignal\tlong\tx=9\nbad\tsignal\tflat\tx\n")
        b = self.write("b.tsv", "ts\tstage\taction\tinputs\n0\tsignal\tlong\tx=1\n")
        self.assertIn("unreadable ts 'bad'", self.lab("truncate", a, b, "--at", "2", expect=1))
        a = self.write("a.tsv", "ts\tstage\taction\tinputs\n0\tsignal\tlong\tx=9\n")
        self.assertIn("decision 1 before 2 differs", self.lab("truncate", a, b, "--at", "2", expect=1))

    def test_streams_are_compared_separately(self):
        a = self.write("a.tsv", "ts\tstream\tstage\taction\tinputs\n1\tES\ts\tlong\tx\n1\tNQ\ts\tflat\ty\n")
        b = self.write("b.tsv", "ts\tstream\tstage\taction\tinputs\n1\tNQ\ts\tflat\ty\n1\tES\ts\tlong\tx\n")
        self.assertIn("same 2 decisions", self.lab("truncate", a, b, "--at", "5"))


if __name__ == "__main__":
    unittest.main()
