"""Termine: Datumseingabe, Status, Zusammenführen und Kalenderfenster."""
import datetime as dt
import unittest

import events
from events import Event

TODAY = dt.date(2026, 10, 9)


def ev(id=1, symbol="AAPL", kind="earnings", day=dt.date(2026, 11, 2), end=None, precision="day", status="expected",
       source="Yahoo Finance", relevance=2, title="Quartalszahlen", **extra):
    return Event(id, symbol, kind, title, day, end or day, precision, status, source, relevance=relevance, **extra)


class ParseWhenTests(unittest.TestCase):
    def test_exact_day_in_three_notations(self):
        for text in ("08.10.2026", "8.10.26", "2026-10-08", "  08.10.2026  "):
            self.assertEqual(events.parse_when(text), (dt.date(2026, 10, 8), dt.date(2026, 10, 8), "day"), text)

    def test_month_in_numbers_and_names(self):
        october = (dt.date(2026, 10, 1), dt.date(2026, 10, 31), "month")
        for text in ("10/2026", "10.2026", "Oktober 2026", "oktober 2026", "Okt 2026", "okt. 2026"):
            self.assertEqual(events.parse_when(text), october, text)

    def test_month_end_follows_the_calendar(self):
        self.assertEqual(events.parse_when("02/2028")[1], dt.date(2028, 2, 29))  # Schaltjahr
        self.assertEqual(events.parse_when("02/2027")[1], dt.date(2027, 2, 28))
        self.assertEqual(events.parse_when("April 2026")[1], dt.date(2026, 4, 30))

    def test_march_in_all_spellings(self):
        for text in ("März 2026", "Maerz 2026", "mär 2026", "03/2026"):
            self.assertEqual(events.parse_when(text)[0], dt.date(2026, 3, 1), text)

    def test_year(self):
        self.assertEqual(events.parse_when("2027"), (dt.date(2027, 1, 1), dt.date(2027, 12, 31), "year"))

    def test_garbage_is_rejected_with_a_hint(self):
        for text in ("", "bald", "32.13.2026", "13/2026", "00/2026", "1850", "Frühling 2026", "10/26", "Oktober"):
            with self.assertRaisesRegex(ValueError, "Datum"):
                events.parse_when(text)


class TextTests(unittest.TestCase):
    def test_date_text_by_precision(self):
        self.assertEqual(events.date_text(ev(day=dt.date(2026, 11, 2))), "02.11.2026")
        self.assertEqual(events.date_text(ev(day=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31), precision="month")),
                         "Oktober 2026")
        self.assertEqual(events.date_text(ev(day=dt.date(2026, 1, 1), end=dt.date(2026, 12, 31), precision="year")),
                         "2026")

    def test_a_range_shows_both_ends(self):
        self.assertEqual(events.date_text(ev(day=dt.date(2026, 11, 2), end=dt.date(2026, 11, 6))), "02.11.–06.11.2026")
        self.assertEqual(events.date_text(ev(day=dt.date(2026, 12, 28), end=dt.date(2027, 1, 3))),
                         "28.12.2026–03.01.2027")

    def test_relative_text_for_days(self):
        def text(day, end=None):
            return events.relative_text(ev(day=day, end=end), TODAY)
        self.assertEqual(text(TODAY), "heute")
        self.assertEqual(text(TODAY + dt.timedelta(days=1)), "morgen")
        self.assertEqual(text(TODAY + dt.timedelta(days=7)), "in 7 Tagen")
        self.assertEqual(text(TODAY - dt.timedelta(days=1)), "gestern")
        self.assertEqual(text(TODAY - dt.timedelta(days=4)), "vor 4 Tagen")
        self.assertEqual(text(TODAY - dt.timedelta(days=1), TODAY + dt.timedelta(days=2)), "läuft jetzt")

    def test_relative_text_for_months_and_years(self):
        month = lambda y, m: ev(day=dt.date(y, m, 1), end=dt.date(y, m, 28), precision="month")
        self.assertEqual(events.relative_text(month(2026, 10), TODAY), "in diesem Monat")
        self.assertEqual(events.relative_text(month(2026, 11), TODAY), "im nächsten Monat")
        self.assertEqual(events.relative_text(month(2027, 2), TODAY), "in 4 Monaten")
        self.assertEqual(events.relative_text(month(2026, 9), TODAY), "im letzten Monat")
        self.assertEqual(events.relative_text(month(2026, 6), TODAY), "vor 4 Monaten")
        year = lambda y: ev(day=dt.date(y, 1, 1), end=dt.date(y, 12, 31), precision="year")
        self.assertEqual(events.relative_text(year(2026), TODAY), "in diesem Jahr")
        self.assertEqual(events.relative_text(year(2027), TODAY), "im nächsten Jahr")
        self.assertEqual(events.relative_text(year(2029), TODAY), "in 3 Jahren")
        self.assertEqual(events.relative_text(year(2025), TODAY), "im letzten Jahr")

    def test_when_text_joins_date_and_relative_part(self):
        self.assertEqual(events.when_text(ev(day=TODAY + dt.timedelta(days=7)), TODAY), "16.10.2026 · in 7 Tagen")

    def test_source_labels(self):
        self.assertEqual(events.source_label("manual"), "Manuell")
        self.assertEqual(events.source_label("Yahoo Finance"), "Yahoo Finance")


