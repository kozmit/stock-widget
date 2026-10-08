"""Portfolio-Kennzahlen, Wechselkurse und die dafür erweiterte Buchführung, mit von Hand nachgerechneten Zahlen."""
import datetime as dt
import unittest

import ledger
import portfolio
from fx import BASE, FxTable, currency_code, split_currency
from ledger import Transaction

D1, D2, D3 = dt.date(2026, 1, 5), dt.date(2026, 2, 10), dt.date(2026, 3, 2)


def tx(id, symbol, kind, shares, price, day, fee=0.0, note=""):
    return Transaction(id, symbol, kind, shares, price, fee, day, note)


class LedgerLotTests(unittest.TestCase):
    def test_lots_remember_buy_date_and_cost_per_share_including_fee(self):
        state = ledger.replay([tx(1, "A", "buy", 10, 100, D1, fee=10), tx(2, "A", "buy", 10, 120, D2)])
        self.assertEqual(state.lots, (ledger.Lot(D1, 10, 101.0), ledger.Lot(D2, 10, 120.0)))

    def test_sale_lists_the_consumed_purchases_oldest_first(self):
        state = ledger.replay([tx(1, "A", "buy", 10, 100, D1, fee=10), tx(2, "A", "buy", 10, 120, D2),
                               tx(3, "A", "sell", 15, 130, D3)])
        [sale] = state.sales
        self.assertEqual(sale.pieces, (ledger.Piece(D1, 10, 1010.0), ledger.Piece(D2, 5, 600.0)))
        self.assertAlmostEqual(sum(p.cost for p in sale.pieces), sale.cost)
        self.assertEqual(state.lots, (ledger.Lot(D2, 5, 120.0),))

    def test_opening_purchases_are_marked(self):
        state = ledger.replay([tx(1, "A", "buy", 5, 80, D1, note="Startbestand (aus dem bisherigen Widget)"),
                               tx(2, "A", "buy", 5, 90, D2)])
        self.assertEqual([lot.opening for lot in state.lots], [True, False])

    def test_opening_flag_travels_into_sold_pieces(self):
        state = ledger.replay([tx(1, "A", "buy", 5, 80, D1, note="Startbestand"), tx(2, "A", "sell", 2, 90, D2)])
        self.assertTrue(state.sales[0].pieces[0].opening)

    def test_closed_position_has_no_lots(self):
        state = ledger.replay([tx(1, "A", "buy", 5, 80, D1), tx(2, "A", "sell", 5, 90, D2)])
        self.assertEqual(state.lots, ())


