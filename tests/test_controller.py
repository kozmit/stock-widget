"""Controller: Übernahme, Transaktionen, Watchlist und Signale, mit temporärer Datenbank und ohne Netzwerk."""
import datetime as dt
from unittest import mock

import stock_data as sd
from tests.support import AppTestCase, QUOTE, wait_until
import stock_widget as w

DAY1, DAY2, DAY3 = dt.date(2026, 1, 1), dt.date(2026, 2, 1), dt.date(2026, 3, 1)


class MigrationTests(AppTestCase):
    legacy = {"symbols": ["GEV", "NVDA"],
              "positions": {"GEV": {"shares": 7.36, "cost": 547.62, "realized": 20.0}}}

    def test_old_watchlist_becomes_symbols_and_opening_purchase(self):
        self.assertEqual(self.ctl.symbols, ["GEV", "NVDA"])
        position = self.ctl.positions["GEV"]
        self.assertAlmostEqual(position["shares"], 7.36)
        self.assertAlmostEqual(position["cost"], 547.62)
        self.assertNotIn("NVDA", self.ctl.positions)

    def test_old_realized_profit_is_kept(self):
        self.assertAlmostEqual(self.ctl.realized["GEV"], 20.0)

    def test_new_controller_on_same_database_does_not_import_twice(self):
        self.ctl.store.close()
        again = w.Controller()
        self.addCleanup(again.store.close)
        self.addCleanup(lambda: again.pool.shutdown(wait=False))
        self.assertEqual(len(again.store.transactions()), 1)
        self.assertEqual(again.symbols, ["GEV", "NVDA"])
        self.ctl = again  # damit das Aufräumen eine offene Datenbank findet


class FreshInstallTests(AppTestCase):
    def test_default_watchlist_without_legacy_file(self):
        self.assertEqual(self.ctl.symbols, ["AAPL", "MSFT", "DELL"])
        self.assertEqual(self.ctl.positions, {})


