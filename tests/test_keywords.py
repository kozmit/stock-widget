"""Suchbegriffe je Aktie und News-Filter. Kein Netzwerk, keine echte CLI: Yahoo und subprocess sind ersetzt."""
import datetime as dt
import subprocess
import unittest
from unittest import mock

import keywords as kw
from store import Store
from tests.support import AppTestCase

NOW = dt.datetime(2026, 10, 9, 12, 0)
STAMP = dt.datetime(2026, 10, 8, 9, 0)


def entry(source, days_old):
    return {"terms": ["Reddit"], "source": source, "fetched_at": NOW - dt.timedelta(days=days_old)}


class NamesAndTermsTests(unittest.TestCase):
    def test_company_name_loses_its_legal_form(self):
        self.assertEqual(kw.clean_name("Reddit, Inc."), "Reddit")
        self.assertEqual(kw.clean_name("Take-Two Interactive Software, Inc."), "Take-Two Interactive Software")
        self.assertEqual(kw.clean_name("Siemens Energy AG"), "Siemens Energy")
        self.assertEqual(kw.clean_name(None), "")

    def test_normalize_drops_duplicates_symbol_and_junk(self):
        result = kw.normalize(["Reddit", "reddit", "RDDT", " Steve   Huffman ", "ab", "", None, 5, "--"], "RDDT")
        self.assertEqual(result, ["Reddit", "Steve Huffman"])

    def test_normalize_caps_the_list(self):
        self.assertEqual(len(kw.normalize([f"Begriff{i}" for i in range(100)])), kw.MAX_TERMS)

    def test_model_answer_is_read_from_plain_json_and_code_blocks(self):
        self.assertEqual(kw.parse_terms('["a b", "c"]'), ["a b", "c"])
        self.assertEqual(kw.parse_terms('Hier:\n```json\n["x", 3, "y"]\n```'), ["x", "y"])
        with self.assertRaises(ValueError):
            kw.parse_terms("keine Liste")
        with self.assertRaises(ValueError):
            kw.parse_terms("[kaputt")


class RelevanceTests(unittest.TestCase):
    TERMS = ["Reddit", "Steve Huffman", "GTA6", "Words With Friends", "Take-Two"]

    def relevant(self, title, symbol="RDDT"):
        return kw.is_relevant(title, symbol, self.TERMS)

    def test_a_term_in_the_title_is_enough(self):
        self.assertTrue(self.relevant("Evercore ISI Adjusts Price Target on Reddit to $265"))
        self.assertTrue(self.relevant("Steve Huffman sells shares"))
        self.assertTrue(self.relevant("reddit launches ads"))  # Groß- und Kleinschreibung egal

    def test_the_ticker_counts_only_exactly_as_written(self):
        self.assertTrue(self.relevant("Some Company (RDDT) Advances"))
        self.assertFalse(self.relevant("rddt is not written like this"))
        self.assertFalse(self.relevant("XRDDTX"))

    def test_short_tickers_are_never_matched(self):
        self.assertFalse(kw.is_relevant("A new era for T", "T", []))

    def test_spelling_variants_match(self):
        self.assertTrue(self.relevant("GTA 6 may be the last blockbuster"))
        self.assertTrue(self.relevant("Why Take Two stock fell"))
        self.assertTrue(self.relevant("Words with Friends expands its dictionary"))

    def test_terms_match_whole_words_only(self):
        self.assertFalse(self.relevant("Redditors are angry"))
        self.assertFalse(self.relevant("Subreddit rules changed"))

    def test_unrelated_titles_are_dropped(self):
        for title in ("3 Overrated Stocks We Think Twice About", "Teacher, 27, Has $70K 'Doing Nothing' In Savings"):
            self.assertFalse(self.relevant(title), title)

    def test_filter_keeps_order_and_items(self):
        news = [("Reddit up", "Q", STAMP, "l1"), ("Nothing", "Q", STAMP, "l2"), ("GTA 6 delayed", "Q", STAMP, "l3")]
        self.assertEqual([n[3] for n in kw.filter_news(news, "RDDT", self.TERMS)], ["l1", "l3"])

    def test_without_terms_and_with_a_short_ticker_nothing_is_filtered(self):
        news = [("Egal", "Q", STAMP, "l")]
        self.assertEqual(kw.filter_news(news, "T", []), news)
        self.assertEqual(kw.filter_news(news, "RDDT", []), [])  # ein langes Kürzel lässt sich noch prüfen


