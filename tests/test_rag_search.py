"""Search side of the RAG engine: routing, hybrid fusion, reranking, GraphRAG, CRAG and the faithfulness
check, end to end through the command line. Standard library only:
python3 -m unittest discover -s tests -v
"""
import importlib.util
import json
import pathlib
import unittest

from rag_helpers import CORPUS, ROOT, RagCase

from ragkit import graph as graph_mod
from ragkit.verify import split_claims


class Routing(RagCase):
    def route(self, *query):
        return self.search(*query)["route"]

    def test_identifier_goes_to_exact(self):
        self.assertEqual(self.route("ERR-4417"), "exact")

    def test_specific_question_goes_to_local(self):
        self.assertEqual(self.route("webhook retry timeout"), "local")

    def test_relation_question_about_a_known_entity_goes_to_graph(self):
        self.assertEqual(self.route("что сломается, если отключить Redis"), "graph")

    def test_relation_words_without_an_entity_stay_local(self):
        self.assertEqual(self.route("what breaks the session timeout"), "local")

    def test_ordinary_words_are_not_taken_for_entities(self):
        self.write("topics/plain.md", "# Plain\n\n## Note\n\nThe Commands page and the Planner page are separate.\n")
        self.assertEqual(self.route("commands planner"), "local")

    def test_question_about_the_whole_base_goes_to_global(self):
        self.assertEqual(self.route("какие общие проблемы были у всех команд"), "global")
        self.assertEqual(self.route("what integration problems recur across teams"), "global")

    def test_all_alone_does_not_make_a_question_global(self):
        self.assertEqual(self.route("how do I install all hooks"), "local")

    def test_mode_overrides_the_route(self):
        self.assertEqual(self.search("--mode", "exact", "webhook retry timeout")["route"], "exact")


class NoiseFloor(RagCase):
    def test_closeness_means_the_same_under_any_model(self):
        from ragkit.search import Index

        class Store:
            meta = {"files": {}, "backend": "fastembed:x", "noise": 0.5}
        index = Index(Store, self.kb)
        self.assertAlmostEqual(index.closeness(0.75), 0.5)
        self.assertLess(index.closeness(0.4), 0)
        Store.meta = {"files": {}, "backend": "fastembed:y", "noise": 0.2}
        self.assertAlmostEqual(Index(Store, self.kb).closeness(0.6), 0.5)

    def test_hashing_vouches_for_nothing(self):
        from ragkit.search import Index

        class Store:
            meta = {"files": {}, "backend": "hash-512"}
        self.assertEqual(Index(Store, self.kb).closeness(0.99), 0.0)

    def test_it_is_measured_for_models_only(self):
        self.run_engine("index")
        self.assertEqual(self.meta()["noise"], 0.0)
        self.fake_models()
        self.run_engine("index", AUTOLEARN_EMBED="fastembed", AUTOLEARN_RERANK="off")
        self.assertGreater(self.meta()["noise"], 0.0)


class GlobalNeedsDocuments(RagCase):
    copy_corpus = False

    def test_one_document_cannot_answer_a_question_about_all_of_them(self):
        self.write("a.md", "# A\n\n## S\n\nCommon problems of all teams are listed here.\n")
        self.assertEqual(self.search("какие общие проблемы у всех команд")["route"], "local")


class Hybrid(RagCase):
    def test_exact_code_beats_a_near_twin(self):
        result = self.search("ERR-4417")
        self.assertEqual(result["verdict"], "CORRECT")
        self.assertIn("ERR-4417", result["results"][0]["text"])
        self.assertIn("runbooks/errors.md", self.paths(result)[0])

    def test_unknown_long_number_is_not_confused_with_one_sharing_its_prefix(self):
        self.write("topics/n.md", "# N\n\n## S\n\nInvoice 1234599 was paid on time.\n")
        self.assertEqual(self.search("invoice 1234567")["verdict"] != "CORRECT", True)
        self.assertEqual(self.search("invoice 1234599")["verdict"], "CORRECT")

    def test_all_three_rankings_contribute(self):
        self.write("topics/one.md", "# Billing\n\n## Limits\n\nThe nightly export stops at the retention boundary.\n")
        result = self.search("nightly export retention")
        self.assertEqual(self.paths(result)[0], "topics/one.md")
        self.assertGreater(result["results"][0]["cos"], 0.0)

    def test_section_spread_answer_is_found(self):
        self.write("topics/spread.md", "# Quokka\n\n## Care\n\nThe quokka needs shade.\n\n"
                   + "Filler sentence without anything of interest, repeated at length. " * 10
                   + "\n\nIts enclosure faces north.\n")
        self.assertEqual(self.paths(self.search("quokka enclosure"))[0], "topics/spread.md")