class StatusTests(unittest.TestCase):
    def test_confirmed_and_expected_become_occurred_after_their_date(self):
        for status in ("confirmed", "expected"):
            past = ev(status=status, day=TODAY - dt.timedelta(days=1))
            self.assertEqual(events.effective_status(past, TODAY), "occurred")

    def test_they_stay_what_they_are_up_to_and_including_their_last_day(self):
        today_event = ev(status="confirmed", day=TODAY)
        self.assertEqual(events.effective_status(today_event, TODAY), "confirmed")
        running = ev(status="expected", day=TODAY - dt.timedelta(days=2), end=TODAY)
        self.assertEqual(events.effective_status(running, TODAY), "expected")

    def test_speculation_never_becomes_a_fact_by_itself(self):
        past = ev(status="speculative", day=TODAY - dt.timedelta(days=30))
        self.assertEqual(events.effective_status(past, TODAY), "speculative")
        self.assertTrue(events.is_overdue(past, TODAY))
        self.assertFalse(events.is_overdue(ev(status="speculative", day=TODAY), TODAY))
        self.assertFalse(events.is_overdue(ev(status="expected", day=TODAY - dt.timedelta(days=3)), TODAY))

    def test_occurred_stays_occurred(self):
        self.assertEqual(events.effective_status(ev(status="occurred", day=TODAY + dt.timedelta(days=5)), TODAY),
                         "occurred")

    def test_month_events_end_with_the_month(self):
        october = ev(day=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31), precision="month", status="expected")
        self.assertEqual(events.effective_status(october, TODAY), "expected")
        self.assertEqual(events.effective_status(october, dt.date(2026, 11, 1)), "occurred")

    def test_status_labels_cover_all_statuses_in_german(self):
        self.assertEqual([events.STATUS_LABELS[s] for s in events.STATUSES],
                         ["bestätigt", "erwartet", "spekulativ", "eingetreten"])