class RefreshTests(unittest.TestCase):
    def test_missing_entry_needs_a_refresh(self):
        self.assertTrue(kw.needs_refresh(None, NOW))

    def test_model_terms_last_long_and_name_only_terms_are_retried_soon(self):
        self.assertFalse(kw.needs_refresh(entry(kw.SOURCE_MODEL, 30), NOW))
        self.assertTrue(kw.needs_refresh(entry(kw.SOURCE_MODEL, 91), NOW))
        self.assertFalse(kw.needs_refresh(entry(kw.SOURCE_NAME, 0), NOW))
        self.assertTrue(kw.needs_refresh(entry(kw.SOURCE_NAME, 2), NOW))


class ModelCallTests(unittest.TestCase):
    SOURCES = {"name": "Reddit, Inc.", "industry": "Internet", "website": "https://redditinc.com",
               "officers": ["Jennifer Wong (COO)"], "summary": "Reddit operates a community."}

    def completed(self, stdout="", code=0, stderr=""):
        return subprocess.CompletedProcess([], code, stdout, stderr)

    def test_the_cli_is_called_without_tools_and_without_saving_a_session(self):
        with mock.patch.object(kw, "claude_command", return_value="claude"), \
                mock.patch.object(kw.subprocess, "run", return_value=self.completed('["Reddit", "Subreddit"]')) as run:
            self.assertEqual(kw.ask_model("RDDT", self.SOURCES), ["Reddit", "Subreddit"])
        args = run.call_args.args[0]
        self.assertEqual(args[:2], ["claude", "-p"])
        self.assertIn("Reddit, Inc.", args[2])
        self.assertIn("--no-session-persistence", args)
        self.assertEqual(args[args.index("--tools") + 1], "")
        self.assertNotEqual(run.call_args.kwargs["cwd"], "")  # in einem eigenen leeren Ordner

    def test_missing_cli_and_failures_raise_runtime_error(self):
        with mock.patch.object(kw, "claude_command", return_value=None):
            with self.assertRaises(RuntimeError):
                kw.ask_model("RDDT", self.SOURCES)
        with mock.patch.object(kw, "claude_command", return_value="claude"):
            with mock.patch.object(kw.subprocess, "run", return_value=self.completed(code=1, stderr="boom")):
                with self.assertRaises(RuntimeError):
                    kw.ask_model("RDDT", self.SOURCES)
            with mock.patch.object(kw.subprocess, "run", side_effect=subprocess.TimeoutExpired("claude", 1)):
                with self.assertRaises(RuntimeError):
                    kw.ask_model("RDDT", self.SOURCES)

    def test_collect_combines_name_and_model_terms(self):
        with mock.patch.object(kw, "fetch_sources", return_value=self.SOURCES), \
                mock.patch.object(kw, "ask_model", return_value=["Subreddit", "reddit", "Steve Huffman"]):
            found = kw.collect("RDDT")
        self.assertEqual(found, {"terms": ["Reddit", "Subreddit", "Steve Huffman"], "source": kw.SOURCE_MODEL})

    def test_collect_falls_back_to_the_company_name_without_the_model(self):
        for failure in (RuntimeError("keine CLI"), ValueError("Müll")):
            with mock.patch.object(kw, "fetch_sources", return_value=self.SOURCES), \
                    mock.patch.object(kw, "ask_model", side_effect=failure):
                found = kw.collect("RDDT")
            self.assertEqual(found, {"terms": ["Reddit"], "source": kw.SOURCE_NAME})