class ParentChild(RagCase):
    def test_search_finds_the_child_and_returns_its_section(self):
        result = self.search("when do we escalate to the database team")
        top = result["results"][0]
        self.assertEqual(top["path"], "handbook/oncall.md")
        self.assertIn("Escalation to the database team", top["text"])
        self.assertGreater(top["end"] - top["start"], top["matched"][1] - top["matched"][0],
                           "the reader gets more than the sentence that matched")
        self.assertIn("Handover notes", top["text"], "the section's other paragraphs come with it")

    def test_section_over_the_budget_shows_only_the_matched_child(self):
        out = self.run_engine("search", "--no-log", "--budget", "400", "escalation", "database", "team", "read-only")
        self.assertIn("=== handbook/oncall.md", out)
        self.assertIn("20 minutes of read-only", out)
        self.assertNotIn("Pager volume", out)

    def test_two_children_of_a_section_are_one_result(self):
        paths = self.paths(self.search("rotation handover postmortems"))
        self.assertEqual(paths.count("handbook/oncall.md"), 1)


class Contextual(RagCase):
    def test_chunk_is_found_by_what_only_its_document_says(self):
        result = self.search("какая выручка у компании Globex за 2025")
        self.assertEqual(self.paths(result)[0], "reports/globex-2025.md")
        self.assertNotIn("Globex", result["results"][0]["text"].split("## Показатели")[1])

    def test_same_sentence_in_two_documents_is_told_apart(self):
        self.assertEqual(self.paths(self.search("операционная маржа Acme"))[0], "reports/acme-2025.md")
        self.assertEqual(self.paths(self.search("операционная маржа Globex"))[0], "reports/globex-2025.md")


