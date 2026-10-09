"""Benachrichtigungen: Regeln und Auswertung (alerts.py), Speicher, Controller und Fenster."""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import alerts
import events as evt
import glossary
import newsfeed
import stock_widget as w
from store import Store
from tests.support import AppTestCase, DialogDriver, QUOTE, wait_until

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 9, 12, 0, tzinfo=UTC)


def ctx(**overrides):
    base = dict(now=NOW, quotes={"AAPL": {"price": 100.0, "change_pct": 1.0, "currency": "USD"}}, fresh={"AAPL"},
                positions=["AAPL"], symbols=["AAPL", "MSFT"])
    base.update(overrides)
    return alerts.Context(**base)


def rule(kind="price_above", rule_id=1, symbol="AAPL", threshold=110.0, days=None, **kw):
    return alerts.Rule(rule_id, kind, symbol, threshold, days, **kw)


def merged(day, kind="earnings", title="Quartalszahlen", status="expected", symbol="AAPL", precision="day", end=None, event_id=1):
    event = evt.Event(event_id, symbol, kind, title, day, end or day, precision, status, "Yahoo Finance")
    return evt.merge_duplicates([event], NOW.date())[0]


def story(title="Apple earnings beat", hours=1, importance=3, tag="Zahlen", symbols=("AAPL",), link="https://x.test/a"):
    return newsfeed.FeedItem(title, "Quelle", NOW - dt.timedelta(hours=hours), link, list(symbols), importance, tag)


class CheckRuleTests(unittest.TestCase):
    def test_price_rules_need_a_symbol_and_a_positive_price(self):
        self.assertEqual(alerts.check_rule("price_above", " aapl ", 120, None), ("price_above", "AAPL", 120.0, None))
        for kwargs in (dict(symbol=None, threshold=120), dict(symbol="AAPL", threshold=0), dict(symbol="AAPL", threshold=None),
                       dict(symbol="AAPL", threshold=-5)):
            with self.assertRaises(ValueError):
                alerts.check_rule("price_below", kwargs["symbol"], kwargs["threshold"], None)

    def test_percent_rules_need_a_threshold_between_0_and_100(self):
        self.assertEqual(alerts.check_rule("day_move", None, 5, None), ("day_move", None, 5.0, None))
        self.assertEqual(alerts.check_rule("analyst", "AAPL", 100, None)[2], 100.0)
        for value in (0, -1, 101, None):
            for kind in ("day_move", "analyst"):
                with self.assertRaises(ValueError):
                    alerts.check_rule(kind, None, value, None)

    def test_the_lead_time_is_a_whole_number_of_days_up_to_60(self):
        self.assertEqual(alerts.check_rule("earnings", None, None, 3), ("earnings", None, None, 3))
        self.assertEqual(alerts.check_rule("earnings", None, None, 0)[3], 0)
        for days in (-1, 61, 2.5, None):
            with self.assertRaises(ValueError):
                alerts.check_rule("earnings", None, None, days)

    def test_news_needs_nothing(self):
        self.assertEqual(alerts.check_rule("news", None, None, None), ("news", None, None, None))

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            alerts.check_rule("magie", None, None, None)

    def test_every_rule_describes_itself_in_words(self):
        self.assertEqual(rule("price_above", threshold=250).describe(), "AAPL: Kurs steigt auf 250 oder mehr")
        self.assertEqual(rule("price_below", threshold=90).describe(), "AAPL: Kurs fällt auf 90 oder weniger")
        self.assertEqual(rule("day_move", symbol=None, threshold=5).describe(), "Alle Positionen: Tagesbewegung von mindestens 5 %")
        self.assertEqual(rule("earnings", symbol=None, threshold=None, days=3).describe(),
                         "Alle Positionen: Termin in 3 Tagen oder früher")
        self.assertEqual(rule("news", threshold=None).describe(), "AAPL: wichtige News")
        self.assertIn("2,5" if False else "2.5", rule("analyst", threshold=2.5).describe())


class ScopeTests(unittest.TestCase):
    def test_a_symbol_rule_watches_that_stock_if_it_is_on_the_list(self):
        self.assertEqual(alerts.scope(rule(symbol="MSFT"), ctx()), ["MSFT"])
        self.assertEqual(alerts.scope(rule(symbol="DELL"), ctx()), [])

    def test_a_rule_without_symbol_watches_the_positions_only(self):
        self.assertEqual(alerts.scope(rule(symbol=None), ctx()), ["AAPL"])


class PriceRuleTests(unittest.TestCase):
    def test_below_the_threshold_nothing_happens_and_the_distance_is_explained(self):
        result = alerts.evaluate(rule(threshold=110), ctx())
        self.assertEqual(result.hits, [])
        self.assertIn("noch 10.0 % entfernt", result.lines[0])
        self.assertTrue(result.armed)

    def test_reaching_the_threshold_reports_once_and_disarms(self):
        result = alerts.evaluate(rule(threshold=99), ctx())
        [hit] = result.hits
        self.assertIn("100.00 USD", hit.text)
        self.assertEqual(hit.symbol, "AAPL")
        self.assertFalse(result.armed)

    def test_exactly_the_threshold_counts(self):
        self.assertEqual(len(alerts.evaluate(rule(threshold=100), ctx()).hits), 1)

    def test_a_disarmed_rule_is_silent_until_the_price_returns(self):
        result = alerts.evaluate(rule(threshold=99, armed=False), ctx())
        self.assertEqual(result.hits, [])
        self.assertFalse(result.armed)
        self.assertIn("scharft sich erst wieder", " ".join(result.lines))
        back = alerts.evaluate(rule(threshold=150, armed=False), ctx())
        self.assertTrue(back.armed)

    def test_the_below_rule_works_the_other_way(self):
        self.assertEqual(alerts.evaluate(rule("price_below", threshold=90), ctx()).hits, [])
        self.assertEqual(len(alerts.evaluate(rule("price_below", threshold=100), ctx()).hits), 1)
        self.assertIn("unter", alerts.evaluate(rule("price_below", threshold=120), ctx()).hits[0].text)

    def test_the_above_rule_says_above(self):
        self.assertIn("über", alerts.evaluate(rule(threshold=90), ctx()).hits[0].text)

    def test_a_stale_price_never_triggers_and_the_rule_waits(self):
        result = alerts.evaluate(rule(threshold=50), ctx(fresh=set()))
        self.assertEqual(result.hits, [])
        self.assertFalse(result.ready)
        self.assertTrue(result.armed)
        self.assertIn("kein aktueller Kurs", result.lines[0])

    def test_a_stock_off_the_list_cannot_trigger(self):
        result = alerts.evaluate(rule(symbol="DELL", threshold=1), ctx())
        self.assertEqual((result.hits, result.ready), ([], False))

    def test_two_triggers_have_different_keys_so_a_second_alert_is_possible(self):
        first = alerts.evaluate(rule(threshold=99), ctx()).hits[0].key
        later = alerts.evaluate(rule(threshold=99), ctx(now=NOW + dt.timedelta(minutes=5))).hits[0].key
        self.assertNotEqual(first, later)