class TradeTests(AppTestCase):
    def test_buy_buy_partial_sell_gives_correct_remainder(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        c.record_trade("AAPL", "buy", 10, 120, 0, DAY2)
        c.record_trade("AAPL", "sell", 5, 130, 0, DAY3)
        position = c.positions["AAPL"]
        self.assertAlmostEqual(position["shares"], 15)
        self.assertAlmostEqual(position["cost"], 1700 / 15)
        self.assertAlmostEqual(c.realized["AAPL"], 150)

    def test_fees_are_part_of_cost_and_proceeds(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 10, DAY1)
        c.record_trade("AAPL", "sell", 10, 110, 5, DAY2)
        self.assertAlmostEqual(c.realized["AAPL"], (1100 - 5) - (1000 + 10))
        self.assertNotIn("AAPL", c.positions)

    def test_selling_everything_closes_position_but_keeps_realized_profit(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 4, 100, 0, DAY1)
        c.record_trade("AAPL", "sell", 4, 150, 0, DAY2)
        self.assertNotIn("AAPL", c.positions)
        self.assertAlmostEqual(c.realized["AAPL"], 200)

    def test_oversell_is_rejected_and_nothing_is_stored(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        with self.assertRaisesRegex(ValueError, "nur 10"):
            c.record_trade("AAPL", "sell", 11, 100, 0, DAY2)
        self.assertEqual(len(c.store.transactions()), 1)
        self.assertAlmostEqual(c.positions["AAPL"]["shares"], 10)

    def test_sale_dated_before_the_purchase_is_rejected(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY2)
        with self.assertRaises(ValueError):
            c.record_trade("AAPL", "sell", 5, 100, 0, DAY1)

    def test_backdated_purchase_changes_fifo_but_stays_valid(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY2)
        c.record_trade("AAPL", "sell", 5, 150, 0, DAY3)
        c.record_trade("AAPL", "buy", 10, 50, 0, DAY1)  # älter: wird jetzt zuerst verkauft
        self.assertAlmostEqual(c.realized["AAPL"], 5 * 150 - 5 * 50)

    def test_invalid_numbers_are_rejected(self):
        for args in ((0, 100, 0), (5, 0, 0), (5, 100, -1)):
            with self.assertRaises(ValueError):
                self.ctl.record_trade("AAPL", "buy", *args, DAY1)
        self.assertEqual(self.ctl.store.transactions(), [])

    def test_state_survives_a_restart(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        self.ctl.store.close()
        again = w.Controller()
        self.addCleanup(lambda: again.pool.shutdown(wait=False))
        self.ctl = again
        self.assertAlmostEqual(again.positions["AAPL"]["shares"], 10)

    def test_each_sale_gets_its_own_gain(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        c.record_trade("AAPL", "sell", 2, 110, 0, DAY2)
        c.record_trade("AAPL", "sell", 3, 90, 0, DAY3)
        gains = sorted(round(s.gain, 2) for s in c.sales["AAPL"].values())
        self.assertEqual(gains, [-30.0, 20.0])


class StartPositionTests(AppTestCase):
    def test_cost_is_derived_from_current_profit_and_stored_as_a_buy(self):
        self.ctl.start_position("AAPL", 10, 25.0)  # Kurs 100, +25 % -> Einstand 80
        position = self.ctl.positions["AAPL"]
        self.assertAlmostEqual(position["cost"], 80.0)
        [tx] = self.ctl.transactions["AAPL"]
        self.assertEqual((tx.kind, tx.note, tx.day), ("buy", "Startbestand", dt.date.today()))

    def test_negative_profit_gives_higher_cost(self):
        self.ctl.start_position("AAPL", 10, -20.0)
        self.assertAlmostEqual(self.ctl.positions["AAPL"]["cost"], 125.0)

    def test_profit_of_minus_100_percent_or_less_is_rejected(self):
        with self.assertRaises(ValueError):
            self.ctl.start_position("AAPL", 10, -100.0)

    def test_second_start_is_rejected(self):
        self.ctl.start_position("AAPL", 10, 0)
        with self.assertRaises(ValueError):
            self.ctl.start_position("AAPL", 5, 0)

    def test_zero_shares_are_rejected(self):
        with self.assertRaises(ValueError):
            self.ctl.start_position("AAPL", 0, 0)


class DeleteTransactionTests(AppTestCase):
    def test_deleting_a_sale_restores_the_shares(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        c.record_trade("AAPL", "sell", 4, 120, 0, DAY2)
        sale = c.transactions["AAPL"][1]
        c.delete_transaction("AAPL", sale.id)
        self.assertAlmostEqual(c.positions["AAPL"]["shares"], 10)
        self.assertAlmostEqual(c.realized["AAPL"], 0)

    def test_deleting_a_purchase_that_a_sale_depends_on_is_rejected(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        c.record_trade("AAPL", "sell", 4, 120, 0, DAY2)
        purchase = c.transactions["AAPL"][0]
        with self.assertRaisesRegex(ValueError, "Löschen nicht möglich"):
            c.delete_transaction("AAPL", purchase.id)
        self.assertEqual(len(c.store.transactions()), 2)
        self.assertAlmostEqual(c.positions["AAPL"]["shares"], 6)

    def test_deleting_an_unneeded_purchase_works(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        c.record_trade("AAPL", "buy", 5, 100, 0, DAY2)
        c.delete_transaction("AAPL", c.transactions["AAPL"][1].id)
        self.assertAlmostEqual(c.positions["AAPL"]["shares"], 10)


class WatchlistTests(AppTestCase):
    def test_removing_a_symbol_keeps_its_transactions(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        c.remove("AAPL")
        self.assertNotIn("AAPL", c.symbols)
        self.assertNotIn("AAPL", c.positions)
        self.assertEqual(len(c.store.transactions()), 1)

    def test_adding_a_known_symbol_again_restores_the_position(self):
        c = self.ctl
        c.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        c.remove("AAPL")
        c.add_symbol("aapl", lambda symbol: self.fail("Kürzel sollte gültig sein"))
        self.assertTrue(wait_until(lambda: "AAPL" in c.symbols))
        self.assertAlmostEqual(c.positions["AAPL"]["shares"], 10)

    def test_adding_a_new_symbol_uppercases_it_and_stores_it(self):
        self.ctl.add_symbol(" nvda ", lambda symbol: self.fail("Kürzel sollte gültig sein"))
        self.assertTrue(wait_until(lambda: "NVDA" in self.ctl.symbols))
        self.assertIn("NVDA", self.ctl.store.symbols())
        self.assertEqual(self.ctl.quotes["NVDA"]["price"], QUOTE["price"])

    def test_unknown_symbol_reports_an_error_and_is_not_added(self):
        failed = []
        self.ctl.add_symbol("BAD", failed.append)
        self.assertTrue(wait_until(lambda: failed))
        self.assertEqual(failed, ["BAD"])
        self.assertNotIn("BAD", self.ctl.symbols)

    def test_duplicate_and_empty_symbols_are_ignored(self):
        before = list(self.ctl.symbols)
        self.ctl.add_symbol("AAPL", lambda s: None)
        self.ctl.add_symbol("   ", lambda s: None)
        self.app.processEvents()
        self.assertEqual(self.ctl.symbols, before)

    def test_order_of_the_watchlist_is_kept(self):
        self.ctl.add_symbol("ZZZ", lambda s: None)
        self.assertTrue(wait_until(lambda: "ZZZ" in self.ctl.symbols))
        self.assertEqual(self.ctl.symbols, ["AAPL", "MSFT", "DELL", "ZZZ"])


MATCHES = [{"symbol": "DRO.AX", "name": "DroneShield Limited", "exchange": "Australian", "type": "EQUITY"},
           {"symbol": "DRSHF", "name": "Droneshield Ltd", "exchange": "OTC Markets", "type": "EQUITY"}]


class AddSymbolLookupTests(AppTestCase):
    def add(self, text, matches=None):
        errors, choices = [], []
        with mock.patch.object(sd, "search_symbols", lambda q, count=8: matches or []):
            self.ctl.add_symbol(text, errors.append, lambda query, found: choices.append((query, found)))
            wait_until(lambda: errors or choices or len(self.ctl.symbols) > 3)
        return errors, choices

    def test_us_suffix_is_translated(self):
        errors, choices = self.add("ORA.US")
        self.assertEqual((errors, choices), ([], []))
        self.assertIn("ORA", self.ctl.symbols)
        self.assertNotIn("ORA.US", self.ctl.symbols)

    def test_swiss_zurich_suffix_is_translated(self):
        self.add("ABBN.ZU")
        self.assertIn("ABBN.SW", self.ctl.symbols)

    def test_translated_symbol_is_stored_so_it_survives_a_restart(self):
        self.add("ABBN.ZU")
        self.assertIn("ABBN.SW", self.ctl.store.symbols())

    def test_status_tells_about_the_translation(self):
        seen = []
        self.ctl.status.connect(seen.append)
        self.add("ABBN.ZU")
        self.assertIn("ABBN.ZU als ABBN.SW gefunden", seen)

    def statuses(self):
        seen = []
        self.ctl.status.connect(seen.append)
        return seen

    def test_status_says_found_instead_of_staying_on_searching(self):
        seen = self.statuses()
        self.add("NVDA")
        self.assertEqual([m for m in seen if "NVDA" in m][-1], "NVDA gefunden")
        self.assertEqual(seen[0], "Suche NVDA …")

    def test_status_after_unknown_symbol_is_not_stuck_on_searching(self):
        seen = self.statuses()
        self.add("BAD")
        self.assertEqual(seen[-1], "BAD nicht gefunden")

    def test_status_after_multiple_matches_is_not_stuck_on_searching(self):
        seen = self.statuses()
        self.add("droneshield", MATCHES)
        self.assertEqual(seen[-1], "Mehrere Treffer für droneshield")

    def test_status_after_failed_search_is_not_stuck_on_searching(self):
        seen = self.statuses()
        errors = []
        with mock.patch.object(sd, "search_symbols", side_effect=RuntimeError("offline")):
            self.ctl.add_symbol("droneshield", errors.append, self.fail)
            self.assertTrue(wait_until(lambda: errors))
        self.assertEqual(seen[-1], "droneshield: Suche fehlgeschlagen")

    def test_direct_yahoo_symbol_does_not_search(self):
        with mock.patch.object(sd, "search_symbols", side_effect=AssertionError("Suche nicht nötig")):
            self.ctl.add_symbol("DRO.AX", self.fail, self.fail)
            self.assertTrue(wait_until(lambda: "DRO.AX" in self.ctl.symbols))

    def test_name_triggers_search_and_offers_the_matches(self):
        errors, choices = self.add("droneshield", MATCHES)
        self.assertEqual(errors, [])
        self.assertEqual(choices, [("droneshield", MATCHES)])
        self.assertEqual(self.ctl.symbols, ["AAPL", "MSFT", "DELL"])  # nichts ungefragt hinzugefügt

    def test_nothing_found_reports_error(self):
        errors, choices = self.add("droneshield", [])
        self.assertEqual((errors, choices), (["droneshield"], []))

    def test_matches_without_chooser_count_as_not_found(self):
        errors = []
        with mock.patch.object(sd, "search_symbols", lambda q, count=8: MATCHES):
            self.ctl.add_symbol("droneshield", errors.append)
            self.assertTrue(wait_until(lambda: errors))

    def test_search_failure_reports_error(self):
        errors = []
        with mock.patch.object(sd, "search_symbols", side_effect=RuntimeError("offline")):
            self.ctl.add_symbol("droneshield", errors.append, self.fail)
            self.assertTrue(wait_until(lambda: errors))

    def test_translated_duplicate_is_not_added_twice(self):
        self.ctl.add_symbol("MSFT.US", self.fail, self.fail)
        self.assertTrue(wait_until(lambda: "ist schon in der Liste" in self.status_text()))
        self.assertEqual(self.ctl.symbols.count("MSFT"), 1)

    def status_text(self):
        if not hasattr(self, "_status"):
            self._status = []
            self.ctl.status.connect(self._status.append)
        return " ".join(self._status)


class SignalTests(AppTestCase):
    def collect(self, signal):
        seen = []
        signal.connect(lambda *args: seen.append(args))
        return seen

    def test_trade_signal_carries_symbol_and_kind(self):
        seen = self.collect(self.ctl.trade_recorded)
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        self.ctl.record_trade("AAPL", "sell", 1, 100, 0, DAY2)
        self.assertEqual(seen, [("AAPL", "buy"), ("AAPL", "sell")])

    def test_start_position_counts_as_a_buy(self):
        seen = self.collect(self.ctl.trade_recorded)
        self.ctl.start_position("AAPL", 10, 0)
        self.assertEqual(seen, [("AAPL", "buy")])

    def test_rejected_trade_emits_nothing(self):
        trades, changes = self.collect(self.ctl.trade_recorded), self.collect(self.ctl.ledger_changed)
        with self.assertRaises(ValueError):
            self.ctl.record_trade("AAPL", "sell", 5, 100, 0, DAY1)
        self.assertEqual((trades, changes), ([], []))

    def test_deleting_changes_the_ledger_but_is_not_a_trade(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        trades, changes = self.collect(self.ctl.trade_recorded), self.collect(self.ctl.ledger_changed)
        self.ctl.delete_transaction("AAPL", self.ctl.transactions["AAPL"][0].id)
        self.assertEqual(trades, [])
        self.assertEqual(len(changes), 1)