class Reranking(RagCase):
    def test_built_in_puts_the_stated_fact_first(self):
        result = self.search("webhook retry timeout")
        self.assertEqual(result["reranker"], "built-in")
        self.assertEqual(self.paths(result)[0], "notes/webhooks-settings.md")

    def test_literal_form_beats_a_shared_stem(self):
        self.write("topics/x.md", "# Contributing\n\n## Ways\n\nContributors contribute lessons by contributing tests.\n")
        self.write("topics/y.md", "# Conflicts\n\n## Rule\n\nContradicted lessons move to the archive.\n")
        self.assertEqual(self.paths(self.search("contradicted lessons"))[0], "topics/y.md")

    def test_noise_far_below_the_best_is_cut_to_pointers(self):
        out = self.run_engine("search", "--no-log", "webhook", "retry", "timeout")
        self.assertLessEqual(out.count("=== "), 5)

    def ce_ready(self):
        self.fake_models()
        self.env_extra["FAKE_CE_PREFER"] = "A webhook is an HTTP callback"
        self.run_engine("index")

    def test_cross_encoder_is_used_once_available_and_decides_the_order(self):
        self.ce_ready()
        result = self.search("webhook retry timeout")
        self.assertIn("fastembed:", result["reranker"])
        self.assertEqual(self.paths(result)[0], "notes/webhooks-general.md")

    def test_search_never_downloads_the_cross_encoder_but_index_does(self):
        self.fake_models()
        self.assertEqual(self.search("webhook retry timeout")["reranker"], "built-in")
        log = pathlib.Path(self.env_extra["FAKE_CE_LOG"])
        self.assertFalse(log.exists(), "a search before the first index does not even try to load it")
        self.run_engine("index")
        self.assertIn("local_files_only=False", log.read_text())
        self.assertIn("fastembed:", self.search("webhook retry timeout")["reranker"])

    def test_index_fetches_it_once(self):
        self.ce_ready()
        log = pathlib.Path(self.env_extra["FAKE_CE_LOG"])
        before = log.read_text().count("local_files_only=False")
        self.run_engine("index")
        self.assertEqual(log.read_text().count("local_files_only=False"), before)

    def test_english_cross_encoder_sits_out_on_russian_text(self):
        self.ce_ready()
        result = self.search("сколько живёт сессия ttl")
        self.assertIn("English only", result["reranker"])
        self.assertEqual(self.paths(result)[0], "architecture/session-service.md")

    def test_english_cross_encoder_reads_english_notes_that_hold_a_stray_symbol(self):
        self.write("topics/sym.md", "# Limits \u2014 notes\n\n## Values\n\nThe webhook retry timeout budget \u2192 see the dead letter queue.\n")
        self.ce_ready()
        self.assertIn("fastembed:", self.search("webhook retry timeout")["reranker"])

    def test_english_cross_encoder_is_kept_from_russian_notes_on_an_english_question(self):
        self.write("topics/ru.md", "# Webhook retry timeout\n\n## \u0417\u043d\u0430\u0447\u0435\u043d\u0438\u0435\n\n\u0422\u0430\u0439\u043c\u0430\u0443\u0442 \u043f\u043e\u0432\u0442\u043e\u0440\u0430 \u0440\u0430\u0432\u0435\u043d 30 \u0441\u0435\u043a\u0443\u043d\u0434\u0430\u043c \u0434\u043b\u044f \u0432\u0441\u0435\u0445 \u0432\u0435\u0431\u0445\u0443\u043a\u043e\u0432.\n")
        self.fake_models()
        self.env_extra["FAKE_CE_PREFER"] = "\u0422\u0430\u0439\u043c\u0430\u0443\u0442"
        self.run_engine("index")
        result = self.search("webhook retry timeout")
        self.assertIn("fastembed:", result["reranker"])
        self.assertEqual(self.paths(result)[0], "notes/webhooks-settings.md")

    def test_multilingual_model_is_trusted_with_russian(self):
        self.fake_models()
        self.env_extra["AUTOLEARN_RERANK_MODEL"] = "jinaai/jina-reranker-v2-base-multilingual"
        self.run_engine("index")
        self.assertIn("multilingual", self.search("сколько живёт сессия ttl")["reranker"])

    def test_sentence_transformers_cross_encoder_also_works(self):
        self.fake_sentence_transformers()
        self.assertEqual(self.search("webhook retry timeout")["reranker"], "built-in")
        self.run_engine("index")
        self.assertTrue(self.search("webhook retry timeout")["reranker"].startswith("st:"))

    def test_cross_encoder_failure_keeps_the_answer(self):
        self.ce_ready()
        result = self.search("webhook retry timeout", FAKE_CE_FAIL="1")
        self.assertEqual(result["reranker"], "built-in")
        self.assertEqual(self.paths(result)[0], "notes/webhooks-settings.md")

    def test_exact_route_skips_the_cross_encoder(self):
        self.ce_ready()
        self.assertEqual(self.search("ERR-4417")["reranker"], "built-in")

    def test_turned_off_means_off(self):
        self.ce_ready()
        self.assertEqual(self.search("webhook retry timeout", AUTOLEARN_RERANK="off")["reranker"], "built-in")