class FxTableTests(unittest.TestCase):
    def setUp(self):
        self.fx = FxTable({"USD": {dt.date(2026, 1, 5): 0.90, dt.date(2026, 1, 6): 0.91, dt.date(2026, 1, 9): 0.92}},
                          {"USD": {"rate": 0.80, "fetched_at": dt.datetime(2026, 10, 9), "source": "Yahoo Finance"}})

    def test_base_currency_is_always_one(self):
        self.assertEqual(self.fx.now(BASE), 1.0)
        self.assertEqual(self.fx.on(BASE, D1), 1.0)

    def test_exact_day(self):
        self.assertEqual(self.fx.on("USD", dt.date(2026, 1, 6)), 0.91)

    def test_weekend_uses_the_last_trading_day(self):
        self.assertEqual(self.fx.on("USD", dt.date(2026, 1, 10)), 0.92)  # Samstag nach dem 9.
        self.assertEqual(self.fx.on("USD", dt.date(2026, 1, 7)), 0.91)

    def test_before_the_history_starts_the_first_rate_is_used(self):
        self.assertEqual(self.fx.on("USD", dt.date(2025, 12, 1)), 0.90)

    def test_after_the_history_the_last_rate_is_used(self):
        self.assertEqual(self.fx.on("USD", dt.date(2026, 6, 1)), 0.92)

    def test_unknown_currency_gives_none(self):
        self.assertIsNone(self.fx.on("CHF", D1))
        self.assertIsNone(self.fx.now("CHF"))

    def test_now_uses_latest_rate(self):
        self.assertEqual(self.fx.now("USD"), 0.80)

    def test_now_falls_back_to_the_newest_daily_rate(self):
        fx = FxTable({"USD": {dt.date(2026, 1, 5): 0.90, dt.date(2026, 1, 6): 0.91}})
        self.assertEqual(fx.now("USD"), 0.91)

    def test_pence_are_converted_to_pounds(self):
        fx = FxTable({"GBP": {D1: 1.2}}, {"GBP": {"rate": 1.25, "fetched_at": None, "source": ""}})
        self.assertAlmostEqual(fx.on("GBp", D1), 0.012)
        self.assertAlmostEqual(fx.now("GBp"), 0.0125)
        self.assertEqual(split_currency("GBp"), ("GBP", 0.01))
        self.assertEqual(currency_code("GBp"), "GBP")
        self.assertEqual(currency_code("USD"), "USD")

    def test_adding_history_extends_the_table_and_coverage(self):
        self.assertEqual(self.fx.coverage("USD"), (dt.date(2026, 1, 5), dt.date(2026, 1, 9)))
        self.fx.add_history("USD", {dt.date(2026, 1, 12): 0.93})
        self.assertEqual(self.fx.coverage("USD")[1], dt.date(2026, 1, 12))
        self.assertEqual(self.fx.on("USD", dt.date(2026, 1, 13)), 0.93)
        self.assertIsNone(self.fx.coverage("CHF"))


