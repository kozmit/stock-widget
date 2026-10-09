"""Marken im Kursdiagramm, Zusammenfassung, Verlauf des Portfolios und die Planung von Importen."""
import datetime as dt
import unittest

import history
import ledger
import portfolio
import sources
from fx import FxTable
from ledger import Transaction
from sources import ExternalTrade

D1, D2, D3 = dt.date(2026, 1, 5), dt.date(2026, 2, 10), dt.date(2026, 3, 2)


def tx(id, symbol, kind, shares, price, day, fee=0.0, note="", source="manual", external_id=""):
    return Transaction(id, symbol, kind, shares, price, fee, day, note, source, external_id)


def stamps(*days):
    return [(dt.datetime(d.year, d.month, d.day, 12), 100.0 + i) for i, d in enumerate(days)]


class MarkerTests(unittest.TestCase):
    def setUp(self):
        self.points = stamps(dt.date(2026, 1, 5), dt.date(2026, 1, 6), dt.date(2026, 1, 9), dt.date(2026, 1, 12))

    def test_marker_sits_on_the_point_of_its_day(self):
        self.assertEqual(history.marker_index(self.points, dt.date(2026, 1, 6)), 1)

    def test_weekend_uses_the_previous_trading_day(self):
        self.assertEqual(history.marker_index(self.points, dt.date(2026, 1, 10)), 2)

    def test_days_outside_the_range_have_no_marker(self):
        self.assertIsNone(history.marker_index(self.points, dt.date(2026, 1, 4)))
        self.assertIsNone(history.marker_index(self.points, dt.date(2026, 1, 13)))

    def test_intraday_points_use_the_last_point_of_the_day(self):
        hourly = [(dt.datetime(2026, 1, 5, h), 1.0) for h in (10, 11, 12)] + [(dt.datetime(2026, 1, 6, 10), 1.0)]
        self.assertEqual(history.marker_index(hourly, dt.date(2026, 1, 5)), 2)

    def test_empty_chart_has_no_markers(self):
        self.assertIsNone(history.marker_index([], D1))
        self.assertEqual(history.chart_markers([tx(1, "A", "buy", 1, 100, D1)], []), [])

    def test_markers_for_buys_and_sells_in_range_only(self):
        txs = [tx(1, "A", "buy", 10, 100.0, dt.date(2026, 1, 5)), tx(2, "A", "sell", 4, 130.0, dt.date(2026, 1, 9)),
               tx(3, "A", "buy", 1, 90.0, dt.date(2025, 6, 1))]
        markers = history.chart_markers(txs, self.points)
        self.assertEqual([(m.index, m.kind) for m in markers], [(0, "buy"), (2, "sell")])
        self.assertEqual(markers[1].price, 130.0)

    def test_opening_entries_are_marked_and_explained(self):
        txs = [tx(1, "A", "buy", 7.36, 547.62, dt.date(2026, 1, 6), note="Startbestand", source="opening")]
        [marker] = history.chart_markers(txs, self.points)
        self.assertTrue(marker.opening)
        self.assertIn("Kaufdatum unbekannt", marker.text)
        self.assertIn("7.36", marker.text)

    def test_old_data_without_a_source_is_still_recognised_by_its_note(self):
        [marker] = history.chart_markers([tx(1, "A", "buy", 1, 10.0, dt.date(2026, 1, 6), note="Startbestand (alt)")],
                                         self.points)
        self.assertTrue(marker.opening)

    def test_text_names_the_source_of_imported_entries(self):
        [marker] = history.chart_markers([tx(1, "A", "buy", 2, 55.5, dt.date(2026, 1, 6), source="etoro",
                                              external_id="X1")], self.points)
        self.assertEqual(marker.text, "Kauf 2 Stk · 55.50 · 06.01.2026 · eToro")
        self.assertFalse(marker.opening)

    def test_manual_entries_carry_no_source_suffix(self):
        self.assertEqual(history.describe(tx(1, "A", "sell", 1, 9.0, D1)), "Verkauf 1 Stk · 9.00 · 05.01.2026")

    def test_source_labels(self):
        self.assertEqual(history.source_label("manual"), "Manuell")
        self.assertEqual(history.source_label("opening"), "Startbestand")
        self.assertEqual(history.source_label("etoro"), "eToro")
        self.assertEqual(history.source_label("trading212"), "Trading212")


