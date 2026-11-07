"""Typed knowledge: what a section is (a measurement, an expectation, a conjecture), whether it still
holds, and which folders a search may draw on.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import time
import unittest

from rag_helpers import RagCase


class Kinds(RagCase):
    copy_corpus = False

    def test_a_prediction_is_labelled_and_never_correct_on_its_own(self):
        self.write("topics/e7.md", """\
            # E7 queue model

            ## E7 net result per trade
            kind: expectation · scope: ES Q3 2026
            We expect E7 to earn 0.4 per trade net of costs.
            """)
        result = self.search("E7", "net", "result", "per", "trade")
        self.assertEqual(result["results"][0]["kind"], "expectation")
        self.assertEqual(result["results"][0]["scope"], "ES Q3 2026")
        self.assertNotEqual(result["verdict"], "CORRECT")
        text = self.run_engine("search", "--no-log", "E7", "net", "result", "per", "trade")
        self.assertIn("<expectation · scope ES Q3 2026>", text)

    def test_the_measurement_wins_over_the_prediction(self):
        self.write("topics/e7.md", """\
            # E7 queue model

            ## E7 expected net result per trade
            kind: expectation
            We expect E7 to earn 0.4 per trade net of costs.

            ## E7 measured net result per trade
            kind: measurement · date: 2026-11-07
            E7 earned 0.02 per trade net of costs over 412 trades.
            """)
        result = self.search("E7", "measured", "net", "result", "per", "trade")
        self.assertEqual(result["results"][0]["kind"], "measurement")
        self.assertEqual(result["verdict"], "CORRECT")

    def test_retired_sections_stay_out_unless_asked_for(self):
        self.write("topics/fills.md", """\
            # Fill model

            ## Mid fills are realistic
            status: superseded · supersedes: nothing
            Buys fill at the mid price in the simulator.

            ## Buys fill at the ask
            kind: measurement
            Buys fill at the ask; the mid was optimistic by a quarter point.
            """)
        out = self.run_engine("search", "--no-log", "mid", "price", "fill")
        self.assertNotIn("Mid fills are realistic", out)
        self.assertIn("marked invalid or superseded were left out", out)
        out = self.run_engine("search", "--no-log", "--with-retired", "mid", "price", "fill")
        self.assertIn("<SUPERSEDED>", out)

    def test_an_invalid_journal_row_is_retired(self):
        research = self.dir / "proj" / "research" / "mm" / "KB"
        research.mkdir(parents=True)
        (research / "EXPERIMENTS.tsv").write_text(
            "id\thypothesis\tresult\tdecision\nE5\tleaky momentum\t+0.44 per trade\tINVALID\n"
            "E6\thonest momentum\t-0.20 per trade\tDISCARD\n", encoding="utf-8")
        out = self.run_engine("search", "--no-log", "--root", str(research), "momentum", "per", "trade")
        self.assertIn("E6", out)
        self.assertNotIn("+0.44", out)

    def test_research_files_get_their_kind_from_where_they_are(self):
        research = self.dir / "proj" / "research" / "mm" / "KB"
        for rel, text in (("reviews/R01_E1.md", "# Review R01\n\n## Leakage in E1\nThe momentum feature reads the next bar.\n"),
                          ("sources/01_fills.md", "# Sources\n\n## Queue position\nBack of queue fills are toxic.\n"),
                          ("KNOWLEDGE.md", "# Knowledge\n\n## Fill side\nBuys pay the ask in every run since E3.\n")):
            (research / rel).parent.mkdir(parents=True, exist_ok=True)
            (research / rel).write_text(text, encoding="utf-8")
        for query, kind in ((["leakage", "momentum"], "review"), (["queue", "toxic"], "source"), (["buys", "ask"], "conclusion")):
            result = self.search("--root", str(research), *query)
            self.assertEqual(result["results"][0]["kind"], kind, query)

    def test_expectation_folders_are_not_indexed(self):
        research = self.dir / "proj" / "research" / "mm" / "KB"
        (research / "expect").mkdir(parents=True)
        (research / "expect" / "E7.md").write_text("# Expected behaviour: E7\n\nThe quokka strategy earns 0.4.\n")
        (research / "KNOWLEDGE.md").write_text("# Knowledge\n\n## Nothing yet\nNo runs.\n")
        out = self.run_engine("search", "--no-log", "--root", str(research), "quokka", "strategy")
        self.assertNotIn("E7.md", out)


class LockBeforeModel(RagCase):
    def test_a_busy_index_leaves_a_note_without_loading_anything(self):
        rag = self.kb / ".rag"
        rag.mkdir(parents=True, exist_ok=True)
        (rag / "lock").write_text(str(os.getpid()))  # a live owner
        (self.dir / "somewhere").mkdir()
        start = time.time()
        out = self.run_engine("index", "--root", str(self.dir / "somewhere"), RESEARCH_LAB_EMBED="fastembed")
        self.assertIn("Another index is running", out)
        self.assertLess(time.time() - start, 10)
        self.assertIn("somewhere", (rag / "dirty").read_text())
        self.assertFalse((rag / "meta.json").exists())


if __name__ == "__main__":
    unittest.main()