class MergeTests(unittest.TestCase):
    def merge(self, *items):
        return events.merge_duplicates(list(items), TODAY)

    def test_different_stocks_or_kinds_stay_apart(self):
        merged = self.merge(ev(1, "AAPL"), ev(2, "MSFT"), ev(3, "AAPL", kind="dividend"))
        self.assertEqual(len(merged), 3)

    def test_the_same_event_from_two_sources_becomes_one(self):
        merged = self.merge(ev(1, source="Yahoo Finance"), ev(2, source="manual", status="confirmed"))
        [item] = merged
        self.assertEqual(item.sources, ("Yahoo Finance", "Manuell"))
        self.assertEqual(len(item.members), 2)

    def test_the_best_status_wins_and_the_source_of_it_is_primary(self):
        [item] = self.merge(ev(1, status="expected"), ev(2, source="manual", status="confirmed"))
        self.assertEqual(item.status, "confirmed")
        self.assertEqual(item.event.id, 2)

    def test_speculation_loses_against_an_expectation(self):
        [item] = self.merge(ev(1, status="speculative", source="manual"), ev(2, status="expected"))
        self.assertEqual((item.status, item.event.id), ("expected", 2))

    def test_earnings_a_few_days_apart_are_the_same_event(self):
        [item] = self.merge(ev(1, day=dt.date(2026, 11, 2)), ev(2, day=dt.date(2026, 11, 7), source="manual"))
        self.assertEqual(len(item.members), 2)
        self.assertEqual(item.other_dates[0][1] in ("Yahoo Finance", "Manuell"), True)

    def test_earnings_more_than_a_week_apart_are_two_events(self):
        self.assertEqual(len(self.merge(ev(1, day=dt.date(2026, 11, 2)), ev(2, day=dt.date(2026, 11, 20)))), 2)

    def test_other_kinds_need_the_same_day(self):
        a = ev(1, kind="product", day=dt.date(2026, 11, 2), title="Release")
        b = ev(2, kind="product", day=dt.date(2026, 11, 3), title="Release")
        self.assertEqual(len(self.merge(a, b)), 2)
        same = ev(3, kind="product", day=dt.date(2026, 11, 2), title="Release", source="manual")
        self.assertEqual(len(self.merge(a, same)), 1)

    def test_different_products_of_one_stock_in_one_month_stay_apart(self):
        a = ev(1, kind="product", title="GTA VI", day=dt.date(2026, 11, 15), source="manual")
        b = ev(2, kind="product", title="Red Dead 3", day=dt.date(2026, 11, 20), source="manual")
        c = ev(3, kind="product", title="Demo", day=dt.date(2026, 11, 1), end=dt.date(2026, 11, 30), precision="month")
        self.assertEqual(len(self.merge(a, b, c)), 3)

    def test_titles_are_compared_without_case_and_extra_spaces(self):
        a = ev(1, kind="product", title="GTA  VI", day=dt.date(2026, 11, 15), source="manual")
        b = ev(2, kind="product", title="gta vi", day=dt.date(2026, 11, 15))
        self.assertEqual(len(self.merge(a, b)), 1)

    def test_single_kinds_merge_whatever_the_title(self):
        a = ev(1, kind="dividend", title="Dividende", day=dt.date(2026, 11, 15), source="manual")
        b = ev(2, kind="dividend", title="Ausschüttung", day=dt.date(2026, 11, 15))
        self.assertEqual(len(self.merge(a, b)), 1)

    def test_a_month_event_swallows_a_day_inside_it(self):
        month = ev(1, kind="product", day=dt.date(2026, 11, 1), end=dt.date(2026, 11, 30), precision="month",
                   status="speculative", source="manual")
        day = ev(2, kind="product", day=dt.date(2026, 11, 15), status="confirmed")
        [item] = self.merge(month, day)
        self.assertEqual((item.status, item.event.precision), ("confirmed", "day"))

    def test_a_range_from_one_source_merges_with_a_day_inside(self):
        window = ev(1, day=dt.date(2026, 11, 2), end=dt.date(2026, 11, 6))
        self.assertEqual(len(self.merge(window, ev(2, day=dt.date(2026, 11, 4), source="manual"))), 1)

    def test_differing_dates_are_kept_as_a_hint(self):
        [item] = self.merge(ev(1, day=dt.date(2026, 11, 2), status="confirmed", source="manual"),
                            ev(2, day=dt.date(2026, 11, 5)))
        self.assertEqual(item.event.id, 1)
        self.assertEqual(item.other_dates, [("05.11.2026", "Yahoo Finance")])

    def test_equal_dates_give_no_hint(self):
        [item] = self.merge(ev(1), ev(2, source="manual"))
        self.assertEqual(item.other_dates, [])

    def test_a_past_and_a_future_event_of_the_same_kind_do_not_merge(self):
        merged = self.merge(ev(1, day=dt.date(2026, 7, 28)), ev(2, day=dt.date(2026, 11, 2)))
        self.assertEqual(sorted(m.status for m in merged), ["expected", "occurred"])

    def test_merged_past_event_is_occurred(self):
        [item] = self.merge(ev(1, day=dt.date(2026, 10, 1)), ev(2, day=dt.date(2026, 10, 2), source="manual"))
        self.assertEqual(item.status, "occurred")

    def test_the_inputs_are_not_changed(self):
        items = [ev(1), ev(2, source="manual")]
        before = list(items)
        events.merge_duplicates(items, TODAY)
        self.assertEqual(items, before)

    def test_nothing_in_nothing_out(self):
        self.assertEqual(events.merge_duplicates([], TODAY), [])