class HistorySummaryTests(unittest.TestCase):
    def test_no_transactions_no_summary(self):
        self.assertIsNone(history.summarize_history([]))

    def test_counts_dates_amounts_fees_and_sources(self):
        s = history.summarize_history([tx(1, "A", "buy", 10, 100, D1, fee=2), tx(2, "A", "sell", 4, 120, D2, fee=1),
                                       tx(3, "A", "buy", 5, 90, D3, source="etoro", external_id="1")])
        self.assertEqual((s.count, s.first_day, s.last_day), (3, D1, D3))
        self.assertEqual((s.bought, s.sold, s.fees), (15, 4, 3))
        self.assertEqual(s.sources, {"manual": 2, "etoro": 1})
        self.assertFalse(s.only_opening)

    def test_only_opening_entries_mean_no_real_history(self):
        s = history.summarize_history([tx(1, "A", "buy", 10, 100, D1, note="Startbestand", source="opening")])
        self.assertTrue(s.only_opening)
        self.assertEqual(s.sources, {"opening": 1})

    def test_opening_recognised_by_note_for_old_rows(self):
        s = history.summarize_history([tx(1, "A", "buy", 10, 100, D1, note="Startbestand")])
        self.assertTrue(s.only_opening)


class PerformanceTests(unittest.TestCase):
    """AAA in USD: 10 Stück zu 100 am 05.01. (Kurs 0,90), 4 verkauft zu 130 am 02.03.; Schlusskurse 100, 110, 120."""

    def setUp(self):
        self.txs = {"AAA": [tx(1, "AAA", "buy", 10, 100, D1), tx(2, "AAA", "sell", 4, 130, D3)]}
        self.closes = {"AAA": {D1: 100.0, D2: 110.0, D3: 120.0}}
        self.fx = FxTable({"USD": {D1: 0.90}})

    def series(self, **kw):
        args = dict(transactions=self.txs, closes=self.closes, currencies={"AAA": "USD"}, fx=self.fx)
        args.update(kw)
        return history.performance_series(**args)

    def test_value_invested_and_result_day_by_day(self):
        points = self.series().points
        self.assertEqual([p.day for p in points], [D1, D2, D3])
        first, second, third = points
        self.assertAlmostEqual((first.value, first.invested, first.result), (900.0, 900.0, 0.0))
        self.assertAlmostEqual((second.value, second.invested, second.result), (990.0, 900.0, 90.0))
        # nach dem Verkauf: 6 Stück à 120 = 648, Kosten 540, realisiert (520 - 400) * 0,9 = 108
        self.assertAlmostEqual((third.value, third.invested, third.realized, third.result), (648.0, 540.0, 108.0, 216.0))
        self.assertAlmostEqual(third.return_pct, 216 / (540 + 360) * 100)

    def test_last_point_matches_the_overview(self):
        states = {"AAA": ledger.replay(self.txs["AAA"])}
        overview = portfolio.summarize(states, {"AAA": {"price": 120.0, "currency": "USD"}}, {},
                                       FxTable({"USD": {D1: 0.90}}, {"USD": {"rate": 0.90, "fetched_at": None, "source": ""}}))
        last = self.series().points[-1]
        self.assertAlmostEqual(last.value, overview.value)
        self.assertAlmostEqual(last.result, overview.total_result)

    def test_exchange_rate_of_each_day_is_used(self):
        fx = FxTable({"USD": {D1: 0.90, D2: 0.80, D3: 0.85}})
        second = self.series(fx=fx).points[1]
        self.assertAlmostEqual(second.value, 10 * 110 * 0.80)
        self.assertAlmostEqual(second.invested, 10 * 100 * 0.90)  # Kosten bleiben zum Kaufkurs

    def test_no_points_before_the_first_transaction(self):
        self.closes["AAA"][dt.date(2025, 12, 1)] = 90.0
        self.assertEqual(self.series().points[0].day, D1)

    def test_prices_are_carried_forward_over_gaps(self):
        del self.closes["AAA"][D2]
        points = self.series().points
        self.assertEqual([p.day for p in points], [D1, D3])
        self.txs["AAA"].append(tx(3, "AAA", "buy", 1, 100, D2))  # ein Handelstag ohne Kurs: der alte gilt
        by_day = {p.day: p for p in self.series().points}
        self.assertAlmostEqual(by_day[D2].value, 11 * 100.0 * 0.9)

    def test_only_an_opening_entry_gives_a_single_day(self):
        txs = {"AAA": [tx(1, "AAA", "buy", 10, 100, D3, note="Startbestand", source="opening")]}
        points = self.series(transactions=txs).points
        self.assertEqual([p.day for p in points], [D3])
        self.assertAlmostEqual(points[0].result, 10 * 20 * 0.9)

    def test_missing_prices_are_reported_not_invented(self):
        result = self.series(closes={})
        self.assertEqual(result.missing, ["AAA"])
        self.assertTrue(all(p.value == 0 for p in result.points))

    def test_missing_exchange_rate_is_reported(self):
        result = self.series(fx=FxTable({}))
        self.assertEqual(result.missing, ["AAA"])

    def test_stock_without_currency_is_reported(self):
        self.assertEqual(self.series(currencies={}).missing, ["AAA"])

    def test_invalid_history_of_one_stock_does_not_break_the_others(self):
        txs = dict(self.txs, BBB=[tx(5, "BBB", "sell", 1, 10, D1)])  # Verkauf ohne Bestand
        result = self.series(transactions=txs)
        self.assertEqual(result.missing, ["BBB"])
        self.assertAlmostEqual(result.points[0].value, 900.0)

    def test_two_stocks_add_up(self):
        txs = dict(self.txs, BBB=[tx(5, "BBB", "buy", 5, 50, D1)])
        closes = dict(self.closes, BBB={D1: 50.0, D2: 60.0, D3: 70.0})
        result = history.performance_series(txs, closes, {"AAA": "USD", "BBB": "EUR"}, self.fx)
        self.assertAlmostEqual(result.points[0].value, 900.0 + 250.0)
        self.assertAlmostEqual(result.points[1].value, 990.0 + 300.0)

    def test_long_histories_are_thinned_out_keeping_first_and_last_day(self):
        closes = {"AAA": {D1 + dt.timedelta(days=i): 100.0 + i for i in range(1000)}}
        points = history.performance_series(self.txs, closes, {"AAA": "USD"}, self.fx, max_points=50).points
        self.assertEqual(len(points), 50)
        self.assertEqual(points[0].day, D1)
        self.assertEqual(points[-1].day, D1 + dt.timedelta(days=999))

    def test_no_transactions_no_series(self):
        result = history.performance_series({}, {}, {}, self.fx)
        self.assertEqual((result.points, result.missing), ([], []))


