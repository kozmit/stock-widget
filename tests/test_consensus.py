"""Einschätzungen der Analysten: Auswertung, Abruf und Anzeige im Detailfenster."""
import datetime as dt
import types
import unittest
from unittest import mock

import pandas as pd
from PySide6.QtWidgets import QLabel

import consensus as cons
import glossary
import stock_data as sd
import stock_widget as w
from tests.support import AppTestCase, wait_until

AAPL_ROWS = [{"period": "0m", "strongBuy": 6, "buy": 19, "hold": 13, "sell": 3, "strongSell": 3},
             {"period": "-1m", "strongBuy": 6, "buy": 19, "hold": 13, "sell": 3, "strongSell": 3},
             {"period": "-3m", "strongBuy": 6, "buy": 22, "hold": 14, "sell": 2, "strongSell": 2}]
EARNINGS_ROWS = [
    {"quarter": dt.date(2026, 3, 31), "epsActual": 2.01, "epsEstimate": 1.94275},
    {"quarter": dt.date(2026, 6, 30), "epsActual": 2.02, "epsEstimate": 1.89243},
    {"quarter": dt.date(2025, 12, 31), "epsActual": 2.84, "epsEstimate": 2.6708},
    {"quarter": dt.date(2025, 9, 30), "epsActual": 1.85, "epsEstimate": 1.76993}]


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.parsed = cons.parse_recommendations(AAPL_ROWS)
        self.now, self.before = self.parsed["now"], self.parsed["before"]

    def test_now_and_three_months_ago_are_picked_by_period(self):
        self.assertEqual(self.now, {"strong_buy": 6, "buy": 19, "hold": 13, "sell": 3, "strong_sell": 3})
        self.assertEqual(self.before["buy"], 22)

    def test_missing_periods_are_none(self):
        self.assertEqual(cons.parse_recommendations([AAPL_ROWS[0]])["before"], None)
        self.assertEqual(cons.parse_recommendations([]), {"now": None, "before": None})
        self.assertEqual(cons.parse_recommendations(None), {"now": None, "before": None})

    def test_a_period_without_analysts_is_none(self):
        row = {"period": "0m", "strongBuy": 0, "buy": 0, "hold": 0, "sell": 0, "strongSell": 0}
        self.assertIsNone(cons.parse_recommendations([row])["now"])

    def test_nan_text_and_negative_counts_become_zero(self):
        row = {"period": "0m", "strongBuy": float("nan"), "buy": "x", "hold": -2, "sell": None, "strongSell": 4.0}
        self.assertEqual(cons.parse_recommendations([row])["now"],
                         {"strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 4})

    def test_the_sides_group_the_five_ratings(self):
        self.assertEqual(cons.sides(self.now), (25, 13, 6))
        self.assertEqual(cons.total(self.now), 44)
        self.assertEqual(cons.total(None), 0)

    def test_the_distribution_has_no_overall_grade(self):
        self.assertEqual(cons.distribution_text(self.now), "25 Kaufen · 13 Halten · 6 Verkaufen")
        for word in ("Empfehlung", "Gesamt", "Note", "Konsens"):
            self.assertNotIn(word, cons.distribution_text(self.now))

    def test_empty_groups_are_left_out_of_the_text(self):
        counts = {"strong_buy": 2, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0}
        self.assertEqual(cons.distribution_text(counts), "2 Kaufen")

    def test_the_positive_share_and_its_trend(self):
        self.assertAlmostEqual(cons.positive_share(self.now), 25 / 44)
        self.assertIsNone(cons.positive_share(None))
        self.assertEqual(cons.trend_text(self.now, self.before),
                         "Kaufen-Anteil 57 % (-4 Prozentpunkte seit 3 Monaten)")

    def test_an_unchanged_share_is_called_unchanged(self):
        self.assertIn("unverändert", cons.trend_text(self.now, dict(self.now)))

    def test_a_rising_share_has_a_plus_sign(self):
        self.assertIn("+", cons.trend_text(self.before, self.now))

    def test_no_trend_without_a_comparison(self):
        self.assertEqual(cons.trend_text(self.now, None), "")
        self.assertEqual(cons.trend_text(None, self.now), "")

    def test_few_analysts_get_a_warning(self):
        self.assertEqual(cons.caution(3), "Nur 3 Analysten: wenig aussagekräftig.")
        self.assertEqual(cons.caution(1), "Nur 1 Analyst: wenig aussagekräftig.")
        self.assertEqual(cons.caution(cons.FEW_ANALYSTS), "")
        self.assertEqual(cons.caution(0), "")


class SurpriseTests(unittest.TestCase):
    def test_the_newest_quarters_come_first_at_most_four(self):
        found = cons.parse_surprises(EARNINGS_ROWS + [{"quarter": dt.date(2025, 6, 30), "epsActual": 1.0, "epsEstimate": 1.0}])
        self.assertEqual([i["quarter"] for i in found],
                         [dt.date(2026, 6, 30), dt.date(2026, 3, 31), dt.date(2025, 12, 31), dt.date(2025, 9, 30)])

    def test_the_deviation_is_computed_from_the_figures(self):
        [first] = cons.parse_surprises(EARNINGS_ROWS[1:2])
        self.assertAlmostEqual(first["surprise"], (2.02 - 1.89243) / 1.89243)

    def test_a_miss_is_negative_and_a_loss_expectation_uses_its_size(self):
        [miss] = cons.parse_surprises([{"quarter": dt.date(2025, 9, 30), "epsActual": 3.3, "epsEstimate": 4.39506}])
        self.assertAlmostEqual(miss["surprise"], -0.2492, places=3)
        [loss] = cons.parse_surprises([{"quarter": dt.date(2025, 9, 30), "epsActual": -0.5, "epsEstimate": -1.0}])
        self.assertAlmostEqual(loss["surprise"], 0.5)  # weniger Verlust als erwartet ist besser

    def test_an_estimate_of_zero_has_no_percentage(self):
        [item] = cons.parse_surprises([{"quarter": dt.date(2025, 9, 30), "epsActual": 0.1, "epsEstimate": 0.0}])
        self.assertIsNone(item["surprise"])
        self.assertEqual(cons.surprise_line(item), "Q3 2025: 0.10 statt 0.00 erwartet")

    def test_rows_without_both_figures_or_a_date_are_skipped(self):
        rows = [{"quarter": dt.date(2025, 9, 30), "epsActual": None, "epsEstimate": 1.0},
                {"quarter": dt.date(2025, 6, 30), "epsActual": float("nan"), "epsEstimate": 1.0},
                {"quarter": None, "epsActual": 1.0, "epsEstimate": 1.0},
                {"quarter": "kaputt", "epsActual": 1.0, "epsEstimate": 1.0}]
        self.assertEqual(cons.parse_surprises(rows), [])
        self.assertEqual(cons.parse_surprises(None), [])

    def test_timestamps_and_text_dates_are_accepted(self):
        rows = [{"quarter": pd.Timestamp("2026-06-30"), "epsActual": 1.0, "epsEstimate": 1.0},
                {"quarter": "2026-03-31", "epsActual": 1.0, "epsEstimate": 1.0}]
        self.assertEqual([i["quarter"] for i in cons.parse_surprises(rows)], [dt.date(2026, 6, 30), dt.date(2026, 3, 31)])

    def test_the_line_names_quarter_figures_and_percentage(self):
        [item] = cons.parse_surprises(EARNINGS_ROWS[1:2])
        self.assertEqual(cons.surprise_line(item), "Q2 2026: 2.02 statt 1.89 erwartet (+6.7 %)")

    def test_quarters_are_numbered_from_the_month(self):
        self.assertEqual([cons.quarter_label(dt.date(2026, m, 28)) for m in (1, 3, 4, 6, 7, 9, 10, 12)],
                         ["Q1 2026", "Q1 2026", "Q2 2026", "Q2 2026", "Q3 2026", "Q3 2026", "Q4 2026", "Q4 2026"])

    def test_the_summary_counts_the_beaten_quarters(self):
        found = cons.parse_surprises(EARNINGS_ROWS)
        self.assertEqual(cons.beat_summary(found), "4 von 4 Quartalen über der Erwartung")
        mixed = cons.parse_surprises([{"quarter": dt.date(2025, 9, 30), "epsActual": 1.0, "epsEstimate": 2.0},
                                      {"quarter": dt.date(2025, 12, 31), "epsActual": 3.0, "epsEstimate": 2.0}])
        self.assertEqual(cons.beat_summary(mixed), "1 von 2 Quartalen über der Erwartung")
        self.assertEqual(cons.beat_summary([]), "")


class FetchTests(unittest.TestCase):
    def frames(self, recommendations=None, earnings=None):
        ticker = types.SimpleNamespace(
            recommendations_summary=pd.DataFrame(AAPL_ROWS) if recommendations is None else recommendations,
            earnings_history=(pd.DataFrame(EARNINGS_ROWS).set_index("quarter") if earnings is None else earnings))
        fake = mock.Mock()
        fake.Ticker = lambda symbol: ticker
        return mock.patch.object(sd, "yf", fake)

    def test_the_data_is_read_from_yahoos_tables(self):
        with self.frames():
            data = sd.fetch_consensus("AAPL")
        self.assertEqual(cons.total(data["recommendations"]["now"]), 44)
        self.assertEqual(len(data["surprises"]), 4)
        self.assertLessEqual(dt.datetime.now() - data["fetched_at"], dt.timedelta(minutes=1))

    def test_one_table_alone_is_enough(self):
        with self.frames(earnings=pd.DataFrame()):
            data = sd.fetch_consensus("BA.L")
        self.assertEqual(data["surprises"], [])
        self.assertIsNotNone(data["recommendations"]["now"])

    def test_nothing_from_yahoo_is_an_error_with_a_reason(self):
        with self.frames(recommendations=pd.DataFrame(), earnings=pd.DataFrame()), self.assertRaises(ValueError) as c:
            sd.fetch_consensus("GC=F")
        self.assertIn("keine Einschätzungen", str(c.exception))

    def test_a_failure_of_both_requests_is_reported_as_such(self):
        class Broken:
            @property
            def recommendations_summary(self):
                raise RuntimeError("offline")

            @property
            def earnings_history(self):
                raise RuntimeError("offline")
        fake = mock.Mock()
        fake.Ticker = lambda symbol: Broken()
        with mock.patch.object(sd, "yf", fake), self.assertRaises(RuntimeError):
            sd.fetch_consensus("AAPL")

    def test_one_failing_request_still_gives_the_other(self):
        class Half:
            recommendations_summary = pd.DataFrame(AAPL_ROWS)

            @property
            def earnings_history(self):
                raise RuntimeError("kaputt")
        fake = mock.Mock()
        fake.Ticker = lambda symbol: Half()
        with mock.patch.object(sd, "yf", fake):
            data = sd.fetch_consensus("AAPL")
        self.assertEqual(data["surprises"], [])


class DetailConsensusTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.main = w.MainWindow(self.ctl)

    def serve(self, recommendations=AAPL_ROWS, earnings=EARNINGS_ROWS):
        data = {"recommendations": cons.parse_recommendations(recommendations),
                "surprises": cons.parse_surprises(earnings), "fetched_at": dt.datetime(2026, 10, 9, 15, 30)}
        patcher = mock.patch.object(sd, "fetch_consensus", lambda s: data)
        patcher.start()
        self.addCleanup(patcher.stop)

    def detail(self):
        self.main.open_detail("AAPL")
        return self.main.details["AAPL"]

    def texts(self, detail):
        return [l.text() for l in detail.consensus_card.findChildren(QLabel) if not l.isHidden()]

    def test_the_card_shows_the_distribution_the_trend_and_the_quarters(self):
        self.serve()
        detail = self.detail()
        self.assertTrue(wait_until(lambda: not detail.consensus_body.isHidden()))
        texts = self.texts(detail)
        self.assertIn("25 Kaufen · 13 Halten · 6 Verkaufen  (44 Analysten)", texts)
        self.assertIn("Kaufen-Anteil 57 % (-4 Prozentpunkte seit 3 Monaten)", texts)
        self.assertIn("Q2 2026: 2.02 statt 1.89 erwartet (+6.7 %)", texts)
        self.assertIn("4 von 4 Quartalen über der Erwartung", texts)
        self.assertIn("Quelle: Yahoo Finance, Stand 09.10.2026 15:30. Schätzungen von Analysten, keine Prognose.", texts)
        self.assertTrue(detail.consensus_status.isHidden())

    def test_there_is_a_bar_with_one_segment_per_side(self):
        self.serve()
        detail = self.detail()
        self.assertTrue(wait_until(lambda: detail.rec_bar.count() == 3))
        self.assertEqual([detail.rec_bar.stretch(i) for i in range(3)], [25, 13, 6])

    def test_there_is_no_overall_grade_anywhere(self):
        self.serve()
        detail = self.detail()
        self.assertTrue(wait_until(lambda: not detail.consensus_body.isHidden()))
        for text in self.texts(detail):
            self.assertNotIn("Strong Buy", text)
            self.assertNotIn("Empfehlung:", text)

    def test_few_analysts_are_warned_about(self):
        rows = [{"period": "0m", "strongBuy": 1, "buy": 2, "hold": 0, "sell": 0, "strongSell": 0}]
        self.serve(recommendations=rows)
        detail = self.detail()
        self.assertTrue(wait_until(lambda: not detail.consensus_body.isHidden()))
        self.assertFalse(detail.rec_warning.isHidden())
        self.assertEqual(detail.rec_warning.text(), "Nur 3 Analysten: wenig aussagekräftig.")

    def test_many_analysts_get_no_warning(self):
        self.serve()
        detail = self.detail()
        self.assertTrue(wait_until(lambda: not detail.consensus_body.isHidden()))
        self.assertTrue(detail.rec_warning.isHidden())

    def test_a_missed_estimate_is_red_and_a_beaten_one_green(self):
        self.serve(earnings=[{"quarter": dt.date(2026, 6, 30), "epsActual": 1.0, "epsEstimate": 2.0},
                             {"quarter": dt.date(2026, 3, 31), "epsActual": 3.0, "epsEstimate": 2.0}])
        detail = self.detail()
        self.assertTrue(wait_until(lambda: not detail.consensus_body.isHidden()))
        colors = {l.text(): l.styleSheet() for l in detail.consensus_card.findChildren(QLabel)}
        self.assertIn(w.RED, colors["Q2 2026: 1.00 statt 2.00 erwartet (-50.0 %)"])
        self.assertIn(w.GREEN, colors["Q1 2026: 3.00 statt 2.00 erwartet (+50.0 %)"])

    def test_without_quarters_only_the_recommendations_show(self):
        self.serve(earnings=[])
        detail = self.detail()
        self.assertTrue(wait_until(lambda: not detail.consensus_body.isHidden()))
        self.assertTrue(detail.surprise_title.isHidden())
        self.assertTrue(detail.surprise_summary.isHidden())

    def test_without_recommendations_only_the_quarters_show(self):
        self.serve(recommendations=[])
        detail = self.detail()
        self.assertTrue(wait_until(lambda: not detail.consensus_body.isHidden()))
        self.assertTrue(detail.rec_text.isHidden())
        self.assertFalse(detail.surprise_title.isHidden())

    def test_nothing_from_yahoo_says_why(self):
        detail = self.detail()  # Vorgabe der Tests: Yahoo nennt nichts
        self.assertTrue(wait_until(lambda: "keine Einschätzungen" in detail.consensus_status.text()))
        self.assertTrue(detail.consensus_body.isHidden())

    def test_a_failed_request_is_called_not_loadable(self):
        mock.patch.object(sd, "fetch_consensus", mock.Mock(side_effect=RuntimeError("offline"))).start()
        self.addCleanup(mock.patch.stopall)
        detail = self.detail()
        self.assertTrue(wait_until(lambda: detail.consensus_status.text() == "Nicht ladbar: offline"))

    def test_the_titles_explain_themselves(self):
        self.serve()
        detail = self.detail()
        captions = {l.text(): l for l in detail.consensus_card.findChildren(QLabel)}
        self.assertEqual(captions["EINSCHÄTZUNG DER ANALYSTEN"]._term_anchor.key, "empfehlung")
        self.assertEqual(captions["GEWINN JE AKTIE GEGEN ERWARTUNG"]._term_anchor.key, "gewinnueberraschung")

    def test_the_glossary_warns_about_the_bias_of_analysts(self):
        text = glossary.term("empfehlung").interpretation
        self.assertIn("Geschäftsbeziehungen", text)
        self.assertIn("fünf", text)

    def test_a_window_closed_before_the_answer_ignores_it(self):
        self.serve()
        detail = self.detail()
        detail.close()
        detail.show_consensus(None, ValueError("egal"))  # darf nichts mehr ändern oder werfen


if __name__ == "__main__":
    unittest.main()