class SummaryTests(unittest.TestCase):
    """AAA in USD: 10 Stück zu 100 am 05.01. (Kurs 0,90), 4 verkauft zu 130 am 10.02. (Kurs 0,85), heute 120 (0,80).
    BBB in EUR: 5 Stück zu 50, heute 60."""

    def setUp(self):
        self.states = {
            "AAA": ledger.replay([tx(1, "AAA", "buy", 10, 100, D1), tx(2, "AAA", "sell", 4, 130, D2)]),
            "BBB": ledger.replay([tx(3, "BBB", "buy", 5, 50, D1)]),
        }
        self.quotes = {"AAA": {"price": 120.0, "currency": "USD"}, "BBB": {"price": 60.0, "currency": "EUR"}}
        self.instruments = {"AAA": {"name": "Alpha", "sector": "Technology", "country": "United States"},
                            "BBB": {"name": "Beta", "sector": "Industrials", "country": "Germany"}}
        self.fx = FxTable({"USD": {D1: 0.90, D2: 0.85}},
                          {"USD": {"rate": 0.80, "fetched_at": dt.datetime(2026, 10, 9), "source": ""}})

    def summary(self, **overrides):
        args = dict(states=self.states, quotes=self.quotes, instruments=self.instruments, fx=self.fx)
        args.update(overrides)
        return portfolio.summarize(**args)

    def holding(self, summary, symbol):
        return next(h for h in summary.holdings if h.symbol == symbol)

    def test_foreign_currency_position_split_into_price_and_currency_effect(self):
        h = self.holding(self.summary(), "AAA")
        self.assertAlmostEqual(h.cost, 540.0)           # 6 * 100 * 0,90
        self.assertAlmostEqual(h.value, 576.0)          # 6 * 120 * 0,80
        self.assertAlmostEqual(h.unrealized, 36.0)
        self.assertAlmostEqual(h.price_effect, 108.0)   # (720 - 600) * 0,90
        self.assertAlmostEqual(h.fx_effect, -72.0)      # 720 * (0,80 - 0,90)
        self.assertAlmostEqual(h.price_effect + h.fx_effect, h.unrealized)

    def test_base_currency_position_has_no_currency_effect(self):
        h = self.holding(self.summary(), "BBB")
        self.assertAlmostEqual((h.cost, h.value, h.unrealized, h.fx_effect), (250.0, 300.0, 50.0, 0.0))
        self.assertAlmostEqual(h.pl_pct, 20.0)

    def test_totals(self):
        s = self.summary()
        self.assertAlmostEqual(s.value, 876.0)
        self.assertAlmostEqual(s.invested, 790.0)
        self.assertAlmostEqual(s.unrealized, 86.0)
        self.assertAlmostEqual(s.unrealized_pct, 86 / 790 * 100)
        self.assertAlmostEqual(s.price_effect, 158.0)
        self.assertAlmostEqual(s.fx_effect, -72.0)

    def test_realized_gain_with_currency_effect(self):
        s = self.summary()
        self.assertAlmostEqual(s.realized, 82.0)        # 520 * 0,85 - 400 * 0,90
        self.assertAlmostEqual(s.realized_price, 108.0)  # (520 - 400) * 0,90
        self.assertAlmostEqual(s.realized_fx, -26.0)
        self.assertAlmostEqual(s.sold_cost, 360.0)

    def test_total_result_and_return_on_all_capital_ever_invested(self):
        s = self.summary()
        self.assertAlmostEqual(s.total_result, 168.0)
        self.assertAlmostEqual(s.total_invested, 1150.0)
        self.assertAlmostEqual(s.total_return_pct, 168 / 1150 * 100)

    def test_shares_of_total_value_add_up_to_one_hundred(self):
        s = self.summary()
        self.assertAlmostEqual(self.holding(s, "AAA").share, 576 / 876 * 100)
        self.assertAlmostEqual(sum(h.share for h in s.holdings), 100.0)

    def test_selling_later_does_not_change_the_earlier_sale(self):
        before = self.summary().realized
        self.states["AAA"] = ledger.replay([tx(1, "AAA", "buy", 10, 100, D1), tx(2, "AAA", "sell", 4, 130, D2),
                                            tx(4, "AAA", "sell", 6, 90, D3)])
        self.fx.add_history("USD", {D3: 0.88})
        after = self.summary()
        # der erste Verkauf trägt weiterhin genau 82 bei, der zweite kommt dazu: 540 * 0,88 - 600 * 0,90 = -64,8
        self.assertAlmostEqual(after.realized, before + (540 * 0.88 - 600 * 0.90))
        self.assertEqual([h.symbol for h in after.holdings], ["BBB"])

    def test_empty_portfolio_has_no_percentages(self):
        s = portfolio.summarize({}, {}, {}, self.fx)
        self.assertEqual((s.value, s.invested), (0.0, 0.0))
        self.assertIsNone(s.unrealized_pct)
        self.assertIsNone(s.total_return_pct)

    def test_pence_quotation(self):
        states = {"VOD": ledger.replay([tx(1, "VOD", "buy", 100, 120, D1)])}  # 120 Pence
        fx = FxTable({"GBP": {D1: 1.2}}, {"GBP": {"rate": 1.2, "fetched_at": None, "source": ""}})
        s = portfolio.summarize(states, {"VOD": {"price": 150.0, "currency": "GBp"}}, {}, fx)
        h = s.holdings[0]
        self.assertAlmostEqual(h.cost, 144.0)    # 100 * 120 * 0,01 * 1,2
        self.assertAlmostEqual(h.value, 180.0)
        self.assertAlmostEqual(h.unrealized, 36.0)

    def test_missing_exchange_rate_excludes_the_position_and_says_so(self):
        states = {"CCC": ledger.replay([tx(1, "CCC", "buy", 10, 100, D1)])}
        s = portfolio.summarize(states, {"CCC": {"price": 120.0, "currency": "CHF"}}, {}, self.fx)
        self.assertEqual(s.holdings, [])
        self.assertEqual(s.value, 0.0)
        self.assertIn("CCC: kein Wechselkurs CHF → EUR, nicht enthalten", s.warnings)

    def test_missing_quote_excludes_the_position_and_says_so(self):
        s = self.summary(quotes={"BBB": self.quotes["BBB"]})
        self.assertEqual([h.symbol for h in s.holdings], ["BBB"])
        self.assertIn("AAA: kein Kurs, nicht enthalten", s.warnings)

    def test_sales_without_exchange_rate_are_left_out_and_reported(self):
        s = self.summary(fx=FxTable({}, {"USD": {"rate": 0.8, "fetched_at": None, "source": ""}}))
        self.assertAlmostEqual(s.realized, 0.0)
        self.assertTrue(any("AAA: 1 Verkauf(e) ohne Wechselkurs" in w for w in s.warnings))

    def test_opening_balance_in_foreign_currency_is_flagged(self):
        self.states["AAA"] = ledger.replay([tx(1, "AAA", "buy", 6, 100, D1, note="Startbestand")])
        s = self.summary()
        self.assertTrue(any("Startbestand (1 Positionen in Fremdwährung)" in w for w in s.warnings))

    def test_opening_balance_in_base_currency_needs_no_warning(self):
        self.states["BBB"] = ledger.replay([tx(3, "BBB", "buy", 5, 50, D1, note="Startbestand")])
        s = self.summary()
        self.assertFalse(any("Startbestand" in w for w in s.warnings))

    def test_carried_realized_profit_is_converted_at_todays_rate_and_flagged(self):
        s = self.summary(opening_realized={"AAA": 50.0})
        self.assertAlmostEqual(s.realized, 82.0 + 50 * 0.80)
        self.assertTrue(any("Übernommener realisierter Gewinn" in w for w in s.warnings))

    def test_clean_portfolio_has_no_warnings(self):
        self.assertEqual(self.summary().warnings, [])

    def test_order_follows_the_watchlist(self):
        s = self.summary(order=["BBB", "AAA"])
        self.assertEqual([h.symbol for h in s.holdings], ["BBB", "AAA"])


