"""News-Feed: Einstufung, Zusammenführen, Auswahl, Controller und Fenster."""
import datetime as dt
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

import events as evt
import newsfeed as nf
import stock_data as sd
import stock_widget as w
from tests.support import AppTestCase, wait_until

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def ago(hours):
    return NOW - dt.timedelta(hours=hours)


def news(title, hours=1, source="Quelle", link=None):
    return (title, source, ago(hours), link or f"https://x.test/{title.replace(' ', '-')}")


def item(title="Titel", hours=1, importance=1, tag="", symbols=("AAPL",), link=None):
    return nf.FeedItem(title, "Quelle", ago(hours) if hours is not None else None,
                       link or f"https://x.test/{title}", list(symbols), importance, tag)


def event(kind="earnings", day=dt.date(2026, 10, 14), status="expected", title="Quartalszahlen", end=None, symbol="AAPL"):
    return evt.Event(1, symbol, kind, title, day, end or day, "day", status, "Yahoo Finance")


class ClassifyTests(unittest.TestCase):
    def check(self, title, level, tag=""):
        self.assertEqual(nf.classify(title), (level, tag), title)

    def test_results_guidance_deals_approvals_and_lawsuits_are_important(self):
        self.check("Apple earnings beat expectations", 3, "Zahlen")
        self.check("Apple Quartalszahlen übertreffen Erwartungen", 3, "Zahlen")
        self.check("Cloudflare raises full-year outlook", 3, "Prognose")
        self.check("Siemens hebt Prognose an", 3, "Prognose")
        self.check("Nvidia to acquire startup for $1 billion", 3, "Übernahme")
        self.check("Telekom übernimmt Konkurrenten", 3, "Übernahme")
        self.check("FDA approves new drug", 3, "Zulassung")
        self.check("EU sues the company over antitrust", 3, "Rechtliches")
        self.check("Tesla recall affects 100,000 cars", 3, "Rechtliches")
        self.check("CEO steps down after ten years", 3, "Führung")

    def test_orders_analysts_launches_and_dividends_are_interesting(self):
        self.check("Rheinmetall wins large order", 2, "Auftrag")
        self.check("Goldman upgrades the stock", 2, "Analysten")
        self.check("JPMorgan Adjusts Price Target on GE Vernova to $1,150", 2, "Analysten")
        self.check("Apple unveils new iPhone", 2, "Produkt")
        self.check("Company raises dividend and buyback", 2, "Dividende")

    def test_the_most_important_rule_wins_over_the_first(self):
        self.check("Analyst upgrade after earnings beat", 3, "Zahlen")

    def test_everything_else_is_normal(self):
        self.check("Ein ganz normaler Titel", 1)
        self.check("", 1)

    def test_price_chatter_and_advice_lists_are_noise(self):
        for title in ("Why Nvidia Stock Is Up Today", "Should You Buy Apple Stock Now?", "3 Stocks to Buy in 2026",
                      "Apple Shares Jump 3% in Early Trading", "Is Reddit Still Below Fair Value?",
                      "Which Is the Better Buy: AMD or Intel?", "Jim Cramer Likes This Stock",
                      "Why Are Nasdaq Futures Surging Premarket?", "Aktie steigt heute kräftig"):
            self.assertEqual(nf.classify(title), (0, ""), title)

    def test_noise_never_hides_an_important_or_interesting_story(self):
        self.check("Why Apple stock fell after earnings", 3, "Zahlen")
        self.check("Tests Its Valuation As The Mistral Deal Reshapes The Story", 2, "Auftrag")

    def test_case_does_not_matter(self):
        self.check("EARNINGS PREVIEW", 3, "Zahlen")

    def test_words_are_matched_whole(self):
        self.check("The merger-free zone", 3, "Übernahme")  # „merger“ als ganzes Wort
        self.check("Dealer network grows", 1)              # „deal“ steckt in „Dealer“