class DayMoveRuleTests(unittest.TestCase):
    def quotes(self, **moves):
        return {s: {"price": 10.0, "change_pct": m, "currency": "USD"} for s, m in moves.items()}

    def test_a_big_rise_or_fall_triggers_once_per_stock_and_day(self):
        data = ctx(quotes=self.quotes(AAPL=-6.5, MSFT=2.0), fresh={"AAPL", "MSFT"}, positions=["AAPL", "MSFT"])
        result = alerts.evaluate(rule("day_move", symbol=None, threshold=5), data)
        [hit] = result.hits
        self.assertEqual((hit.symbol, hit.key), ("AAPL", "move:1:AAPL:2026-10-09"))
        self.assertIn("gefallen", hit.text)
        self.assertIn("6.5 %", hit.text)

    def test_a_rise_says_risen(self):
        data = ctx(quotes=self.quotes(AAPL=7.0))
        self.assertIn("gestiegen", alerts.evaluate(rule("day_move", symbol="AAPL", threshold=5), data).hits[0].text)

    def test_below_the_threshold_nothing_happens(self):
        data = ctx(quotes=self.quotes(AAPL=4.99))
        result = alerts.evaluate(rule("day_move", symbol="AAPL", threshold=5), data)
        self.assertEqual(result.hits, [])
        self.assertIn("+4.99 %", result.lines[0])

    def test_another_day_has_another_key(self):
        data = ctx(quotes=self.quotes(AAPL=9.0))
        tomorrow = ctx(quotes=self.quotes(AAPL=9.0), now=NOW + dt.timedelta(days=1))
        r = rule("day_move", symbol="AAPL", threshold=5)
        self.assertNotEqual(alerts.evaluate(r, data).hits[0].key, alerts.evaluate(r, tomorrow).hits[0].key)

    def test_the_same_day_has_the_same_key_so_it_is_not_reported_twice(self):
        data = ctx(quotes=self.quotes(AAPL=9.0))
        later = ctx(quotes=self.quotes(AAPL=9.0), now=NOW + dt.timedelta(hours=3))
        r = rule("day_move", symbol="AAPL", threshold=5)
        self.assertEqual(alerts.evaluate(r, data).hits[0].key, alerts.evaluate(r, later).hits[0].key)

    def test_stale_quotes_are_ignored(self):
        data = ctx(quotes=self.quotes(AAPL=20.0), fresh=set())
        self.assertEqual(alerts.evaluate(rule("day_move", symbol="AAPL", threshold=5), data).hits, [])

    def test_only_the_positions_are_watched_without_a_symbol(self):
        data = ctx(quotes=self.quotes(AAPL=1.0, MSFT=30.0), fresh={"AAPL", "MSFT"}, positions=["AAPL"])
        self.assertEqual(alerts.evaluate(rule("day_move", symbol=None, threshold=5), data).hits, [])

    def test_the_explanation_says_when_nothing_moved(self):
        result = alerts.evaluate(rule("day_move", symbol=None, threshold=5), ctx())
        self.assertIn("Keine Aktie hat sich heute um 5 % oder mehr bewegt", result.lines[0])


class EarningsRuleTests(unittest.TestCase):
    def evaluate(self, events, days=3, **kw):
        return alerts.evaluate(rule("earnings", symbol=None, threshold=None, days=days),
                               ctx(events={"AAPL": events}, **kw))

    def test_an_event_inside_the_lead_time_is_reported_with_status_and_source(self):
        result = self.evaluate([merged(dt.date(2026, 10, 11))])
        [hit] = result.hits
        self.assertIn("Quartalszahlen", hit.title)
        self.assertIn("in 2 Tagen", hit.title)
        self.assertIn("Status erwartet", hit.text)
        self.assertIn("Yahoo Finance", hit.text)

    def test_an_event_today_is_reported(self):
        self.assertEqual(len(self.evaluate([merged(dt.date(2026, 10, 9))]).hits), 1)

    def test_an_event_beyond_the_lead_time_is_not(self):
        self.assertEqual(self.evaluate([merged(dt.date(2026, 10, 13))]).hits, [])
        self.assertEqual(len(self.evaluate([merged(dt.date(2026, 10, 12))]).hits), 1)

    def test_past_and_occurred_events_are_not_reported(self):
        self.assertEqual(self.evaluate([merged(dt.date(2026, 10, 8))]).hits, [])

    def test_an_overdue_speculative_event_is_not_reminded(self):
        overdue = merged(dt.date(2026, 10, 7), kind="product", title="GTA VI", status="speculative")
        self.assertEqual(overdue.status, "speculative")  # bleibt spekulativ, gilt aber nicht als bevorstehend
        self.assertEqual(self.evaluate([overdue]).hits, [])

    def test_an_event_yesterday_is_not_reminded_even_if_still_expected(self):
        self.assertEqual(self.evaluate([merged(dt.date(2026, 10, 8), status="speculative")]).hits, [])

    def test_a_speculative_event_is_never_called_confirmed(self):
        [hit] = self.evaluate([merged(dt.date(2026, 10, 10), kind="product", title="GTA VI", status="speculative")]).hits
        self.assertIn("Status spekulativ", hit.text)
        self.assertNotIn("bestätigt", hit.text)

    def test_a_confirmed_event_says_so(self):
        [hit] = self.evaluate([merged(dt.date(2026, 10, 10), status="confirmed")]).hits
        self.assertIn("Status bestätigt", hit.text)

    def test_events_without_an_exact_day_are_not_reminded(self):
        month = merged(dt.date(2026, 10, 1), precision="month", end=dt.date(2026, 10, 31))
        self.assertEqual(self.evaluate([month]).hits, [])

    def test_the_key_names_the_event_and_its_date_so_a_move_is_reported_again(self):
        first = self.evaluate([merged(dt.date(2026, 10, 11))]).hits[0].key
        moved = self.evaluate([merged(dt.date(2026, 10, 12))]).hits[0].key
        self.assertNotEqual(first, moved)
        self.assertEqual(first, self.evaluate([merged(dt.date(2026, 10, 11))]).hits[0].key)

    def test_nothing_to_report_is_explained(self):
        self.assertIn("Kein Termin in den nächsten 3 Tagen", self.evaluate([]).lines[0])

    def test_only_positions_are_reminded_without_a_symbol(self):
        result = alerts.evaluate(rule("earnings", symbol=None, threshold=None, days=3),
                                 ctx(events={"MSFT": [merged(dt.date(2026, 10, 10), symbol="MSFT")]}))
        self.assertEqual(result.hits, [])