class StoreTests(unittest.TestCase):
    def test_terms_are_saved_and_read_back_with_umlauts(self):
        store = Store(":memory:")
        store.save_search_terms("SAP.DE", ["SAP", "Hasso Plattner", "Müller"], kw.SOURCE_MODEL, NOW)
        store.save_search_terms("SAP.DE", ["SAP", "Hasso Plattner", "Müller"], kw.SOURCE_MODEL, NOW)  # ersetzt
        self.assertEqual(store.search_terms(),
                         {"SAP.DE": {"terms": ["SAP", "Hasso Plattner", "Müller"], "source": kw.SOURCE_MODEL,
                                     "fetched_at": NOW}})

    def test_a_damaged_row_is_skipped(self):
        store = Store(":memory:")
        store.db.execute("INSERT INTO search_terms VALUES ('X', 'kein json', 'Test', '2026-10-09T12:00:00')")
        self.assertEqual(store.search_terms(), {})


class ControllerTermsTests(AppTestCase):
    def run_now(self):
        """Führt die Arbeit sofort aus statt im Hintergrund; ein Fehler geht wie sonst an fail oder verfällt."""
        def run(ctl, work, done, fail=None, pool=None):
            try:
                value = work()
            except Exception as exc:
                if fail:
                    fail(exc)
                return
            done(value)
        return mock.patch.object(type(self.ctl), "run", run)

    def test_terms_are_collected_saved_and_announced(self):
        seen = []
        self.ctl.terms_changed.connect(seen.append)
        found = {"terms": ["Apple", "iPhone"], "source": kw.SOURCE_MODEL}
        with self.run_now(), mock.patch.object(kw, "collect", lambda symbol: found):
            self.assertTrue(self.ctl.load_terms("AAPL"))
        self.assertEqual(self.ctl.terms["AAPL"]["terms"], ["Apple", "iPhone"])
        self.assertEqual(self.ctl.store.search_terms()["AAPL"]["terms"], ["Apple", "iPhone"])
        self.assertEqual(seen, ["AAPL"])

    def test_fresh_terms_are_not_collected_again_and_a_start_tries_each_stock_once(self):
        calls = []

        def collect(symbol):
            calls.append(symbol)
            raise ValueError("keine Stammdaten")
        with self.run_now(), mock.patch.object(kw, "collect", collect):
            self.ctl.load_terms("AAPL")
            self.ctl.load_terms("AAPL")  # der zweite Versuch im selben Start entfällt
        self.assertEqual(calls, ["AAPL"])
        self.ctl.terms["MSFT"] = {"terms": ["Microsoft"], "source": kw.SOURCE_MODEL, "fetched_at": dt.datetime.now()}
        with self.run_now(), mock.patch.object(kw, "collect", collect):
            self.assertFalse(self.ctl.load_terms("MSFT"))

    def test_news_terms_fall_back_to_the_company_name(self):
        self.assertEqual(self.ctl.news_terms("AAPL"), [])
        self.ctl.instruments["AAPL"] = {"name": "Apple Inc."}
        self.assertEqual(self.ctl.news_terms("AAPL"), ["Apple"])
        self.ctl.terms["AAPL"] = {"terms": ["Apple", "iPhone"], "source": kw.SOURCE_MODEL, "fetched_at": NOW}
        self.assertEqual(self.ctl.news_terms("AAPL"), ["Apple", "iPhone"])

    def test_removing_a_stock_forgets_its_terms(self):
        self.ctl.terms["AAPL"] = {"terms": ["Apple"], "source": kw.SOURCE_MODEL, "fetched_at": NOW}
        self.ctl.remove("AAPL")
        self.assertNotIn("AAPL", self.ctl.terms)


if __name__ == "__main__":
    unittest.main()