class GraphExtraction(unittest.TestCase):
    def rels(self, text, subject=""):
        return graph_mod.extract(text, subject, graph_mod.mid_sentence_names(text))[1]

    def test_typed_relations_in_both_languages(self):
        self.assertEqual(self.rels("billing-api depends on Kafka to publish events."), [["billing-api", "depends_on", "Kafka"]])
        self.assertEqual(self.rels("Checkout зависит от session-service."), [["Checkout", "depends_on", "session-service"]])

    def test_passive_voice_keeps_the_direction(self):
        self.assertEqual(self.rels("Redis is used by session-service."), [["session-service", "uses", "Redis"]])

    def test_generic_subject_becomes_what_the_document_is_about(self):
        self.assertEqual(self.rels("Сервис использует Redis как хранилище.", "session-service"),
                         [["session-service", "uses", "Redis"]])

    def test_several_objects(self):
        self.assertEqual(self.rels("checkout-api depends on Redis and Kafka."),
                         [["checkout-api", "depends_on", "Redis"], ["checkout-api", "depends_on", "Kafka"]])

    def test_arrow_is_a_relation(self):
        self.assertEqual(self.rels("gateway-proxy -> billing-api"), [["gateway-proxy", "flows_to", "billing-api"]])

    def test_ordinary_nouns_make_no_relation(self):
        self.assertEqual(self.rels("The report requires approval. Teams use templates."), [])
        self.assertEqual(self.rels("Use Redis for caches."), [])

    def test_capitalised_first_word_is_grammar_unless_the_document_uses_it_as_a_name(self):
        names = lambda text: graph_mod.extract(text, "", graph_mod.mid_sentence_names(text))[0]
        self.assertNotIn("Every", names("Every alert links to a runbook."))
        self.assertIn("Vault", names("Vault stores secrets. We rotate keys in Vault weekly."))
        self.assertIn("Redis", names("Redis упал ночью."), "a Latin name in a Cyrillic sentence")

    def test_markdown_scaffolding_starts_a_new_sentence(self):
        names = lambda text: graph_mod.extract(text, "", graph_mod.mid_sentence_names(text))[0]
        self.assertNotIn("Plugin", names("| Mode | Plugin |\n| light | **Plugin** agents |"))

    def test_identifiers_and_code(self):
        names = graph_mod.extract("See PAY-123, E07c and `kb_rag.py` in src/app/main.py today.", "", set())[0]
        for expected in ("PAY-123", "E07c", "kb_rag.py", "src/app/main.py"):
            self.assertIn(expected, names)

    def test_spelling_variants_are_one_node(self):
        self.assertEqual(graph_mod.key_of("Order-Services"), graph_mod.key_of("order service"))


class GraphSearch(RagCase):
    def test_impact_question_reaches_what_never_mentions_the_entity(self):
        result = self.search("что сломается, если отключить Redis")
        self.assertEqual(result["verdict"], "CORRECT")
        self.assertIn("architecture/checkout.md", self.paths(result))
        checkout = next(r for r in result["results"] if r["path"] == "architecture/checkout.md")
        self.assertNotIn("Redis", checkout["text"])
        self.assertTrue(any("session-service --uses--> Redis" in p for p in result["paths"]), result["paths"])
        self.assertTrue(any("checkout --depends_on--> session-service" in p for p in result["paths"]), result["paths"])

    def test_two_hops_across_documents_and_languages(self):
        paths = self.paths(self.search("what breaks if Kafka goes down"))
        for doc in ("architecture/billing-api.md", "architecture/notifications.md", "architecture/checkout.md"):
            self.assertIn(doc, paths)

    def test_impact_walks_towards_dependents_only(self):
        links = self.search("what breaks if session-service goes down")["paths"]
        self.assertTrue(any("checkout --depends_on--> session-service" in p for p in links), links)
        self.assertFalse(any("--> Redis" in p for p in links), links)

    def test_dependency_question_walks_the_other_way(self):
        links = self.search("от чего зависит billing-api")["paths"]
        self.assertTrue(any("billing-api --depends_on--> Kafka" in p for p in links), links)
        self.assertFalse(any("--> billing-api" in p for p in links), links)

    def test_question_about_two_entities_finds_the_path_between_them(self):
        result = self.search("как связаны checkout и Redis")
        self.assertEqual(result["route"], "graph")
        self.assertIn("architecture/session-service.md", self.paths(result))
        self.assertEqual(len(result["paths"]), 2)

    def test_graph_follows_edits_without_a_rebuild(self):
        self.assertIn("architecture/checkout.md", self.paths(self.search("что сломается, если отключить Redis")))
        doc = self.kb / "architecture" / "checkout.md"
        doc.write_text(doc.read_text(encoding="utf-8").replace("checkout зависит от session-service", "checkout не трогает"),
                       encoding="utf-8")
        links = self.search("что сломается, если отключить Redis")["paths"]
        self.assertFalse(any("checkout --depends_on--> session-service" in p for p in links), links)

    def test_entity_listing(self):
        out = self.run_engine("graph", "Redis")
        self.assertIn("<--uses-- session-service", out)
        self.assertIn("No entity", self.run_engine("graph", "kubernetes"))
        self.assertIn("clusters", self.run_engine("graph"))


