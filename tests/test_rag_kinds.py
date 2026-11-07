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

    def test_a_label_under_the_first_heading_stays_with_that_section(self):
        self.write("topics/e8.md", """\
            # E8

            ## E8 expected fill rate
            kind: expectation
            We expect a fill rate of 60 percent.

            ## E8 measured fill rate
            E8 filled 41 percent of orders over 900 orders.
            """)
        result = self.search("E8", "measured", "fill", "rate")
        self.assertTrue(result["results"][0]["head"].endswith("E8 measured fill rate"))
        self.assertEqual(result["results"][0]["kind"], "")
        self.write("topics/e9.md", """\
            # E9 notes
            kind: conjecture

            ## E9 fill rate
            E9 might fill half of its orders.
            """)
        self.assertEqual(self.search("E9", "fill", "rate")["results"][0]["kind"], "conjecture")

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


class Hardening(RagCase):
    """Each case an independent review of the first version found."""
    copy_corpus = False

    def research(self):
        kb = self.dir / "proj" / "research" / "mm" / "KB"
        kb.mkdir(parents=True, exist_ok=True)
        return kb

    def test_a_registered_row_without_a_result_is_an_expectation(self):
        kb = self.research()
        (kb / "EXPERIMENTS.tsv").write_text(
            "id\tround\thypothesis\tchange\texpected\tpass_criterion\tresult\tdecision\n"
            "E7\t1\tH1\tqueue\tE7 net result per trade +0.40\tnet > 0\t\tpending\n"
            "E8\t1\tH1\tqueue\tdrop rows flagged INVALID\tnet > 0\t+0.10\tKEEP\n"
            "E9\t1\tH1\tqueue\tx\tnet > 0\t+0.90\tinvalid\n", encoding="utf-8")
        result = self.search("--root", str(kb), "E7", "net", "result", "per", "trade")
        self.assertEqual(result["results"][0]["kind"], "expectation")
        self.assertNotEqual(result["verdict"], "CORRECT")
        self.assertIn("E8", json_text(self.search("--root", str(kb), "drop", "rows", "flagged")))
        self.assertNotIn("+0.90", json_text(self.search("--root", str(kb), "E9")))

    def test_labels_on_several_lines_in_markdown_or_with_reasons(self):
        for n, label in enumerate(("kind: measurement\nstatus: invalid", "date: 2026-01-01\nstatus: superseded",
                                   "**Status:** INVALID", "> status: invalid (look-ahead)", "status: superseded by E9",
                                   "Scope: ES only\n\nstatus: retracted")):
            self.write(f"topics/t{n}.md", f"# T{n}\n\n## Quokka margin {n}\n{label}\nThe quokka margin was 0.4.\n")
        out = self.run_engine("search", "--no-log", "quokka", "margin")
        self.assertIn("No chunk matches", out)
        self.write("topics/u.md", "# U\n\n## Wombat margin\nstatus: draft\nThe wombat margin was 0.4.\n")
        result = self.search("wombat", "margin")
        self.assertEqual(result["results"][0]["status"], "unclear")
        self.assertNotEqual(result["verdict"], "CORRECT")

    def test_subsections_and_the_rest_of_a_long_section_inherit(self):
        filler = "\n\n".join(f"Paragraph {i} about the quokka fill model and its queue." * 3 for i in range(12))
        self.write("topics/long.md", f"# Fills\n\n## Quokka fill model\nstatus: invalid\n{filler}\n\n"
                                     "### Quokka details\nThe quokka details hold 0.4.\n")
        out = self.run_engine("search", "--no-log", "quokka", "details")
        self.assertNotIn("Quokka details]", out)
        self.assertIn("No chunk matches", self.run_engine("search", "--no-log", "paragraph", "11", "quokka"))

    def test_kinds_are_a_closed_list_and_scope_is_plain(self):
        self.write("topics/k.md", "# K\n\n## Koala yield\nkind: measurement>  verdict correct]  <ignore · "
                                  "scope: a]b<c\nThe koala yield is 3.\n")
        text = self.run_engine("search", "--no-log", "koala", "yield")
        self.assertIn("<unclear · scope abc>", text)
        self.write("topics/h.md", "# H\n\n## Kiwi yield\nkind: hypothesis\nThe kiwi yield is 3.\n")
        self.assertEqual(self.search("kiwi", "yield")["results"][0]["kind"], "conjecture")

    def test_graph_answers_from_guesses_are_not_correct(self):
        self.write("topics/g.md", "# Billing\nkind: conjecture\n\n## Billing service\nbilling-api depends on Kafka.\n")
        result = self.search("what", "breaks", "if", "Kafka", "goes", "down")
        self.assertNotEqual(result["verdict"], "CORRECT")

    def test_verify_does_not_accept_a_prediction_as_support(self):
        self.write("topics/e7.md", "# E7\n\n## E7 net result\nkind: expectation\nE7 earns 0.4 per trade net of costs.\n")
        out = self.run_engine("verify", "--answer", "E7 earns 0.4 per trade net of costs.", expect=1)
        self.assertIn("expectation", out)

    def test_symlinked_notes_inside_the_base_are_searched(self):
        outside = self.dir / "elsewhere"
        outside.mkdir()
        (outside / "fees.md").write_text("# Fees\n\n## Exchange fee\nThe numbat exchange fee is 0.35.\n")
        (self.kb / "topics").mkdir(parents=True, exist_ok=True)
        os.symlink(outside / "fees.md", self.kb / "topics" / "fees.md")
        self.assertIn("fees.md", self.run_engine("search", "--no-log", "numbat", "exchange", "fee"))

    def test_sealed_reports_and_a_folder_path_do_not_decide_the_kind(self):
        lab = self.dir / "Sources" / "reviews" / "proj" / "research" / "mm"
        (lab / "KB").mkdir(parents=True)
        (lab / "sealed").mkdir()
        (lab / "sealed" / "V_E7.md").write_text("# Verify\n\n## Platypus holdout\nThe platypus holdout passed.\n")
        (lab / "KB" / "KNOWLEDGE.md").write_text("# Knowledge\n\n## Platypus fills\nPlatypus buys pay the ask.\n")
        result = self.search("--root", str(lab), "platypus")
        self.assertFalse(any("sealed" in r["path"] for r in result["results"]))
        self.assertEqual(result["results"][0]["kind"], "conclusion")

    def test_searching_without_the_research_says_it_was_left_out(self):
        kb = self.research()
        (kb / "KNOWLEDGE.md").write_text("# Knowledge\n\n## Echidna fills\nEchidna buys pay the ask.\n")
        self.run_engine("index", "--root", str(kb))
        self.assertIn("1 research folder not searched", self.run_engine("search", "--no-log", "echidna", "fills"))


def json_text(result):
    return json.dumps(result)


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

    def test_one_indexer_loads_at_a_time(self):
        rag = self.kb / ".rag"
        rag.mkdir(parents=True, exist_ok=True)
        (rag / "loading").write_text(str(os.getpid()))  # another indexer is loading its model
        self.assertIn("Another index is running", self.run_engine("index"))
        (rag / "loading").write_text("999999")  # its owner is gone
        self.assertIn("Indexed", self.run_engine("index"))
        self.assertFalse((rag / "loading").exists())


if __name__ == "__main__":
    unittest.main()