class NewsRuleTests(unittest.TestCase):
    def evaluate(self, items, loaded=("AAPL",), symbol=None):
        return alerts.evaluate(rule("news", symbol=symbol, threshold=None), ctx(news=items, news_loaded=set(loaded)))

    def test_a_recent_important_story_is_reported_with_its_tag(self):
        [hit] = self.evaluate([story()]).hits
        self.assertEqual((hit.title, hit.text, hit.symbol), ("AAPL: Zahlen", "Apple earnings beat", "AAPL"))

    def test_normal_and_interesting_stories_are_not(self):
        self.assertEqual(self.evaluate([story(importance=2), story(importance=1, link="https://x.test/b")]).hits, [])

    def test_a_story_older_than_a_day_is_not(self):
        self.assertEqual(self.evaluate([story(hours=25)]).hits, [])
        self.assertEqual(len(self.evaluate([story(hours=23)]).hits), 1)

    def test_a_story_without_a_time_is_not(self):
        item = story()
        item.published = None
        self.assertEqual(self.evaluate([item]).hits, [])

    def test_a_story_about_another_stock_is_not(self):
        self.assertEqual(self.evaluate([story(symbols=("MSFT",))]).hits, [])

    def test_the_key_comes_from_the_address_so_it_is_stable(self):
        a = self.evaluate([story(link="https://www.x.test/a?utm=1")]).hits[0].key
        b = self.evaluate([story(link="https://x.test/a")]).hits[0].key
        self.assertEqual(a, b)

    def test_without_loaded_news_the_rule_is_not_ready(self):
        result = self.evaluate([], loaded=())
        self.assertFalse(result.ready)
        self.assertIn("noch nicht geladen", result.lines[0])

    def test_no_important_story_is_explained(self):
        self.assertIn("Keine wichtige News", self.evaluate([]).lines[0])

    def test_a_story_for_two_stocks_names_those_that_are_watched(self):
        [hit] = self.evaluate([story(symbols=("AAPL", "MSFT"))]).hits
        self.assertEqual(hit.title, "AAPL: Zahlen")


class AnalystRuleTests(unittest.TestCase):
    def evaluate(self, targets, threshold=5.0):
        return alerts.evaluate(rule("analyst", symbol=None, threshold=threshold), ctx(targets=targets))

    def test_without_a_reference_the_value_is_remembered_not_reported(self):
        result = self.evaluate({"AAPL": {"mean": 200.0, "currency": "USD", "reference": None}})
        self.assertEqual((result.hits, result.references, result.ready), ([], {"AAPL": 200.0}, True))

    def test_a_change_beyond_the_threshold_is_reported_and_becomes_the_new_reference(self):
        result = self.evaluate({"AAPL": {"mean": 220.0, "currency": "USD", "reference": 200.0}})
        [hit] = result.hits
        self.assertIn("+10.0 %", hit.title)
        self.assertIn("Schätzungen", hit.text)
        self.assertEqual(result.references, {"AAPL": 220.0})

    def test_a_cut_is_reported_too(self):
        result = self.evaluate({"AAPL": {"mean": 180.0, "currency": "USD", "reference": 200.0}})
        self.assertIn("-10.0 %", result.hits[0].title)

    def test_a_small_change_is_not_reported_and_keeps_the_reference(self):
        result = self.evaluate({"AAPL": {"mean": 204.0, "currency": "USD", "reference": 200.0}})
        self.assertEqual((result.hits, result.references), ([], {}))
        self.assertIn("Schwelle 5 %", result.lines[0])

    def test_without_targets_the_rule_is_not_ready(self):
        result = self.evaluate({})
        self.assertFalse(result.ready)
        self.assertIn("noch nicht geladen", result.lines[0])

    def test_the_key_names_the_new_value(self):
        a = self.evaluate({"AAPL": {"mean": 220.0, "currency": "USD", "reference": 200.0}}).hits[0].key
        b = self.evaluate({"AAPL": {"mean": 230.0, "currency": "USD", "reference": 200.0}}).hits[0].key
        self.assertNotEqual(a, b)


class SettingsTests(unittest.TestCase):
    def test_defaults(self):
        s = alerts.Settings()
        self.assertTrue(s.enabled)
        self.assertTrue(all(s.type_enabled(kind) for kind in alerts.KINDS))
        self.assertEqual((s.digest_mode, s.digest_hour, s.digest_weekday), ("off", 18, 4))

    def test_roundtrip(self):
        s = alerts.Settings(False, tuple((k, k != "news") for k in alerts.KINDS), "weekly", 7, 0)
        self.assertEqual(alerts.Settings.from_dict(s.to_dict()), s)
        self.assertFalse(s.type_enabled("news"))
        self.assertTrue(s.type_enabled("analyst"))

    def test_broken_values_fall_back_to_the_defaults(self):
        s = alerts.Settings.from_dict({"digest_mode": "stündlich", "digest_hour": 99, "digest_weekday": "Fr",
                                      "types": "kaputt"})
        self.assertEqual((s.digest_mode, s.digest_hour, s.digest_weekday), ("off", 18, 4))
        self.assertTrue(s.type_enabled("news"))
        self.assertEqual(alerts.Settings.from_dict(None), alerts.Settings())
        self.assertEqual(alerts.Settings.from_dict([1, 2]), alerts.Settings())

    def test_a_missing_type_counts_as_on(self):
        self.assertTrue(alerts.Settings.from_dict({"types": {"news": False}}).type_enabled("price_above"))


class DigestTests(unittest.TestCase):
    def at(self, day, hour):
        return dt.datetime(2026, 10, day, hour, 0, tzinfo=UTC)   # 9.10.2026 ist ein Freitag

    def test_off_never_comes_due(self):
        self.assertIsNone(alerts.digest_key(alerts.Settings(), self.at(9, 23)))

    def test_daily_comes_due_from_the_chosen_hour_with_one_key_per_day(self):
        s = alerts.Settings(digest_mode="daily", digest_hour=18)
        self.assertIsNone(alerts.digest_key(s, self.at(9, 17)))
        self.assertEqual(alerts.digest_key(s, self.at(9, 18)), "digest:day:2026-10-09")
        self.assertEqual(alerts.digest_key(s, self.at(9, 23)), "digest:day:2026-10-09")
        self.assertEqual(alerts.digest_key(s, self.at(10, 19)), "digest:day:2026-10-10")

    def test_weekly_comes_due_on_the_chosen_weekday_and_catches_up_later_that_week(self):
        s = alerts.Settings(digest_mode="weekly", digest_hour=18, digest_weekday=4)
        self.assertIsNone(alerts.digest_key(s, self.at(8, 23)))      # Donnerstag
        self.assertIsNone(alerts.digest_key(s, self.at(9, 17)))      # Freitag vor der Zeit
        key = alerts.digest_key(s, self.at(9, 18))
        self.assertEqual(key, "digest:week:2026-41")
        self.assertEqual(alerts.digest_key(s, self.at(11, 9)), key)  # Sonntag: nachgeholt
        self.assertEqual(alerts.digest_key(s, self.at(16, 20)), "digest:week:2026-42")  # nächster Freitag

    def test_the_text_covers_moves_events_and_news(self):
        data = ctx(quotes={"AAPL": {"price": 1, "change_pct": 3.0, "currency": "USD"},
                           "MSFT": {"price": 1, "change_pct": -2.0, "currency": "USD"}},
                   fresh={"AAPL", "MSFT"}, positions=["AAPL", "MSFT"],
                   events={"AAPL": [merged(dt.date(2026, 10, 14))]}, news=[story()])
        title, text = alerts.build_digest(alerts.Settings(digest_mode="daily"), data)
        self.assertEqual(title, "Tageszusammenfassung 09.10.2026")
        self.assertIn("1 im Plus, 1 im Minus", text)
        self.assertIn("Stärkste: AAPL +3.0 %, schwächste: MSFT -2.0 %", text)
        self.assertIn("14.10. AAPL Quartalszahlen (erwartet)", text)
        self.assertIn("Wichtige News: 1", text)

    def test_the_weekly_title_names_the_week(self):
        title, _ = alerts.build_digest(alerts.Settings(digest_mode="weekly"), ctx())
        self.assertEqual(title, "Wochenzusammenfassung KW 41")

    def test_without_events_the_text_says_so(self):
        _, text = alerts.build_digest(alerts.Settings(digest_mode="daily"), ctx())
        self.assertIn("Keine Termine der Positionen in den nächsten 7 Tagen", text)

    def test_stale_quotes_are_left_out_of_the_moves(self):
        _, text = alerts.build_digest(alerts.Settings(digest_mode="daily"), ctx(fresh=set()))
        self.assertNotIn("im Plus", text)

    def test_the_message_is_shortened_for_the_taskbar(self):
        self.assertEqual(alerts.short("a  b\nc"), "a b c")
        self.assertEqual(len(alerts.short("x" * 500)), 240)
        self.assertTrue(alerts.short("x" * 500).endswith("…"))


class StoreAlertTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "test.db")
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_a_rule_roundtrips_and_starts_armed_and_enabled(self):
        rule_id = self.store.add_rule("price_above", "AAPL", 120.0, None, NOW)
        self.assertEqual(self.store.rules(), [(rule_id, "price_above", "AAPL", 120.0, None, 1, 1, 0)])

    def test_changing_a_rule_rearms_it(self):
        rule_id = self.store.add_rule("price_above", "AAPL", 120.0, None, NOW)
        self.store.set_rule_state(rule_id, armed=False)
        self.store.update_rule(rule_id, "MSFT", 130.0, None)
        self.assertEqual(self.store.rules()[0][2:], ("MSFT", 130.0, None, 1, 1, 0))

    def test_state_changes_touch_only_what_is_given(self):
        rule_id = self.store.add_rule("news", None, None, None, NOW)
        self.store.set_rule_state(rule_id, baselined=True)
        self.store.set_rule_state(rule_id, enabled=False)
        self.assertEqual(self.store.rules()[0][5:], (0, 1, 1))

    def test_a_rule_can_be_deleted(self):
        rule_id = self.store.add_rule("news", None, None, None, NOW)
        self.store.delete_rule(rule_id)
        self.assertEqual(self.store.rules(), [])

    def test_the_same_key_is_logged_only_once(self):
        self.assertTrue(self.store.log_alert(1, "k", "AAPL", "Titel", "Text", NOW))
        self.assertFalse(self.store.log_alert(1, "k", "AAPL", "Titel", "Text", NOW))
        self.assertTrue(self.store.alert_logged("k"))
        self.assertFalse(self.store.alert_logged("anderer"))
        self.assertEqual(len(self.store.alert_log()), 1)

    def test_silent_entries_prevent_repeats_but_are_not_listed_or_unread(self):
        self.store.log_alert(1, "alt", None, "Titel", "Text", NOW, silent=True)
        self.assertTrue(self.store.alert_logged("alt"))
        self.assertEqual((self.store.alert_log(), self.store.unread_alerts()), ([], 0))

    def test_the_log_is_newest_first_and_marks_unread(self):
        self.store.log_alert(1, "a", "AAPL", "Erste", "x", NOW)
        self.store.log_alert(1, "b", None, "Zweite", "y", NOW + dt.timedelta(minutes=1))
        self.assertEqual([e["title"] for e in self.store.alert_log()], ["Zweite", "Erste"])
        self.assertEqual(self.store.unread_alerts(), 2)
        self.store.mark_alerts_read()
        self.assertEqual(self.store.unread_alerts(), 0)
        self.assertTrue(all(e["read"] for e in self.store.alert_log()))

    def test_the_log_limit_applies(self):
        for n in range(5):
            self.store.log_alert(1, f"k{n}", None, f"T{n}", "x", NOW)
        self.assertEqual(len(self.store.alert_log(limit=3)), 3)

    def test_everything_survives_a_restart(self):
        self.store.add_rule("news", None, None, None, NOW)
        self.store.log_alert(1, "k", None, "T", "x", NOW)
        self.store.close()
        again = Store(self.path)
        self.addCleanup(again.close)
        self.assertEqual(len(again.rules()), 1)
        self.assertTrue(again.alert_logged("k"))

    def test_an_older_database_gets_the_tables(self):
        self.store.db.execute("DROP TABLE alert_rules")
        self.store.db.execute("DROP TABLE alert_log")
        self.store.db.commit()
        self.store.close()
        again = Store(self.path)
        self.addCleanup(again.close)
        self.assertEqual(again.rules(), [])
        self.assertEqual(again.meta("schema_version"), "8")


class ControllerAlertTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.record_trade("AAPL", "buy", 10, 90.0, 0.0, dt.date.today() - dt.timedelta(days=30))
        self.set_quote(100.0)
        self.fired = []
        self.ctl.alert_fired.connect(lambda title, text: self.fired.append((title, text)))

    def set_quote(self, price, change=1.0, fresh=True, symbol="AAPL"):
        self.ctl.quotes[symbol] = {**QUOTE, "price": price, "change_pct": change}
        self.ctl.quote_times[symbol] = dt.datetime.now() if fresh else dt.datetime.now() - dt.timedelta(hours=5)
        self.ctl.quote_errors.pop(symbol, None)

    def test_rules_are_validated_when_created(self):
        with self.assertRaises(ValueError):
            self.ctl.add_rule("price_above", "AAPL", -1, None)
        with self.assertRaises(ValueError):
            self.ctl.add_rule("price_above", "GIBTESNICHT", 100, None)
        self.assertEqual(self.ctl.alert_rules, [])

    def test_a_rule_can_be_added_changed_switched_and_deleted(self):
        seen = []
        self.ctl.alerts_changed.connect(lambda: seen.append(1))
        rule_id = self.ctl.add_rule("price_above", "aapl", 120, None)
        [stored] = self.ctl.alert_rules
        self.assertEqual((stored.id, stored.symbol, stored.threshold, stored.enabled), (rule_id, "AAPL", 120.0, True))
        self.ctl.update_rule(rule_id, "MSFT", 130, None)
        self.assertEqual((self.ctl.alert_rules[0].symbol, self.ctl.alert_rules[0].threshold), ("MSFT", 130.0))
        self.ctl.set_rule_enabled(rule_id, False)
        self.assertFalse(self.ctl.alert_rules[0].enabled)
        self.ctl.delete_rule(rule_id)
        self.assertEqual(self.ctl.alert_rules, [])
        self.assertEqual(len(seen), 4)

    def test_changing_a_vanished_rule_is_refused(self):
        with self.assertRaises(ValueError):
            self.ctl.update_rule(999, "AAPL", 100, None)

    def test_changing_a_rule_validates_too(self):
        rule_id = self.ctl.add_rule("price_above", "AAPL", 120, None)
        with self.assertRaises(ValueError):
            self.ctl.update_rule(rule_id, "AAPL", 0, None)
        self.assertEqual(self.ctl.alert_rules[0].threshold, 120.0)

    def test_a_price_rule_fires_when_reached_and_only_once(self):
        self.ctl.add_rule("price_above", "AAPL", 99, None)
        [(title, text)] = self.ctl.check_alerts()
        self.assertEqual(title, "AAPL: Kursalarm")
        self.assertEqual(self.fired, [(title, alerts_short(text))])
        self.assertEqual(self.ctl.check_alerts(), [])
        self.assertEqual(self.ctl.check_alerts(now=dt.datetime.now().astimezone() + dt.timedelta(minutes=10)), [])
        self.assertEqual(len(self.fired), 1)
        self.assertFalse(self.ctl.alert_rules[0].armed)

    def test_it_fires_again_after_the_price_went_back_and_crossed_again(self):
        self.ctl.add_rule("price_above", "AAPL", 110, None)
        self.assertEqual(self.ctl.check_alerts(), [])
        self.set_quote(111.0)
        self.assertEqual(len(self.ctl.check_alerts()), 1)
        self.set_quote(105.0)
        self.assertEqual(self.ctl.check_alerts(), [])
        self.assertTrue(self.ctl.alert_rules[0].armed)
        self.set_quote(112.0)
        self.assertEqual(len(self.ctl.check_alerts(now=dt.datetime.now().astimezone() + dt.timedelta(seconds=5))), 1)
        self.assertEqual(len(self.ctl.alert_log()), 2)

    def test_a_stale_price_does_not_fire_and_does_not_disarm(self):
        self.ctl.add_rule("price_above", "AAPL", 50, None)
        self.set_quote(100.0, fresh=False)
        self.assertEqual(self.ctl.check_alerts(), [])
        self.assertTrue(self.ctl.alert_rules[0].armed)
        self.set_quote(100.0)
        self.assertEqual(len(self.ctl.check_alerts()), 1)

    def test_a_disabled_rule_does_not_fire(self):
        rule_id = self.ctl.add_rule("price_above", "AAPL", 50, None)
        self.ctl.set_rule_enabled(rule_id, False)
        self.assertEqual(self.ctl.check_alerts(), [])

    def test_a_disabled_type_does_not_fire_but_keeps_the_rule(self):
        self.ctl.add_rule("price_above", "AAPL", 50, None)
        self.ctl.set_alert_settings(alerts.Settings(True, tuple((k, k != "price_above") for k in alerts.KINDS)))
        self.assertEqual(self.ctl.check_alerts(), [])
        self.assertEqual(len(self.ctl.alert_rules), 1)
        self.ctl.set_alert_settings(alerts.Settings())
        self.assertEqual(len(self.ctl.check_alerts()), 1)

    def test_switching_everything_off_silences_everything(self):
        self.ctl.add_rule("price_above", "AAPL", 50, None)
        self.ctl.set_alert_settings(alerts.Settings(enabled=False))
        self.assertEqual(self.ctl.check_alerts(), [])
        self.assertEqual(self.fired, [])

    def test_a_day_move_fires_once_per_day(self):
        self.ctl.add_rule("day_move", None, 5, None)
        self.set_quote(100.0, change=-6.0)
        self.assertEqual(len(self.ctl.check_alerts()), 1)
        self.assertEqual(self.ctl.check_alerts(), [])

    def test_an_event_reminder_fires_once_for_a_coming_event(self):
        self.ctl.record_yahoo_events("AAPL", [(dt.date.today() + dt.timedelta(days=2), "Quartalszahlen")])
        self.ctl.add_rule("earnings", None, None, 3)
        [(title, text)] = self.ctl.check_alerts()
        self.assertIn("Quartalszahlen", title)
        self.assertIn("erwartet", text)
        self.assertEqual(self.ctl.check_alerts(), [])

    def test_a_news_rule_remembers_the_current_state_first_and_then_reports_new_stories(self):
        self.ctl.instruments["AAPL"] = {"name": "Apple Inc.", "source": "Test", "fetched_at": dt.datetime.now()}
        stamp = dt.datetime.now(UTC)
        old = [("Apple earnings beat", "Q", stamp - dt.timedelta(hours=2), "https://x.test/1")]
        self.ctl.add_rule("news", None, None, None)
        self.assertEqual(self.ctl.check_alerts(), [])            # noch keine News geladen: kein stiller Start
        self.assertFalse(self.ctl.alert_rules[0].baselined)
        self.ctl.store_news("AAPL", old)
        self.assertEqual(self.ctl.check_alerts(), [])            # Bestand wird gemerkt, nicht gemeldet
        self.assertTrue(self.ctl.alert_rules[0].baselined)
        self.assertEqual(self.ctl.alert_log(), [])
        self.ctl.store_news("AAPL", old + [("Apple raises outlook", "Q", stamp, "https://x.test/2")])
        [(title, text)] = self.ctl.check_alerts()
        self.assertEqual((title, text), ("AAPL: Prognose", "Apple raises outlook"))
        self.assertEqual(self.ctl.check_alerts(), [])

    def test_an_analyst_rule_remembers_the_first_target_and_reports_a_change(self):
        self.ctl.add_rule("analyst", None, 5, None)
        self.assertEqual(self.ctl.check_alerts(), [])
        self.ctl.targets["AAPL"] = {"mean": 200.0, "currency": "USD", "fetched_at": dt.datetime.now()}
        self.assertEqual(self.ctl.check_alerts(), [])
        self.assertTrue(self.ctl.alert_rules[0].baselined)
        self.ctl.targets["AAPL"]["mean"] = 203.0
        self.assertEqual(self.ctl.check_alerts(), [])
        self.ctl.targets["AAPL"]["mean"] = 220.0
        [(title, _)] = self.ctl.check_alerts()
        self.assertIn("+10.0 %", title)
        self.ctl.targets["AAPL"]["mean"] = 221.0                  # Vergleich ist jetzt 220
        self.assertEqual(self.ctl.check_alerts(), [])

    def test_a_test_reports_what_would_happen_and_changes_nothing(self):
        self.ctl.add_rule("price_above", "AAPL", 99, None)
        [stored] = self.ctl.alert_rules
        result = self.ctl.test_rule(stored)
        self.assertTrue(result.would_fire)
        self.assertEqual(self.ctl.alert_log(), [])
        self.assertTrue(self.ctl.alert_rules[0].armed)
        self.assertEqual(self.fired, [])
        self.assertEqual(len(self.ctl.check_alerts()), 1)         # und danach meldet sie trotzdem

    def test_a_test_ignores_the_armed_state_to_show_the_condition(self):
        self.ctl.add_rule("price_above", "AAPL", 99, None)
        self.ctl.check_alerts()
        [stored] = self.ctl.alert_rules
        self.assertFalse(stored.armed)
        self.assertTrue(self.ctl.test_rule(stored).would_fire)

    def test_a_test_of_an_unmet_rule_explains_why(self):
        self.ctl.add_rule("price_above", "AAPL", 150, None)
        result = self.ctl.test_rule(self.ctl.alert_rules[0])
        self.assertFalse(result.would_fire)
        self.assertIn("noch 50.0 % entfernt", result.lines[0])

    def test_the_digest_comes_once_and_only_with_current_quotes(self):
        self.ctl.set_alert_settings(alerts.Settings(digest_mode="daily", digest_hour=0))
        self.set_quote(100.0, fresh=False)
        self.assertEqual(self.ctl.check_alerts(), [])             # Kurse fehlen noch: später
        self.set_quote(100.0, change=2.0)
        [(title, text)] = self.ctl.check_alerts()
        self.assertTrue(title.startswith("Tageszusammenfassung"))
        self.assertIn("im Plus", text)
        self.assertEqual(self.ctl.check_alerts(), [])
        self.assertEqual(len(self.ctl.alert_log()), 1)

    def test_a_digest_without_positions_is_not_held_back(self):
        self.ctl.record_trade("AAPL", "sell", 10, 100.0, 0.0, dt.date.today())
        self.ctl.set_alert_settings(alerts.Settings(digest_mode="daily", digest_hour=0))
        self.assertEqual(len(self.ctl.check_alerts()), 1)

    def test_the_digest_preview_works_even_when_switched_off(self):
        title, text = self.ctl.digest_preview()
        self.assertTrue(title.startswith("Tageszusammenfassung"))
        self.assertEqual(self.ctl.alert_log(), [])

    def test_the_log_unread_count_and_marking(self):
        self.ctl.add_rule("price_above", "AAPL", 50, None)
        self.ctl.check_alerts()
        self.assertEqual(self.ctl.unread_alerts(), 1)
        self.ctl.mark_alerts_read()
        self.assertEqual(self.ctl.unread_alerts(), 0)

    def test_deleting_a_rule_keeps_its_log(self):
        rule_id = self.ctl.add_rule("price_above", "AAPL", 50, None)
        self.ctl.check_alerts()
        self.ctl.delete_rule(rule_id)
        self.assertEqual(len(self.ctl.alert_log()), 1)

    def test_settings_survive_a_restart(self):
        settings = alerts.Settings(False, tuple((k, k != "news") for k in alerts.KINDS), "weekly", 7, 2)
        self.ctl.set_alert_settings(settings)
        self.ctl.reload_alerts()
        self.assertEqual(self.ctl.alert_settings, settings)

    def test_the_message_for_the_taskbar_is_shortened_but_the_log_keeps_the_full_text(self):
        self.ctl.instruments["AAPL"] = {"name": "Apple Inc.", "source": "Test", "fetched_at": dt.datetime.now()}
        self.ctl.add_rule("news", None, None, None)
        self.ctl.store_news("AAPL", [])
        self.ctl.check_alerts()                                    # Ist-Zustand merken
        long_title = "Apple earnings beat " + "wirklich " * 60
        self.ctl.store_news("AAPL", [(long_title, "Q", dt.datetime.now(UTC), "https://x.test/lang")])
        self.ctl.check_alerts()
        self.assertEqual(len(self.fired), 1)
        self.assertLessEqual(len(self.fired[0][1]), 240)
        self.assertTrue(self.fired[0][1].endswith("…"))
        self.assertEqual(self.ctl.alert_log()[0]["text"], long_title)

    def test_a_new_rule_schedules_a_check_and_the_check_runs_by_itself(self):
        self.ctl.add_rule("price_above", "AAPL", 50, None)
        self.assertTrue(self.ctl.alert_timer.isActive())
        self.assertTrue(wait_until(lambda: len(self.fired) == 1, timeout=4000))

    def test_new_data_triggers_a_check_when_rules_exist(self):
        self.ctl.add_rule("price_above", "AAPL", 150, None)
        self.assertTrue(wait_until(lambda: not self.ctl.alert_timer.isActive(), timeout=4000))
        self.set_quote(160.0)
        self.ctl.changed.emit()
        self.assertTrue(wait_until(lambda: len(self.fired) == 1, timeout=4000))

    def test_without_rules_nothing_is_scheduled(self):
        self.ctl.changed.emit()
        self.assertFalse(self.ctl.alert_timer.isActive())

    def test_the_data_for_news_and_analyst_rules_is_loaded_only_when_needed(self):
        with mock.patch.object(self.ctl, "load_news") as news, mock.patch.object(self.ctl, "load_targets") as targets:
            self.ctl.refresh_alert_data()
            self.assertEqual((news.call_count, targets.call_count), (0, 0))
            self.ctl.add_rule("news", None, None, None)
            self.ctl.add_rule("analyst", "MSFT", 5, None)
            self.ctl.refresh_alert_data()
            news.assert_called_once_with("AAPL")        # nur die Positionen
            targets.assert_called_once_with("MSFT")     # genau die genannte Aktie

    def test_no_data_is_loaded_for_switched_off_rules_or_types(self):
        self.ctl.add_rule("news", None, None, None)
        with mock.patch.object(self.ctl, "load_news") as news:
            self.ctl.set_alert_settings(alerts.Settings(True, tuple((k, k != "news") for k in alerts.KINDS)))
            self.ctl.refresh_alert_data()
            self.ctl.set_alert_settings(alerts.Settings(enabled=False))
            self.ctl.refresh_alert_data()
            self.assertEqual(news.call_count, 0)

    def test_targets_are_loaded_once_per_interval(self):
        calls = []
        mock.patch.object(w.sd, "fetch_targets", lambda s: calls.append(s) or {"mean": 200.0, "currency": "USD"}).start()
        self.addCleanup(mock.patch.stopall)
        self.assertTrue(self.ctl.load_targets("AAPL"))
        self.assertFalse(self.ctl.load_targets("AAPL"))
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.targets))
        self.assertFalse(self.ctl.load_targets("AAPL"))
        self.assertTrue(self.ctl.load_targets("AAPL", now=dt.datetime.now() + w.TARGETS_MAX_AGE + dt.timedelta(seconds=1)))
        self.assertTrue(wait_until(lambda: len(calls) == 2))

    def test_a_failed_target_request_is_forgotten_so_it_can_be_retried(self):
        mock.patch.object(w.sd, "fetch_targets", mock.Mock(side_effect=RuntimeError("offline"))).start()
        self.addCleanup(mock.patch.stopall)
        self.ctl.load_targets("AAPL")
        self.assertTrue(wait_until(lambda: not self.ctl.targets_loading))
        self.assertNotIn("AAPL", self.ctl.targets)
        self.assertTrue(self.ctl.load_targets("AAPL"))