class KeyTests(unittest.TestCase):
    def test_links_ignore_scheme_www_query_fragment_and_trailing_slash(self):
        base = nf.link_key("https://finance.example.com/news/abc")
        for variant in ("http://www.finance.example.com/news/abc/", "https://finance.example.com/news/abc?utm=1#x",
                        "HTTPS://Finance.Example.com/News/ABC"):
            self.assertEqual(nf.link_key(variant), base)
        self.assertNotEqual(nf.link_key("https://finance.example.com/news/abd"), base)

    def test_empty_links_give_an_empty_key(self):
        self.assertEqual(nf.link_key(""), "")
        self.assertEqual(nf.link_key(None), "")

    def test_titles_ignore_case_punctuation_and_spaces(self):
        self.assertEqual(nf.title_key("Apple:  Beats, Again!"), nf.title_key("apple beats again"))


class MergeTests(unittest.TestCase):
    def test_the_same_story_for_two_stocks_appears_once_with_both_symbols(self):
        story = news("Chipmaker deal shakes market", link="https://x.test/a")
        items = nf.merge({"NVDA": [story], "TSM": [story]})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].symbols, ["NVDA", "TSM"])

    def test_the_same_title_under_another_address_is_the_same_story(self):
        items = nf.merge({"NVDA": [news("Same Title", link="https://a.test/1")],
                          "TSM": [news("same  title!", link="https://b.test/2")]})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].symbols, ["NVDA", "TSM"])

    def test_the_same_address_with_another_title_is_the_same_story(self):
        items = nf.merge({"NVDA": [news("Title one", link="https://a.test/1?x=1")],
                          "TSM": [news("Title two", link="https://www.a.test/1")]})
        self.assertEqual(len(items), 1)

    def test_different_stories_stay_apart(self):
        items = nf.merge({"NVDA": [news("One", link="https://a.test/1"), news("Two", link="https://a.test/2")]})
        self.assertEqual(len(items), 2)

    def test_a_symbol_is_not_listed_twice(self):
        story = news("Story", link="https://a.test/1")
        self.assertEqual(nf.merge({"NVDA": [story, story]})[0].symbols, ["NVDA"])

    def test_each_story_is_classified(self):
        [found] = nf.merge({"AAPL": [news("Apple earnings beat")]})
        self.assertEqual((found.importance, found.tag), (3, "Zahlen"))

    def test_unrelated_stories_are_filtered_with_the_search_terms(self):
        items = nf.merge({"AAPL": [news("Apple launches phone"), news("10 stocks for your portfolio")]},
                         lambda symbol: ["Apple"])
        self.assertEqual([i.title for i in items], ["Apple launches phone"])

    def test_without_a_filter_nothing_is_removed(self):
        self.assertEqual(len(nf.merge({"AAPL": [news("Whatever one"), news("Whatever two")]})), 2)

    def test_nothing_gives_an_empty_list(self):
        self.assertEqual(nf.merge({}), [])
        self.assertEqual(nf.merge({"AAPL": []}), [])


class AttachEventsTests(unittest.TestCase):
    TODAY = dt.date(2026, 10, 9)

    def attach(self, items, events):
        return nf.attach_events(items, events, self.TODAY)

    def test_a_story_about_results_gets_the_matching_event(self):
        [found] = self.attach([item("Apple earnings preview", importance=3, tag="Zahlen")], {"AAPL": [event()]})
        self.assertEqual(found.event_note, "Termin Quartalszahlen, 14.10.2026 (erwartet)")

    def test_the_status_is_the_effective_one(self):
        past = event(day=dt.date(2026, 10, 7))
        [found] = self.attach([item("Apple earnings", importance=3, tag="Zahlen", hours=1)], {"AAPL": [past]})
        self.assertIn("(eingetreten)", found.event_note)

    def test_a_speculative_event_stays_speculative(self):
        [found] = self.attach([item("Apple earnings", importance=3, tag="Zahlen")],
                              {"AAPL": [event(status="speculative")]})
        self.assertIn("(spekulativ)", found.event_note)

    def test_an_event_too_far_away_does_not_match(self):
        far = event(day=dt.date(2026, 10, 9) + dt.timedelta(days=nf.EVENT_WINDOW_DAYS + 3))
        [found] = self.attach([item("Apple earnings", importance=3, tag="Zahlen", hours=1)], {"AAPL": [far]})
        self.assertEqual(found.event_note, "")

    def test_the_window_includes_its_edges(self):
        edge = event(day=dt.date(2026, 10, 9) + dt.timedelta(days=nf.EVENT_WINDOW_DAYS))
        [found] = self.attach([item("Apple earnings", importance=3, tag="Zahlen", hours=1)], {"AAPL": [edge]})
        self.assertNotEqual(found.event_note, "")

    def test_a_different_kind_of_event_does_not_match(self):
        [found] = self.attach([item("Apple earnings", importance=3, tag="Zahlen")],
                              {"AAPL": [event(kind="product", title="GTA VI")]})
        self.assertEqual(found.event_note, "")

    def test_a_story_without_a_tag_or_a_time_gets_nothing(self):
        plain = item("Apple news")
        undated = item("Apple earnings", importance=3, tag="Zahlen", hours=None)
        self.attach([plain, undated], {"AAPL": [event()]})
        self.assertEqual((plain.event_note, undated.event_note), ("", ""))

    def test_a_story_for_two_stocks_names_the_one_with_the_event(self):
        [found] = self.attach([item("Chip earnings", importance=3, tag="Zahlen", symbols=("NVDA", "TSM"))],
                              {"TSM": [event(symbol="TSM")]})
        self.assertTrue(found.event_note.startswith("Termin TSM: Quartalszahlen"))

    def test_a_period_event_matches_inside_its_period(self):
        month = evt.Event(1, "AAPL", "product", "Demo", dt.date(2026, 10, 1), dt.date(2026, 10, 31), "month", "expected", "manual")
        [found] = self.attach([item("Apple launch", importance=2, tag="Produkt")], {"AAPL": [month]})
        self.assertIn("Demo", found.event_note)