class GlobalSearch(RagCase):
    TEAMS = ["retros/2026-q3-payments.md", "retros/2026-q3-search.md", "retros/2026-q3-mobile.md",
             "retros/2026-q3-growth.md", "retros/2026-q3-platform.md"]

    def test_every_team_gets_a_voice_however_chatty_one_of_them_is(self):
        result = self.search("какие общие проблемы интеграции были у всех команд в третьем квартале")
        self.assertEqual(result["route"], "global")
        self.assertEqual(result["verdict"], "AMBIGUOUS", "a sample of the base: words cannot prove it complete")
        paths = self.paths(result)
        for team in self.TEAMS:
            self.assertIn(team, paths)
        self.assertLessEqual(paths.count("retros/2026-q3-platform.md"), 3)

    def test_the_period_keeps_other_quarters_out(self):
        result = self.search("какие общие проблемы интеграции были у всех команд в третьем квартале")
        self.assertNotIn("retros/2026-q2-payments.md", self.paths(result))
        self.assertTrue(all(p.startswith("retros/2026-q3") for p in self.paths(result)), self.paths(result))
        wider = self.search("какие общие проблемы интеграции были у всех команд")
        self.assertIn("retros/2026-q2-payments.md", self.paths(wider))

    def test_what_recurs_across_documents_is_named_with_evidence(self):
        themes = self.search("какие общие проблемы интеграции были у всех команд в третьем квартале")["themes"]
        okta = next(t for t in themes if t["term"] == "okta")
        self.assertEqual(okta["docs"], 4)
        self.assertEqual(okta["of"], 5)
        self.assertTrue(okta["evidence"][0].startswith("retros/"))
        self.assertTrue(all(t["docs"] >= 2 for t in themes))
        self.assertFalse(any(t["term"].startswith("интегр") for t in themes), "the question's own word is no finding")

    def test_text_report_shows_themes(self):
        out = self.run_engine("search", "--no-log", "what integration problems were common across all teams in Q3")
        self.assertIn("Recurring across 5 documents", out)

    def test_question_in_another_language_is_flagged_not_trusted(self):
        result = self.search("what integration problems were common across all teams in Q3")
        self.assertEqual(result["verdict"], "AMBIGUOUS", "only the period matched: the hash backend cannot read Russian")

    def test_question_with_no_anchor_falls_back_to_communities(self):
        result = self.search("what are the main themes overall")
        self.assertEqual(result["route"], "global")
        self.assertIn("clusters", result["why"])
        self.assertEqual(result["verdict"], "AMBIGUOUS")
        self.assertTrue(result["results"])