class ImportPlanTests(unittest.TestCase):
    def trade(self, id, kind="buy", shares=10, price=100.0, day=D1, symbol="AAPL", fee=0.0):
        return ExternalTrade(id, symbol, kind, shares, price, day, fee)

    def plan(self, trades, existing=(), aliases=None, known=("AAPL", "MSFT"), replace=False):
        return sources.plan_import("etoro", trades, list(existing), aliases or {}, set(known), replace)

    def test_new_trades_are_planned_in_time_order_with_buys_first_on_the_same_day(self):
        plan = self.plan([self.trade("3", "sell", 5, day=D2), self.trade("2", "buy", 5, day=D2),
                          self.trade("1", "buy", 10, day=D1)])
        self.assertEqual([(s, t.external_id) for s, t in plan.new], [("AAPL", "1"), ("AAPL", "2"), ("AAPL", "3")])
        self.assertEqual((plan.duplicates, plan.rejected, plan.unmapped), (0, [], {}))

    def test_already_imported_trades_are_skipped(self):
        existing = [tx(1, "AAPL", "buy", 10, 100, D1, source="etoro", external_id="1")]
        plan = self.plan([self.trade("1"), self.trade("2", day=D2)], existing)
        self.assertEqual((plan.duplicates, [t.external_id for _, t in plan.new]), (1, ["2"]))

    def test_same_id_of_another_source_is_not_a_duplicate(self):
        existing = [tx(1, "AAPL", "buy", 10, 100, D1, source="trading212", external_id="1")]
        self.assertEqual(len(self.plan([self.trade("1")], existing).new), 1)

    def test_the_same_id_twice_in_one_batch_counts_once(self):
        plan = self.plan([self.trade("1"), self.trade("1")])
        self.assertEqual((len(plan.new), plan.duplicates), (1, 1))

    def test_provider_symbol_is_translated_with_an_alias(self):
        plan = self.plan([self.trade("1", symbol="AAPL.US")], aliases={"AAPL.US": "AAPL"})
        self.assertEqual(plan.new[0][0], "AAPL")

    def test_symbols_are_matched_case_insensitively_when_known(self):
        self.assertEqual(self.plan([self.trade("1", symbol="aapl")]).new[0][0], "AAPL")

    def test_unknown_symbols_are_collected_and_not_imported(self):
        plan = self.plan([self.trade("1", symbol="XYZ"), self.trade("2", symbol="XYZ"), self.trade("3", symbol="QQQ")])
        self.assertEqual((plan.unmapped, plan.new), ({"XYZ": 2, "QQQ": 1}, []))

    def test_sale_without_holdings_is_rejected_with_the_reason(self):
        plan = self.plan([self.trade("1", "sell", 5)])
        [(trade, reason)] = plan.rejected
        self.assertEqual(trade.external_id, "1")
        self.assertIn("nur 0", reason)
        self.assertEqual(plan.new, [])

    def test_a_rejected_trade_does_not_block_the_valid_ones(self):
        plan = self.plan([self.trade("1", "buy", 10, day=D1), self.trade("2", "sell", 99, day=D2),
                          self.trade("3", "sell", 4, day=D3)])
        self.assertEqual([t.external_id for _, t in plan.new], ["1", "3"])
        self.assertEqual([t.external_id for t, _ in plan.rejected], ["2"])

    def test_sales_are_checked_against_existing_holdings(self):
        existing = [tx(1, "AAPL", "buy", 10, 100, D1)]
        self.assertEqual(len(self.plan([self.trade("9", "sell", 10, day=D2)], existing).new), 1)
        self.assertEqual(len(self.plan([self.trade("9", "sell", 11, day=D2)], existing).rejected), 1)

    def test_invalid_numbers_are_rejected(self):
        plan = self.plan([self.trade("1", shares=0), self.trade("2", price=-1)])
        self.assertEqual(len(plan.rejected), 2)

    def test_opening_entries_are_kept_by_default(self):
        existing = [tx(1, "AAPL", "buy", 10, 100, D3, note="Startbestand", source="opening")]
        plan = self.plan([self.trade("1", "buy", 10, day=D1)], existing)
        self.assertEqual(plan.remove_ids, [])

    def test_real_purchases_can_replace_the_opening_entry(self):
        existing = [tx(1, "AAPL", "buy", 10, 100, D3, note="Startbestand", source="opening"),
                    tx(2, "MSFT", "buy", 5, 50, D3, note="Startbestand", source="opening")]
        plan = self.plan([self.trade("1", "buy", 10, day=D1)], existing, replace=True)
        self.assertEqual(plan.remove_ids, [1])  # nur die Aktie, für die echte Käufe kommen

    def test_sales_alone_do_not_replace_an_opening_entry(self):
        existing = [tx(1, "AAPL", "buy", 10, 100, D1, note="Startbestand", source="opening")]
        plan = self.plan([self.trade("1", "sell", 2, day=D2)], existing, replace=True)
        self.assertEqual((plan.remove_ids, len(plan.new)), ([], 1))

    def test_after_replacing_the_opening_a_sale_needs_the_real_purchases(self):
        existing = [tx(1, "AAPL", "buy", 10, 100, D3, note="Startbestand", source="opening")]
        plan = self.plan([self.trade("1", "buy", 3, day=D1), self.trade("2", "sell", 8, day=D2)], existing, replace=True)
        self.assertEqual([t.external_id for _, t in plan.new], ["1"])
        self.assertEqual(len(plan.rejected), 1)  # nur 3 Stück gekauft, 8 verkauft

    def test_the_planner_does_not_change_its_inputs(self):
        existing = [tx(1, "AAPL", "buy", 10, 100, D1)]
        before = list(existing)
        self.plan([self.trade("1", "sell", 3, day=D2)], existing)
        self.assertEqual(existing, before)

    def test_base_class_must_be_implemented(self):
        with self.assertRaises(NotImplementedError):
            sources.TransactionSource().fetch("")


if __name__ == "__main__":
    unittest.main()