class BuildTests(unittest.TestCase):
    def build(self, items, **kw):
        return nf.build(items, NOW, **kw)

    def test_important_stories_come_first_then_the_newest(self):
        a = item("Alt wichtig", hours=30, importance=3)
        b = item("Neu normal", hours=1, importance=1)
        c = item("Neu interessant", hours=2, importance=2)
        d = item("Neuer wichtig", hours=5, importance=3)
        self.assertEqual([i.title for i in self.build([b, a, c, d]).items],
                         ["Neuer wichtig", "Alt wichtig", "Neu interessant", "Neu normal"])

    def test_the_time_limit_hides_old_stories_and_counts_them(self):
        feed = self.build([item("Neu", hours=10), item("Alt", hours=60)], hours=48)
        self.assertEqual([i.title for i in feed.items], ["Neu"])
        self.assertEqual(feed.old_hidden, 1)

    def test_the_limit_is_inclusive(self):
        self.assertEqual(len(self.build([item("Genau", hours=48)], hours=48).items), 1)

    def test_no_time_limit_shows_everything(self):
        feed = self.build([item("Sehr alt", hours=5000)], hours=None)
        self.assertEqual((len(feed.items), feed.old_hidden), (1, 0))

    def test_noise_is_hidden_and_counted_unless_asked_for(self):
        stories = [item("Gerede", importance=0), item("Normal", importance=1)]
        feed = self.build(stories)
        self.assertEqual(([i.title for i in feed.items], feed.minor_hidden), (["Normal"], 1))
        shown = self.build(stories, show_minor=True)
        self.assertEqual(([i.title for i in shown.items], shown.minor_hidden), (["Normal", "Gerede"], 0))

    def test_old_noise_counts_as_old_not_as_noise(self):
        feed = self.build([item("Altes Gerede", hours=100, importance=0)], hours=48)
        self.assertEqual((feed.old_hidden, feed.minor_hidden), (1, 0))

    def test_the_count_limit_cuts_and_reports_the_rest(self):
        stories = [item(f"Meldung {n}", hours=n) for n in range(1, 8)]
        feed = self.build(stories, limit=3)
        self.assertEqual(([i.title for i in feed.items], feed.more), (["Meldung 1", "Meldung 2", "Meldung 3"], 4))

    def test_the_default_limit_is_thirty(self):
        feed = self.build([item(f"M{n}", hours=n % 40 + 1) for n in range(45)])
        self.assertEqual((len(feed.items), feed.more), (30, 15))

    def test_no_count_limit_shows_all(self):
        feed = self.build([item(f"M{n}") for n in range(45)], limit=None)
        self.assertEqual((len(feed.items), feed.more), (45, 0))

    def test_a_story_without_a_time_is_kept_and_sorted_last_in_its_level(self):
        stories = [item("Ohne Zeit", hours=None), item("Mit Zeit", hours=3)]
        self.assertEqual([i.title for i in self.build(stories).items], ["Mit Zeit", "Ohne Zeit"])

    def test_nothing_gives_an_empty_feed(self):
        feed = self.build([])
        self.assertEqual((feed.items, feed.minor_hidden, feed.old_hidden, feed.more), ([], 0, 0, 0))

    def test_the_labels(self):
        self.assertEqual([nf.importance_label(item(importance=n)) for n in (3, 2, 1, 0)],
                         ["wichtig", "interessant", "", "Kursgerede"])