class CorrectiveRag(RagCase):
    def logged(self):
        return (self.kb / ".router-log.tsv").read_text(encoding="utf-8").strip().split("\n")[-1].split("\t")

    def test_answerable_is_correct_and_logged_as_hit(self):
        out = self.run_engine("search", "webhook", "retry", "timeout")
        self.assertIn("verdict CORRECT", out)
        self.assertIn("kb_rag.py verify", out)
        fields = self.logged()
        self.assertEqual(len(fields), 4)
        self.assertEqual(fields[3], "hit")
        self.assertTrue(fields[2].startswith("notes/webhooks-settings.md:"))

    def test_not_in_the_base_is_refused_and_logged_as_miss(self):
        for query in ("настройка ingress в kubernetes", "vacation policy for contractors", "рецепт борща с фасолью"):
            out = self.run_engine("search", query)
            self.assertIn("verdict INCORRECT", out)
            self.assertIn("No chunk matches", out)
            self.assertIn("the web", out)
            self.assertNotIn("=== ", out, "nothing is presented as an answer")
            self.assertEqual(self.logged()[2:], ["-", "miss"])

    def test_refusal_still_shows_weak_candidates(self):
        out = self.run_engine("search", "--no-log", "how", "do", "I", "roll", "back", "a", "release")
        self.assertIn("~== notes/releases.md", out)
        self.assertIn("| To undo a bad version", out)

    def test_partly_answered_says_what_is_missing(self):
        self.write("topics/p.md", "# Quokka care\n\n## Food\n\nThe quokka eats leaves and bark every evening.\n")
        out = self.run_engine("search", "quokka", "leaves", "evening", "zoology", "--no-log")
        self.assertIn("verdict AMBIGUOUS", out)
        self.assertIn("Not found: zoology", out)
        self.assertIn("=== topics/p.md", out)

    def test_unknown_identifier_is_not_answered_by_its_neighbours(self):
        result = self.search("что показал E77")
        self.assertNotEqual(result["verdict"], "CORRECT")

    def test_compound_question_is_split_and_the_unanswered_part_named(self):
        result = self.search("операционная маржа Acme и настройка kubernetes ingress")
        self.assertEqual(result["verdict"], "AMBIGUOUS")
        self.assertIn("split", result["corrected"])
        self.assertIn("reports/acme-2025.md", self.paths(result))
        self.assertEqual(result["missing"], ["настройка kubernetes ingress"])

    def test_inflected_form_the_stem_misses_is_repaired(self):
        self.write("topics/e.md", "# Каталог\n\n## Журнал\n\nСписок ошибок лежит в журнале каталога.\n")
        result = self.search("ошибка")
        self.assertEqual(self.paths(result)[0], "topics/e.md")
        self.assertIn("ошибк", result["corrected"])

    def test_question_in_another_alphabet_than_the_notes_gets_told_so(self):
        for path in list((self.kb).rglob("*.md")) + list(self.kb.rglob("*.tsv")):
            path.unlink()
        self.write("topics/en.md", "# Deploys\n\n## Rollback\n\nRollbacks redeploy the previous container image.\n")
        out = self.run_engine("search", "--no-log", "как откатить релиз")
        self.assertIn("No chunk matches", out)
        self.assertIn("another language", out)

    def test_empty_query_and_empty_base(self):
        self.assertIn("No chunk matches", self.run_engine("search", "--no-log"))
        for path in sorted(self.kb.rglob("*"), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.assertIn("No chunk matches", self.run_engine("search", "--no-log", "anything"))


class Faithfulness(RagCase):
    def verify(self, answer, expect=0):
        return json.loads(self.run_engine("verify", "--json", "--answer", answer, expect=expect))

    def test_grounded_answer_passes(self):
        report = self.verify("Выручка Acme выросла на 12% за счёт подписок. Операционная маржа Acme составила 18%.")
        self.assertTrue(report["passed"], report)
        self.assertEqual(report["score"], 1.0)

    def test_wrong_number_fails_and_names_what_the_evidence_says(self):
        report = self.verify("Выручка Acme выросла на 15% за счёт подписок.", expect=1)
        claim = report["claims"][0]
        self.assertEqual(claim["verdict"], "unsupported")
        self.assertIn("15", claim["why"])
        self.assertIn("12", claim["why"])
        self.assertTrue(claim["evidence"].startswith("reports/acme-2025.md:"))

    def test_invented_claim_fails(self):
        report = self.verify("Квантовый криптографический модуль телепортирует биткоины.", expect=1)
        self.assertEqual(report["claims"][0]["verdict"], "unsupported")

    def test_one_bad_claim_fails_a_good_answer(self):
        report = self.verify("Выручка Acme выросла на 12% за счёт подписок. Квантовый модуль телепортирует биткоины.", expect=1)
        self.assertEqual([c["verdict"] for c in report["claims"]], ["supported", "unsupported"])

    def test_flipped_negation_is_flagged(self):
        report = self.verify("Webhook retry timeout is not 30 seconds.", expect=1)
        self.assertEqual(report["claims"][0]["verdict"], "partial")
        self.assertIn("negation", report["claims"][0]["why"])

    def test_good_citation_is_accepted_and_bad_ones_are_flagged(self):
        self.assertTrue(self.verify("Webhook retry timeout is 30 seconds (notes/webhooks-settings.md:5).")["passed"])
        report = self.verify("Webhook retry timeout is 30 seconds (notes/nowhere.md:5).", expect=1)
        self.assertIn("does not have", report["claims"][0]["why"])
        report = self.verify("Webhook retry timeout is 30 seconds (notes/webhooks-settings.md:999).", expect=1)
        self.assertIn("no such section", report["claims"][0]["why"])

    def test_citation_must_name_the_file_not_just_end_like_it(self):
        self.write("topics/data.md", "# Data\n\n## S\n\nThe quokka export runs at noon.\n")
        report = self.verify("The quokka export runs at noon (a.md:3).", expect=1)
        self.assertIn("does not have", report["claims"][0]["why"])
        self.assertTrue(self.verify("The quokka export runs at noon (data.md:5).")["passed"])

    def test_empty_answer_fails(self):
        self.assertFalse(self.verify("", expect=1)["passed"])

    def test_text_report_and_other_inputs(self):
        draft = self.dir / "draft.md"
        draft.write_text("- Webhook retry timeout is 30 seconds.\n", encoding="utf-8")
        self.assertIn("FAITHFUL", self.run_engine("verify", "--file", str(draft)))
        self.assertIn("REVISE", self.run_engine("verify", stdin="Биткоины телепортируются квантовым модулем.\n", expect=1))

    def test_claims_skip_code_markup_and_empty_lines(self):
        claims = split_claims("# Heading words\n\n- First claim here.\n\n```\ncode here now\n```\n1. Second claim here. Third claim here.\nok\n")
        self.assertEqual(claims, ["Heading words", "First claim here.", "Second claim here.", "Third claim here."])


class Reporting(RagCase):
    def test_journal_row_too_big_for_its_share_is_shown_whole(self):
        from ragkit.report import passage_text
        from ragkit.search import Passage
        path = self.kb / "journal" / "EXPERIMENTS.tsv"
        lines = path.read_text(encoding="utf-8").splitlines()
        from ragkit.core import digest
        parent = {"s": 4, "e": 4, "head": "", "sha": digest(lines[3])}
        got = passage_text(str(path), Passage(str(path), 0, parent, {"s": 4, "e": 4}, 1.0, 0.0, 1.0, 1, 1), room=5)
        self.assertIn("E03", got)

    def test_folder_named_like_the_base_with_a_suffix(self):
        extra = self.kb.parent / "knowledge-archive"
        extra.mkdir()
        (extra / "n.md").write_text("# N\n\n## S\n\nThe quokka archive note.\n", encoding="utf-8")
        self.assertIn("knowledge-archive/n.md", self.run_engine("search", "--no-log", "--root", str(extra), "quokka", "archive"))

    def test_header_explains_the_decision(self):
        out = self.run_engine("search", "--no-log", "ERR-4417")
        self.assertRegex(out.splitlines()[0], r"^\[route exact: .* verdict CORRECT")

    def test_json_has_the_documented_fields(self):
        result = self.search("webhook retry timeout")
        for field in ("route", "why", "verdict", "confidence", "reranker", "corrected", "missing", "paths", "themes",
                      "clusters", "results"):
            self.assertIn(field, result)
        for field in ("path", "start", "end", "matched", "head", "score", "cos", "coverage", "hop", "text"):
            self.assertIn(field, result["results"][0])

    def test_status_lists_every_technique_without_loading_a_model(self):
        self.run_engine("index")
        out = self.run_engine("status")
        for word in ("backend hash-512", "late chunking", "reranker", "llm context", "graph:", "last change"):
            self.assertIn(word, out)


class Golden(unittest.TestCase):
    """Floors on the golden set, so a change that quietly hurts retrieval fails CI."""

    def test_floors(self):
        spec = importlib.util.spec_from_file_location("rag_eval", ROOT / "scripts" / "rag_eval.py")
        rag_eval = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(rag_eval)
        golden = CORPUS.parent / "golden_fixture.tsv"
        table = rag_eval.summary(rag_eval.evaluate(rag_eval.ENGINE, CORPUS, golden, "hash"))
        for kind in ("contextual", "child", "exact", "rerank"):
            self.assertEqual(table[kind]["top1"], 1.0, f"{kind}: {table[kind]}")
        self.assertEqual(table["graph"]["recall"], 1.0, table["graph"])
        self.assertEqual(table["global"]["recall"], 1.0, table["global"])
        self.assertEqual(table["ood"]["correct_refusal"], 1.0, table["ood"])


if __name__ == "__main__":
    unittest.main()