class AllocationTests(unittest.TestCase):
    def holdings(self):
        def holding(symbol, value, currency="USD", sector="", country=""):
            return portfolio.Holding(symbol, "", currency, 1, 1, 1, value, value, 0, 0, sector, country)
        return [holding("A", 500, "USD", "Technology", "United States"),
                holding("B", 300, "EUR", "Industrials", "Germany"),
                holding("C", 100, "USD", "Technology", "United States"),
                holding("D", 100, "GBp", "", "")]

    def test_by_position_sorted_by_value(self):
        result = portfolio.allocation(self.holdings(), "position")
        self.assertEqual([r[0] for r in result][:2], ["A", "B"])
        self.assertAlmostEqual(result[0][2], 50.0)
        self.assertAlmostEqual(sum(r[2] for r in result), 100.0)

    def test_by_sector_sums_positions_and_names_the_unknown(self):
        result = dict((label, value) for label, value, _ in portfolio.allocation(self.holdings(), "sector"))
        self.assertEqual(result, {"Technology": 600, "Industrials": 300, portfolio.UNKNOWN: 100})

    def test_by_country(self):
        result = dict((label, value) for label, value, _ in portfolio.allocation(self.holdings(), "country"))
        self.assertEqual(result["United States"], 600)
        self.assertEqual(result[portfolio.UNKNOWN], 100)

    def test_by_currency_uses_the_main_unit_of_pence(self):
        result = dict((label, value) for label, value, _ in portfolio.allocation(self.holdings(), "currency"))
        self.assertEqual(result, {"USD": 600, "EUR": 300, "GBP": 100})

    def test_many_slices_are_grouped_into_a_remainder(self):
        many = [portfolio.Holding(f"S{i}", "", "USD", 1, 1, 1, 100 - i, 1, 0, 0) for i in range(12)]
        result = portfolio.allocation(many, "position", max_items=8)
        self.assertEqual(len(result), 8)
        self.assertEqual(result[-1][0], "Übrige (5)")
        self.assertAlmostEqual(sum(r[2] for r in result), 100.0)

    def test_empty_portfolio_has_no_slices(self):
        self.assertEqual(portfolio.allocation([], "sector"), [])

    def test_unknown_dimension_is_an_error(self):
        with self.assertRaises(KeyError):
            portfolio.allocation(self.holdings(), "farbe")


if __name__ == "__main__":
    unittest.main()