def name_the_companies(ctl):
    """Der Filter erkennt News am Firmennamen; ohne Stammdaten passt nur das Kürzel im Titel."""
    for symbol, name in (("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corporation"), ("DELL", "Dell")):
        ctl.instruments[symbol] = {"name": name, "source": "Test", "fetched_at": dt.datetime.now()}


class ControllerFeedTests(AppTestCase):
    def setUp(self):
        super().setUp()
        name_the_companies(self.ctl)

    def serve(self, by_symbol):
        calls = []

        def fetch(symbol, count=15):
            calls.append(symbol)
            result = by_symbol.get(symbol)
            if isinstance(result, Exception):
                raise result
            return result or []
        patcher = mock.patch.object(sd, "fetch_news", fetch)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def fresh(self, title, hours=1, link=None):
        stamp = dt.datetime.now(UTC) - dt.timedelta(hours=hours)
        return (title, "Quelle", stamp, link or f"https://x.test/{title.replace(' ', '-')}")

    def test_loading_stores_the_news_and_announces_them(self):
        self.serve({"AAPL": [self.fresh("Apple earnings beat")]})
        seen = []
        self.ctl.news_changed.connect(seen.append)
        self.assertTrue(self.ctl.load_news("AAPL"))
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.news_cache))
        self.assertEqual(len(self.ctl.news_cache["AAPL"]["news"]), 1)
        self.assertIsNone(self.ctl.news_cache["AAPL"]["error"])
        self.assertEqual(seen, ["AAPL"])
        self.assertNotIn("AAPL", self.ctl.news_loading)

    def test_fresh_news_are_not_loaded_again_but_old_ones_and_force_are(self):
        calls = self.serve({"AAPL": [self.fresh("Apple earnings beat")]})
        self.ctl.load_news("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.news_cache))
        self.assertFalse(self.ctl.load_news("AAPL"))
        self.assertTrue(self.ctl.load_news("AAPL", now=dt.datetime.now() + w.NEWS_MAX_AGE + dt.timedelta(seconds=1)))
        self.assertTrue(wait_until(lambda: not self.ctl.news_loading))
        self.assertTrue(self.ctl.load_news("AAPL", force=True))
        self.assertTrue(wait_until(lambda: len(calls) == 3))

    def test_a_request_in_flight_is_not_doubled(self):
        self.serve({})
        self.assertTrue(self.ctl.load_news("AAPL"))
        self.assertFalse(self.ctl.load_news("AAPL", force=True))

    def test_a_failure_keeps_earlier_news_and_notes_the_error(self):
        self.serve({"AAPL": [self.fresh("Apple earnings beat")]})
        self.ctl.load_news("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.news_cache))
        mock.patch.object(sd, "fetch_news", mock.Mock(side_effect=RuntimeError("offline"))).start()
        self.addCleanup(mock.patch.stopall)
        self.ctl.load_news("AAPL", force=True)
        self.assertTrue(wait_until(lambda: self.ctl.news_cache["AAPL"]["error"] == "offline"))
        self.assertEqual(len(self.ctl.news_cache["AAPL"]["news"]), 1)

    def test_a_failure_without_earlier_news_leaves_none(self):
        mock.patch.object(sd, "fetch_news", mock.Mock(side_effect=RuntimeError("offline"))).start()
        self.addCleanup(mock.patch.stopall)
        self.ctl.load_news("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.news_cache))
        self.assertIsNone(self.ctl.news_cache["AAPL"]["news"])

    def test_a_failed_load_is_retried_even_when_recent(self):
        calls = self.serve({"AAPL": RuntimeError("offline")})
        self.ctl.load_news("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.news_cache))
        self.assertTrue(self.ctl.load_news("AAPL"))
        self.assertTrue(wait_until(lambda: len(calls) == 2))

    def test_a_result_for_a_removed_stock_is_dropped(self):
        self.serve({"AAPL": [self.fresh("Apple earnings beat")]})
        self.ctl.load_news("AAPL")
        self.ctl.remove("AAPL")
        self.assertTrue(wait_until(lambda: not self.ctl.news_loading))
        self.assertNotIn("AAPL", self.ctl.news_cache)

    def test_removing_a_stock_forgets_its_news(self):
        self.ctl.store_news("AAPL", [self.fresh("Apple earnings beat")])
        self.ctl.remove("AAPL")
        self.assertNotIn("AAPL", self.ctl.news_cache)

    def test_the_scope_is_positions_or_the_whole_watchlist(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, dt.date.today() - dt.timedelta(days=3))
        self.assertEqual(self.ctl.feed_symbols(True), ["AAPL"])
        self.assertEqual(self.ctl.feed_symbols(False), self.ctl.symbols)

    def test_the_feed_merges_filters_and_orders_the_cached_news(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, dt.date.today() - dt.timedelta(days=3))
        self.ctl.record_trade("MSFT", "buy", 1, 100, 0, dt.date.today() - dt.timedelta(days=3))
        shared = self.fresh("Apple and Microsoft earnings beat", link="https://x.test/shared")
        self.ctl.store_news("AAPL", [shared, self.fresh("Apple news today", 2)])
        self.ctl.store_news("MSFT", [shared])
        feed = self.ctl.build_feed(positions_only=True, limit=None, show_minor=True)
        self.assertEqual([(i.title, i.symbols) for i in feed.items],
                         [("Apple and Microsoft earnings beat", ["AAPL", "MSFT"]), ("Apple news today", ["AAPL"])])

    def test_positions_only_leaves_out_the_other_stocks(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, dt.date.today() - dt.timedelta(days=3))
        self.ctl.store_news("AAPL", [self.fresh("Apple earnings beat")])
        self.ctl.store_news("MSFT", [self.fresh("Microsoft earnings beat")])
        self.assertEqual([i.title for i in self.ctl.build_feed(True).items], ["Apple earnings beat"])
        self.assertEqual(len(self.ctl.build_feed(False).items), 2)

    def test_a_story_about_results_names_the_calendar_event(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, dt.date.today() - dt.timedelta(days=3))
        self.ctl.record_yahoo_events("AAPL", [(dt.date.today() + dt.timedelta(days=2), "Quartalszahlen")])
        self.ctl.store_news("AAPL", [self.fresh("Apple earnings preview")])
        [found] = self.ctl.build_feed(True).items
        self.assertIn("Termin Quartalszahlen", found.event_note)
        self.assertIn("(erwartet)", found.event_note)

    def test_a_stock_without_loaded_news_adds_nothing(self):
        self.assertEqual(self.ctl.build_feed(False).items, [])

    def test_the_detail_window_shares_its_news_with_the_feed(self):
        self.serve({})
        main = w.MainWindow(self.ctl)
        mock.patch.object(sd, "fetch_news", lambda s, count=15: [self.fresh("Apple earnings beat")]).start()
        self.addCleanup(mock.patch.stopall)
        main.open_detail("AAPL")
        self.assertTrue(wait_until(lambda: (self.ctl.news_cache.get("AAPL") or {}).get("news")))
        self.assertEqual(self.ctl.news_cache["AAPL"]["news"][0][0], "Apple earnings beat")


class NewsFeedWindowTests(AppTestCase):
    def setUp(self):
        super().setUp()
        for symbol in ("AAPL", "MSFT"):
            self.ctl.record_trade(symbol, "buy", 1, 100, 0, dt.date.today() - dt.timedelta(days=3))
        name_the_companies(self.ctl)  # DELL bleibt ohne Position
        self.main = w.MainWindow(self.ctl)
        self.opened = []
        self.addCleanup(lambda: [win.close() for win in list(w.NEWS_WINDOWS.values())])

    def story(self, title, hours=1, link=None):
        stamp = dt.datetime.now(UTC) - dt.timedelta(hours=hours)
        return (title, "Quelle", stamp, link or f"https://x.test/{title.replace(' ', '-')}")

    def serve(self, by_symbol):
        mock.patch.object(sd, "fetch_news", lambda s, count=15: by_symbol.get(s, [])).start()
        self.addCleanup(mock.patch.stopall)

    def open(self):
        w.open_news(self.ctl, self.opened.append)
        return w.NEWS_WINDOWS["window"]

    def rows(self, window):
        return window.findChildren(w.FeedRow)

    def titles(self, window):
        return [row.headline.text() for row in self.rows(window)]

    def texts(self, window):
        return [label.text() for label in window.findChildren(QLabel) if not label.isHidden()]

    def test_it_opens_once_docks_and_frees_its_slot(self):
        window = self.open()
        w.open_news(self.ctl)
        self.assertEqual(len(w.NEWS_WINDOWS), 1)
        self.assertIn(window, w.Dock.windows)
        window.close()
        self.assertNotIn("window", w.NEWS_WINDOWS)
        self.assertNotIn(window, w.Dock.windows)

    def test_it_starts_with_positions_only_and_48_hours(self):
        window = self.open()
        self.assertTrue(window.only_positions.isChecked())
        self.assertTrue(window.hour_buttons[48].isChecked())
        self.assertFalse(window.minor_button.isChecked())

    def test_opening_loads_the_news_of_the_positions_only(self):
        loaded = []
        mock.patch.object(sd, "fetch_news", lambda s, count=15: loaded.append(s) or []).start()
        self.addCleanup(mock.patch.stopall)
        self.open()
        self.assertTrue(wait_until(lambda: sorted(loaded) == ["AAPL", "MSFT"]))

    def test_stories_are_listed_important_first_with_symbols_source_and_tag(self):
        self.serve({"AAPL": [self.story("Apple news today", 1), self.story("Apple earnings beat", 5)],
                    "DELL": [self.story("Dell earnings beat", 1)]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 2))
        self.assertEqual(self.titles(window), ["Apple earnings beat", "Apple news today"])
        first = self.rows(window)[0]
        self.assertEqual(first.badge.text(), "Zahlen")
        self.assertFalse(first.badge.isHidden())
        self.assertTrue(first.meta.text().startswith("AAPL · Quelle · "))
        self.assertTrue(self.rows(window)[1].badge.isHidden())

    def test_a_story_shared_by_two_positions_shows_both_symbols_once(self):
        shared = self.story("Apple and Microsoft chip earnings beat", link="https://x.test/shared")
        self.serve({"AAPL": [shared], "MSFT": [shared]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 1))
        self.assertTrue(self.rows(window)[0].meta.text().startswith("AAPL, MSFT · "))

    def test_the_switch_adds_the_rest_of_the_watchlist(self):
        self.serve({"AAPL": [self.story("Apple earnings beat")], "DELL": [self.story("Dell earnings beat")]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 1))
        window.only_positions.click()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 2))
        window.only_positions.click()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 1))

    def test_the_time_buttons_change_the_range(self):
        self.serve({"AAPL": [self.story("Apple news recent", 5), self.story("Apple news old", 100)]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 1))
        window.hour_buttons[168].click()
        self.assertEqual(len(self.rows(window)), 2)
        window.hour_buttons[24].click()
        self.assertEqual(len(self.rows(window)), 1)
        self.assertTrue(any("älter als 24 Std." in t for t in self.texts(window)))

    def test_noise_is_hidden_with_a_note_and_the_switch_shows_it(self):
        self.serve({"AAPL": [self.story("Why Apple Stock Is Up Today"), self.story("Apple earnings beat")]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 1))
        self.assertTrue(any("1 Kursgerede ausgeblendet" in t for t in self.texts(window)))
        window.minor_button.click()
        self.assertEqual(len(self.rows(window)), 2)
        self.assertFalse(any("Kursgerede ausgeblendet" in t for t in self.texts(window)))

    def test_only_thirty_show_at_first_and_more_adds_thirty(self):
        self.serve({"AAPL": [self.story(f"Apple note number {n}", 1 + n % 40) for n in range(70)]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 30))
        self.assertFalse(window.more_button.isHidden())
        self.assertIn("40 weitere", window.more_button.text())
        window.more_button.click()
        self.assertEqual(len(self.rows(window)), 60)
        window.more_button.click()
        self.assertEqual(len(self.rows(window)), 70)
        self.assertTrue(window.more_button.isHidden())

    def test_the_status_counts_loaded_stocks_and_names_failures(self):
        mock.patch.object(sd, "fetch_news", mock.Mock(side_effect=RuntimeError("offline"))).start()
        self.addCleanup(mock.patch.stopall)
        window = self.open()
        self.assertTrue(wait_until(lambda: "nicht ladbar: AAPL, MSFT" in window.status.text()))
        self.assertIn("News von 0 von 2 Aktien", window.status.text())

    def test_the_status_says_loading_while_news_arrive(self):
        self.ctl.news_loading.update({"AAPL", "MSFT"})
        window = self.open()
        self.assertEqual(window.status.text(), "Lade News … 0 von 2 Aktien")
        self.assertIn("Wird geladen …", self.texts(window))

    def test_an_empty_feed_says_so(self):
        self.serve({})
        window = self.open()
        self.assertTrue(wait_until(lambda: "Keine passenden News." in self.texts(window)))

    def test_without_positions_the_status_explains_the_switch(self):
        self.ctl.record_trade("AAPL", "sell", 1, 100, 0, dt.date.today())
        self.ctl.record_trade("MSFT", "sell", 1, 100, 0, dt.date.today())
        window = self.open()
        self.assertIn("Keine Aktien im Umfang", window.status.text())

    def test_an_event_note_is_shown_on_the_story(self):
        self.ctl.record_yahoo_events("AAPL", [(dt.date.today() + dt.timedelta(days=2), "Quartalszahlen")])
        self.serve({"AAPL": [self.story("Apple earnings preview")]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 1))
        row = self.rows(window)[0]
        self.assertFalse(row.event_note.isHidden())
        self.assertIn("Termin Quartalszahlen", row.event_note.text())

    def test_a_story_without_an_event_has_no_note(self):
        self.serve({"AAPL": [self.story("Apple earnings preview")]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 1))
        self.assertTrue(self.rows(window)[0].event_note.isHidden())

    def test_a_click_opens_the_link(self):
        self.serve({"AAPL": [self.story("Apple earnings beat", link="https://x.test/a")]})
        window = self.open()
        self.assertTrue(wait_until(lambda: len(self.rows(window)) == 1))
        with mock.patch.object(w.QDesktopServices, "openUrl") as open_url:
            QTest.mouseClick(self.rows(window)[0], Qt.LeftButton)
        self.assertEqual(open_url.call_args[0][0].toString(), "https://x.test/a")

    def test_the_reload_button_loads_everything_again(self):
        loaded = []
        mock.patch.object(sd, "fetch_news", lambda s, count=15: loaded.append(s) or []).start()
        self.addCleanup(mock.patch.stopall)
        window = self.open()
        self.assertTrue(wait_until(lambda: len(loaded) == 2 and not self.ctl.news_loading))
        window.reload_button.click()
        self.assertTrue(wait_until(lambda: len(loaded) == 4))

    def test_new_news_appear_without_reopening(self):
        self.serve({})
        window = self.open()
        self.assertTrue(wait_until(lambda: not self.ctl.news_loading))
        self.ctl.store_news("AAPL", [self.story("Apple earnings beat")])
        self.assertTrue(wait_until(lambda: self.titles(window) == ["Apple earnings beat"]))

    def test_the_button_of_the_positions_box_opens_the_feed(self):
        self.serve({})
        self.main.news_button.click()
        self.assertIn("window", w.NEWS_WINDOWS)
        self.assertEqual(self.main.news_button.toolTip(), "News-Feed")

    def test_the_window_can_be_pinned_and_comes_back(self):
        self.serve({})
        window = self.open()
        window.pin_button.click()
        self.assertTrue(self.ctl.is_pinned("news"))
        window.close()
        w.restore_pinned(self.ctl, self.main)
        self.assertIn("window", w.NEWS_WINDOWS)

    def test_closing_disconnects_the_window(self):
        window = self.open()
        window.close()
        self.ctl.news_changed.emit("AAPL")  # darf nichts mehr aufrufen
        self.ctl.terms_changed.emit("AAPL")
        self.ctl.calendar_changed.emit()


if __name__ == "__main__":
    unittest.main()