class WindowTests(unittest.TestCase):
    def setUp(self):
        d = lambda n: TODAY + dt.timedelta(days=n)
        self.items = events.merge_duplicates([
            ev(1, "AAPL", day=d(3), relevance=3),
            ev(2, "MSFT", kind="dividend", day=d(3), relevance=1),
            ev(3, "AAPL", kind="ex_dividend", day=d(20)),
            ev(4, "TTWO", kind="product", day=d(60), status="speculative", source="manual", title="GTA VI"),
            ev(5, "TTWO", kind="product", day=dt.date(2026, 10, 1), end=dt.date(2026, 10, 31), precision="month",
               status="speculative", source="manual", title="Demo"),
            ev(6, "NVDA", day=d(-5), status="expected"),             # vorbei: eingetreten
            ev(7, "NVDA", kind="product", day=d(-40), status="speculative", source="manual"),  # verstrichen
            ev(8, "NVDA", kind="conference", day=d(200)),            # weit weg
        ], TODAY)

    def window(self, days):
        return events.calendar_window(self.items, TODAY, days)

    def names(self, merged):
        return [(m.event.symbol, m.event.kind) for m in merged]

    def test_seven_days(self):
        dated, undated = self.window(7)
        self.assertEqual(self.names(dated), [("AAPL", "earnings"), ("MSFT", "dividend")])
        self.assertEqual(self.names(undated), [("TTWO", "product")])  # Oktober läuft noch

    def test_thirty_days(self):
        dated, _ = self.window(30)
        self.assertEqual(self.names(dated), [("AAPL", "earnings"), ("MSFT", "dividend"), ("AAPL", "ex_dividend")])

    def test_ninety_days_include_the_speculative_one_with_its_status(self):
        dated, _ = self.window(90)
        self.assertEqual(self.names(dated)[-1], ("TTWO", "product"))
        self.assertEqual(dated[-1].status, "speculative")

    def test_far_events_stay_out(self):
        for days in (7, 30, 90):
            self.assertNotIn(("NVDA", "conference"), self.names(self.window(days)[0]))

    def test_occurred_and_overdue_events_are_not_upcoming(self):
        everything = self.names(self.window(365)[0] + self.window(365)[1])
        self.assertNotIn(("NVDA", "earnings"), everything)
        self.assertNotIn(("NVDA", "product"), everything)

    def test_same_day_is_ordered_by_relevance_then_status(self):
        dated, _ = self.window(7)
        self.assertEqual([m.event.symbol for m in dated], ["AAPL", "MSFT"])

    def test_events_today_are_included_and_yesterdays_are_not(self):
        items = events.merge_duplicates([ev(1, day=TODAY), ev(2, "MSFT", day=TODAY - dt.timedelta(days=1))], TODAY)
        self.assertEqual([m.event.symbol for m in events.calendar_window(items, TODAY, 7)[0]], ["AAPL"])

    def test_an_event_marked_occurred_stays_out_even_with_a_future_date(self):
        items = events.merge_duplicates([ev(1, day=TODAY + dt.timedelta(days=2), status="occurred")], TODAY)
        self.assertEqual(events.calendar_window(items, TODAY, 7), ([], []))

    def test_the_last_day_of_the_window_is_included(self):
        items = events.merge_duplicates([ev(1, day=TODAY + dt.timedelta(days=7)),
                                         ev(2, "MSFT", day=TODAY + dt.timedelta(days=8))], TODAY)
        self.assertEqual([m.event.symbol for m in events.calendar_window(items, TODAY, 7)[0]], ["AAPL"])

    def test_recent_lists_occurred_and_overdue_newest_first(self):
        found = [(m.event.symbol, m.status) for m in events.recent(self.items, TODAY, 60)]
        self.assertEqual(found, [("NVDA", "occurred"), ("NVDA", "speculative")])

    def test_recent_ignores_what_is_older_than_the_limit(self):
        self.assertEqual([m.event.symbol for m in events.recent(self.items, TODAY, 10)], ["NVDA"])

    def test_upcoming_includes_imprecise_events_in_order(self):
        names = self.names(events.upcoming(self.items, TODAY))
        self.assertEqual(names[0], ("TTWO", "product"))  # Oktober beginnt vor allem anderen
        self.assertEqual(len(names), 6)