def alerts_short(text):
    return alerts.short(text)


class AlertsWindowTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.record_trade("AAPL", "buy", 10, 90.0, 0.0, dt.date.today() - dt.timedelta(days=30))
        self.ctl.quotes["AAPL"] = {**QUOTE, "price": 100.0}
        self.ctl.quote_times["AAPL"] = dt.datetime.now()
        self.main = w.MainWindow(self.ctl)
        self.opened = []
        self.addCleanup(lambda: [win.close() for win in list(w.ALERT_WINDOWS.values())])

    def open(self):
        w.open_alerts(self.ctl, self.opened.append)
        return w.ALERT_WINDOWS["window"]

    def rows(self, window):
        return window.findChildren(w.AlertRuleRow)

    def texts(self, window):
        QApplication.processEvents()
        return [label.text() for label in window.findChildren(QLabel) if not label.isHidden()]

    def test_it_opens_once_docks_and_frees_its_slot(self):
        window = self.open()
        w.open_alerts(self.ctl)
        self.assertEqual(len(w.ALERT_WINDOWS), 1)
        self.assertIn(window, w.Dock.windows)
        window.close()
        self.assertNotIn("window", w.ALERT_WINDOWS)
        self.assertNotIn(window, w.Dock.windows)

    def test_without_rules_it_says_how_to_start(self):
        window = self.open()
        self.assertEqual(self.rows(window), [])
        self.assertTrue(any("Noch keine Regeln" in t for t in self.texts(window)))
        self.assertTrue(any("Noch keine Meldungen" in t for t in self.texts(window)))

    def test_rules_are_listed_with_their_description_and_state(self):
        self.ctl.add_rule("price_above", "AAPL", 250, None)
        self.ctl.add_rule("news", None, None, None)
        window = self.open()
        rows = self.rows(window)
        self.assertEqual([r.title.text() for r in rows], ["AAPL: Kurs steigt auf 250 oder mehr", "Alle Positionen: wichtige News"])
        self.assertEqual(rows[0].state.text(), "wartet auf die Schwelle")
        self.assertEqual(rows[1].state.text(), "merkt sich zuerst den Ist-Zustand")

    def test_every_rule_title_explains_itself(self):
        self.ctl.add_rule("price_above", "AAPL", 250, None)
        self.ctl.add_rule("analyst", None, 5, None)
        rows = self.rows(self.open())
        self.assertEqual([r.title._term_anchor.key for r in rows], ["alarm_kurs", "alarm_analyst"])

    def test_the_switch_turns_a_rule_off_and_on(self):
        rule_id = self.ctl.add_rule("price_above", "AAPL", 250, None)
        window = self.open()
        row = self.rows(window)[0]
        row.toggle.click()
        self.assertFalse(self.ctl.alert_rules[0].enabled)
        self.assertTrue(wait_until(lambda: self.rows(window)[0].state.text() == "ausgeschaltet"))
        self.rows(window)[0].toggle.click()
        self.assertTrue(self.ctl.alert_rules[0].enabled)

    def test_the_test_button_shows_what_the_rule_would_report(self):
        self.ctl.add_rule("price_above", "AAPL", 99, None)
        self.ctl.add_rule("price_above", "AAPL", 500, None)
        window = self.open()
        first, second = self.rows(window)
        first.test_button.click()
        second.test_button.click()
        self.assertFalse(first.result.isHidden())
        self.assertTrue(first.result.text().startswith("Würde jetzt melden:"))
        self.assertIn("Schwelle 99 erreicht", first.result.text())
        self.assertTrue(second.result.text().startswith("Würde jetzt nichts melden."))
        self.assertIn("noch 400.0 % entfernt", second.result.text())
        self.assertEqual(self.ctl.alert_log(), [])
        self.assertTrue(self.ctl.alert_rules[0].armed)

    def test_the_menu_offers_every_kind_and_opens_its_dialog(self):
        window = self.open()
        self.assertEqual(list(window.add_actions), list(alerts.KINDS))
        opened = []
        with mock.patch.object(w, "rule_dialog", lambda ctl, kind, rule=None: opened.append((kind, rule))):
            for kind, action in window.add_actions.items():
                action.trigger()
            self.assertTrue(wait_until(lambda: len(opened) == len(alerts.KINDS)))
        self.assertEqual([k for k, _ in opened], list(alerts.KINDS))

    def fill(self, *texts, symbol=None):
        def step(dialog):
            if symbol is not None:
                dialog.entries[0].setCurrentIndex(dialog.entries[0].findData(symbol))
            for entry, text in zip(dialog.entries[1:], texts):
                entry.setText(text)
            dialog.submit()
        return step

    def test_the_price_dialog_creates_a_rule(self):
        driver = DialogDriver(self.fill("123,5", symbol="AAPL"))
        w.rule_dialog(self.ctl, "price_above")
        driver.check()
        [stored] = self.ctl.alert_rules
        self.assertEqual((stored.kind, stored.symbol, stored.threshold), ("price_above", "AAPL", 123.5))

    def test_the_percent_dialog_defaults_to_five_and_all_positions(self):
        seen = []
        driver = DialogDriver(lambda d: (seen.append((d.entries[0].currentData(), d.entries[1].text())), d.submit()))
        w.rule_dialog(self.ctl, "day_move")
        driver.check()
        self.assertEqual(seen, [(None, "5")])
        self.assertEqual(self.ctl.alert_rules[0].threshold, 5.0)

    def test_the_earnings_dialog_takes_whole_days(self):
        driver = DialogDriver(self.fill("4"))
        w.rule_dialog(self.ctl, "earnings")
        driver.check()
        self.assertEqual(self.ctl.alert_rules[0].days, 4)

    def test_fractions_of_days_are_refused_in_the_dialog(self):
        errors = []

        def step(dialog):
            dialog.entries[1].setText("2,5")
            dialog.submit()
            errors.append(dialog.error.text())
        driver = DialogDriver(step)
        w.rule_dialog(self.ctl, "earnings")
        driver.check()
        self.assertIn("ganze Zahl", errors[0])
        self.assertEqual(self.ctl.alert_rules, [])

    def test_the_news_dialog_has_only_the_stock_choice(self):
        counts = []
        driver = DialogDriver(lambda d: (counts.append(len(d.entries)), d.submit()))
        w.rule_dialog(self.ctl, "news")
        driver.check()
        self.assertEqual(counts, [1])
        self.assertEqual(self.ctl.alert_rules[0].kind, "news")

    def test_an_invalid_value_is_refused_in_the_dialog(self):
        errors = []

        def step(dialog):
            dialog.entries[1].setText("0")
            dialog.submit()
            errors.append(dialog.error.text())
        driver = DialogDriver(step)
        w.rule_dialog(self.ctl, "price_above")
        driver.check()
        self.assertIn("größer als 0", errors[0])
        self.assertEqual(self.ctl.alert_rules, [])

    def test_a_price_rule_must_name_a_stock(self):
        seen = []
        driver = DialogDriver(lambda d: (seen.append([d.entries[0].itemData(i) for i in range(d.entries[0].count())]), d.reject()))
        w.rule_dialog(self.ctl, "price_below")
        driver.check()
        self.assertNotIn(None, seen[0])

    def test_the_edit_button_prefills_and_saves(self):
        self.ctl.add_rule("price_above", "AAPL", 250, None)
        window = self.open()
        seen = []

        def step(dialog):
            seen.append((dialog.entries[0].currentData(), dialog.entries[1].text()))
            dialog.entries[1].setText("260")
            dialog.submit()
        driver = DialogDriver(step)
        self.rows(window)[0].edit_button.click()
        driver.check()
        self.assertEqual(seen, [("AAPL", "250")])
        self.assertEqual(self.ctl.alert_rules[0].threshold, 260.0)

    def test_the_delete_button_asks_first(self):
        self.ctl.add_rule("price_above", "AAPL", 250, None)
        window = self.open()
        driver = DialogDriver(lambda d: d.reject())
        self.rows(window)[0].delete_button.click()
        driver.check()
        self.assertEqual(len(self.ctl.alert_rules), 1)
        driver = DialogDriver(lambda d: d.submit())
        self.rows(window)[0].delete_button.click()
        driver.check()
        self.assertEqual(self.ctl.alert_rules, [])
        self.assertTrue(wait_until(lambda: self.rows(window) == []))

    def test_the_settings_dialog_switches_types_and_sets_the_digest(self):
        def step(dialog):
            dialog.entries[0].setCurrentIndex(0)                     # insgesamt an
            dialog.entries[5].setCurrentIndex(1)                     # „Wichtige News“ aus
            dialog.entries[7].setCurrentIndex(dialog.entries[7].findData("weekly"))
            dialog.entries[8].setText("7")
            dialog.entries[9].setCurrentIndex(0)
            dialog.submit()
        driver = DialogDriver(step)
        self.open().settings_button.click()
        driver.check()
        settings = self.ctl.alert_settings
        self.assertFalse(settings.type_enabled("news"))
        self.assertTrue(settings.type_enabled("price_above"))
        self.assertEqual((settings.digest_mode, settings.digest_hour, settings.digest_weekday), ("weekly", 7, 0))

    def test_a_bad_hour_is_refused(self):
        errors = []

        def step(dialog):
            dialog.entries[8].setText("25")
            dialog.submit()
            errors.append(dialog.error.text())
        driver = DialogDriver(step)
        self.open().settings_button.click()
        driver.check()
        self.assertIn("0 und 23", errors[0])
        self.assertEqual(self.ctl.alert_settings, alerts.Settings())

    def test_switching_everything_off_is_shown_in_the_window(self):
        window = self.open()
        self.assertTrue(window.status.isHidden())
        self.ctl.set_alert_settings(alerts.Settings(enabled=False))
        self.assertTrue(wait_until(lambda: not window.status.isHidden()))
        self.assertIn("ausgeschaltet", window.status.text())

    def test_a_switched_off_type_is_shown_on_its_rules(self):
        self.ctl.add_rule("news", None, None, None)
        self.ctl.set_alert_settings(alerts.Settings(True, tuple((k, k != "news") for k in alerts.KINDS)))
        self.assertIn("Art ist in den Einstellungen ausgeschaltet", self.rows(self.open())[0].state.text())

    def test_the_digest_button_shows_the_preview(self):
        seen = []
        driver = DialogDriver(lambda d: (seen.append("\n".join(l.text() for l in d.findChildren(QLabel))), d.reject()))
        self.open().digest_button.click()
        driver.check()
        self.assertIn("Tageszusammenfassung", seen[0])

    def test_the_window_names_the_digest_setting(self):
        self.ctl.set_alert_settings(alerts.Settings(digest_mode="weekly", digest_hour=7, digest_weekday=0))
        self.assertTrue(any("wöchentlich, ab 7:00 Uhr am Montag" in t for t in self.texts(self.open())))

    def test_the_log_lists_alerts_newest_first_with_new_badges(self):
        self.ctl.store.log_alert(None, "a", "AAPL", "Erste", "Text eins", dt.datetime.now())
        self.ctl.store.log_alert(None, "b", None, "Zweite", "Text zwei", dt.datetime.now())
        window = self.open()
        rows = window.findChildren(w.AlertLogRow)
        self.assertEqual([r.title.text() for r in rows], ["Zweite", "Erste"])
        QApplication.processEvents()
        self.assertTrue(all(not r.badge.isHidden() for r in rows))

    def test_closing_marks_everything_read(self):
        self.ctl.store.log_alert(None, "a", "AAPL", "Erste", "Text", dt.datetime.now())
        window = self.open()
        self.assertEqual(self.ctl.unread_alerts(), 1)
        window.close()
        self.assertEqual(self.ctl.unread_alerts(), 0)
        again = self.open()
        QApplication.processEvents()
        self.assertTrue(all(r.badge.isHidden() for r in again.findChildren(w.AlertLogRow)))

    def test_a_click_on_an_entry_opens_the_stock(self):
        self.ctl.store.log_alert(None, "a", "AAPL", "Erste", "Text", dt.datetime.now())
        self.ctl.store.log_alert(None, "b", None, "Zweite", "Text", dt.datetime.now())
        window = self.open()
        by_title = {r.title.text(): r for r in window.findChildren(w.AlertLogRow)}
        by_title["Erste"].clicked.emit("AAPL")
        self.assertEqual(self.opened, ["AAPL"])
        self.assertEqual(by_title["Zweite"].cursor().shape(), w.Qt.ArrowCursor)

    def test_new_alerts_appear_without_reopening(self):
        window = self.open()
        self.ctl.add_rule("price_above", "AAPL", 50, None)
        self.assertTrue(wait_until(lambda: len(window.findChildren(w.AlertLogRow)) == 1, timeout=5000))

    def test_the_button_of_the_positions_box_opens_it(self):
        self.main.alerts_button.click()
        self.assertIn("window", w.ALERT_WINDOWS)
        self.assertEqual(self.main.alerts_button.toolTip(), "Alarme und Benachrichtigungen")

    def test_it_can_be_pinned_and_comes_back(self):
        window = self.open()
        window.pin_button.click()
        self.assertTrue(self.ctl.is_pinned("alerts"))
        window.close()
        w.restore_pinned(self.ctl, self.main)
        self.assertIn("window", w.ALERT_WINDOWS)

    def test_closing_disconnects_the_window(self):
        window = self.open()
        window.close()
        self.ctl.alerts_changed.emit()


class GlossaryTests(unittest.TestCase):
    def test_the_alert_terms_exist(self):
        for key in ("benachrichtigungen", "alarm_kurs", "alarm_tagesbewegung", "alarm_termin", "alarm_news",
                    "alarm_analyst", "zusammenfassung"):
            term = glossary.term(key)
            self.assertTrue(term.text and term.interpretation, key)

    def test_every_rule_kind_has_a_term_in_the_window(self):
        self.assertEqual(set(w.ALERT_TERMS), set(alerts.KINDS))
        for key in w.ALERT_TERMS.values():
            self.assertIn(key, glossary.GLOSSARY)
        self.assertEqual(set(w.ALERT_NAMES), set(alerts.KINDS))


if __name__ == "__main__":
    unittest.main()