class MakeEventTests(unittest.TestCase):
    def make(self, **overrides):
        args = dict(symbol="ttwo", kind="product", title="  GTA   VI ", when_text_input="Mai 2027",
                    status="speculative")
        args.update(overrides)
        return events.make_event(**args)

    def test_fields_are_normalised(self):
        fields = self.make()
        self.assertEqual((fields["symbol"], fields["title"], fields["precision"]), ("TTWO", "GTA VI", "month"))
        self.assertEqual((fields["day"], fields["end"]), (dt.date(2027, 5, 1), dt.date(2027, 5, 31)))
        self.assertEqual(fields["relevance"], 2)

    def test_default_relevance_depends_on_the_kind(self):
        self.assertEqual(self.make(kind="earnings")["relevance"], 3)
        self.assertEqual(self.make(kind="dividend")["relevance"], 1)

    def test_blank_title_falls_back_to_the_kind(self):
        self.assertEqual(self.make(title="  ")["title"], "Produktstart")

    def test_invalid_input_is_rejected(self):
        for overrides in ({"symbol": "  "}, {"kind": "party"}, {"status": "sicher"}, {"relevance": 9},
                          {"when_text_input": "irgendwann"}):
            with self.assertRaises(ValueError):
                self.make(**overrides)

    def test_note_and_url_are_kept_trimmed(self):
        fields = self.make(note="  laut  Trailer ", source_url=" https://x ")
        self.assertEqual((fields["note"], fields["source_url"]), ("laut Trailer", "https://x"))


class VocabularyTests(unittest.TestCase):
    def test_every_event_the_program_already_knows_has_a_kind(self):
        import stock_data as sd
        self.assertEqual(set(events.YAHOO_KINDS), set(sd.EVENT_LABELS.values()))
        for kind in events.YAHOO_KINDS.values():
            self.assertIn(kind, events.KINDS)

    def test_every_kind_has_a_default_relevance_within_range(self):
        for kind in events.KINDS:
            self.assertIn(events.default_relevance(kind), events.RELEVANCE)

    def test_labels_are_unique(self):
        labels = list(events.KINDS.values())
        self.assertEqual(len(labels), len(set(labels)))


if __name__ == "__main__":
    unittest.main()
