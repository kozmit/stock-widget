"""Controller: Übernahme, Transaktionen, Watchlist und Signale, mit temporärer Datenbank und ohne Netzwerk."""
import datetime as dt
import json
from unittest import mock

import events as evt
import stock_data as sd
from sources import ExternalTrade, SyncBatch, TransactionSource
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
        self.ctl.shutdown()
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
        self.ctl.shutdown()
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


class FreshnessTests(AppTestCase):
    def restart(self):
        self.ctl.shutdown()
        self.ctl.store.close()
        self.ctl = w.Controller()

    def load(self, symbol="AAPL"):
        self.ctl.quote_times.pop(symbol, None)
        self.ctl.load_quote(symbol)
        self.assertTrue(wait_until(lambda: symbol in self.ctl.quote_times))

    def test_loaded_quote_is_saved_with_source_and_time(self):
        before = dt.datetime.now().replace(microsecond=0)
        self.load()
        saved = self.ctl.store.snapshots()["AAPL"]
        self.assertEqual((saved["price"], saved["currency"], saved["source"]), (100.0, "USD", "Testquelle"))
        self.assertGreaterEqual(saved["fetched_at"], before)

    def test_freshly_loaded_quote_is_not_stale(self):
        self.load()
        fresh = self.ctl.freshness("AAPL")
        self.assertFalse(fresh["stale"])
        self.assertEqual((fresh["source"], fresh["error"]), ("Testquelle", None))
        self.assertEqual(self.ctl.stale_symbols(), [])

    def test_quote_becomes_stale_after_three_refresh_intervals(self):
        self.load()
        fetched = self.ctl.quote_times["AAPL"]
        edge = dt.timedelta(seconds=w.STALE_SECONDS)
        self.assertFalse(self.ctl.freshness("AAPL", fetched + edge)["stale"])
        self.assertTrue(self.ctl.freshness("AAPL", fetched + edge + dt.timedelta(seconds=1))["stale"])
        self.assertEqual(self.ctl.stale_symbols(fetched + edge + dt.timedelta(seconds=1)), ["AAPL"])

    def test_no_quote_means_no_freshness(self):
        self.ctl.quotes.pop("AAPL")
        self.assertIsNone(self.ctl.freshness("AAPL"))
        self.assertNotIn("AAPL", self.ctl.stale_symbols())

    def test_failed_refresh_keeps_last_quote_but_marks_it_stale(self):
        self.load()
        with mock.patch.object(sd, "fetch_quote", side_effect=RuntimeError("offline")):
            self.ctl.load_quote("AAPL")
            self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.quote_errors))
        self.assertEqual(self.ctl.quotes["AAPL"]["price"], 100.0)
        fresh = self.ctl.freshness("AAPL")
        self.assertTrue(fresh["stale"])
        self.assertIn("offline", fresh["error"])
        self.assertEqual(self.ctl.stale_symbols(), ["AAPL"])

    def test_failed_refresh_does_not_touch_the_saved_snapshot(self):
        self.load()
        saved = self.ctl.store.snapshots()["AAPL"]
        with mock.patch.object(sd, "fetch_quote", side_effect=RuntimeError("offline")):
            self.ctl.load_quote("AAPL")
            wait_until(lambda: "AAPL" in self.ctl.quote_errors)
        self.assertEqual(self.ctl.store.snapshots()["AAPL"], saved)

    def test_failed_refresh_tells_the_ui_to_update(self):
        changes = []
        self.ctl.changed.connect(lambda: changes.append(1))
        with mock.patch.object(sd, "fetch_quote", side_effect=RuntimeError("offline")):
            self.ctl.load_quote("AAPL")
            self.assertTrue(wait_until(lambda: changes))

    def test_next_successful_refresh_clears_the_error(self):
        with mock.patch.object(sd, "fetch_quote", side_effect=RuntimeError("offline")):
            self.ctl.load_quote("AAPL")
            wait_until(lambda: "AAPL" in self.ctl.quote_errors)
        self.load()
        self.assertNotIn("AAPL", self.ctl.quote_errors)
        self.assertFalse(self.ctl.freshness("AAPL")["stale"])

    def test_without_any_quote_a_failure_creates_no_fake_number(self):
        self.ctl.quotes.pop("AAPL")
        with mock.patch.object(sd, "fetch_quote", side_effect=RuntimeError("offline")):
            self.ctl.load_quote("AAPL")
            wait_until(lambda: "AAPL" in self.ctl.quote_errors)
        self.assertNotIn("AAPL", self.ctl.quotes)

    def test_last_saved_quotes_are_shown_after_a_restart_with_their_age(self):
        self.load()
        saved_at = self.ctl.quote_times["AAPL"]
        self.restart()
        self.assertEqual(self.ctl.quotes["AAPL"]["price"], 100.0)
        self.assertEqual(self.ctl.quote_times["AAPL"], saved_at)
        self.assertFalse(self.ctl.freshness("AAPL", saved_at)["stale"])
        self.assertTrue(self.ctl.freshness("AAPL", saved_at + dt.timedelta(days=2))["stale"])

    def test_quote_of_a_removed_symbol_is_not_restored(self):
        self.load()
        self.ctl.remove("AAPL")
        self.assertNotIn("AAPL", self.ctl.quote_times)
        self.restart()
        self.assertNotIn("AAPL", self.ctl.quotes)

    def test_new_symbol_gets_its_snapshot_right_away(self):
        self.ctl.add_symbol("NVDA", self.fail)
        self.assertTrue(wait_until(lambda: "NVDA" in self.ctl.symbols))
        self.assertIn("NVDA", self.ctl.store.snapshots())
        self.assertFalse(self.ctl.freshness("NVDA")["stale"])


class InstrumentLoadingTests(AppTestCase):
    def test_refresh_loads_and_saves_master_data_for_every_symbol(self):
        self.ctl.refresh()
        self.assertTrue(wait_until(lambda: len(self.ctl.instruments) == 3))
        info = self.ctl.instruments["AAPL"]
        self.assertEqual((info["name"], info["sector"], info["source"]), ("AAPL Inc.", "Technology", "Yahoo Finance"))
        self.assertEqual(set(self.ctl.store.instruments()), {"AAPL", "MSFT", "DELL"})

    def test_master_data_is_there_after_a_restart_without_new_download(self):
        self.ctl.refresh()
        wait_until(lambda: len(self.ctl.instruments) == 3)
        self.ctl.shutdown()
        self.ctl.store.close()
        with mock.patch.object(sd, "fetch_instrument", side_effect=AssertionError("kein neuer Abruf nötig")):
            self.ctl = w.Controller()
            self.assertEqual(len(self.ctl.instruments), 3)
            self.assertFalse(self.ctl.needs_instrument("AAPL"))

    def test_failed_download_is_not_repeated_every_minute(self):
        calls = []

        def failing(symbol):
            calls.append(symbol)
            raise RuntimeError("offline")

        with mock.patch.object(sd, "fetch_instrument", failing):
            self.ctl.refresh()
            wait_until(lambda: len(calls) == 3)
            self.ctl.refresh()
            wait_until(lambda: False, 300)
        self.assertEqual(sorted(calls), ["AAPL", "DELL", "MSFT"])
        self.assertEqual(self.ctl.instruments, {})

    def test_old_master_data_is_reloaded_but_recent_data_is_not(self):
        now = dt.datetime.now()
        self.ctl.instruments["AAPL"] = {"name": "x", "fetched_at": now - dt.timedelta(days=8)}
        self.ctl.instruments["MSFT"] = {"name": "x", "fetched_at": now - dt.timedelta(days=6)}
        self.assertTrue(self.ctl.needs_instrument("AAPL"))
        self.assertFalse(self.ctl.needs_instrument("MSFT"))
        self.assertTrue(self.ctl.needs_instrument("DELL"))  # noch gar keine

    def test_new_symbol_loads_its_master_data(self):
        self.ctl.add_symbol("NVDA", self.fail)
        self.assertTrue(wait_until(lambda: "NVDA" in self.ctl.instruments))
        self.assertIn("NVDA", self.ctl.store.instruments())

    def test_removed_symbol_drops_its_master_data_from_memory(self):
        self.ctl.refresh()
        wait_until(lambda: len(self.ctl.instruments) == 3)
        self.ctl.remove("AAPL")
        self.assertNotIn("AAPL", self.ctl.instruments)

    def test_master_data_change_tells_the_ui_to_update(self):
        changes = []
        self.ctl.changed.connect(lambda: changes.append(1))
        self.ctl.load_instrument("AAPL")
        self.assertTrue(wait_until(lambda: changes))


class FxLoadingTests(AppTestCase):
    D1 = dt.date(2026, 1, 5)

    def setUp(self):
        super().setUp()
        self.calls = {"rate": [], "history": []}

        def rate(code, base):
            self.calls["rate"].append((code, base))
            return {"USD": 0.9, "CHF": 1.05}[code]

        def history(code, base, start):
            self.calls["history"].append((code, base, start))
            return {start + dt.timedelta(days=i): {"USD": 0.9, "CHF": 1.05}[code]
                    for i in range((dt.date.today() - start).days + 1)}

        for name, fake in (("fetch_fx_rate", rate), ("fetch_fx_history", history)):
            patcher = mock.patch.object(sd, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_only_foreign_currencies_with_transactions_are_needed(self):
        self.ctl.quotes["MSFT"]["currency"] = "EUR"
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
        self.ctl.record_trade("MSFT", "buy", 1, 100, 0, self.D1)
        self.assertEqual(self.ctl.currencies_in_use(), {"USD": self.D1})  # EUR ist die Basis, DELL hat nichts

    def test_earliest_transaction_day_counts_across_symbols(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, dt.date(2026, 3, 1))
        self.ctl.record_trade("DELL", "buy", 1, 100, 0, dt.date(2026, 2, 1))
        self.assertEqual(self.ctl.currencies_in_use(), {"USD": dt.date(2026, 2, 1)})

    def test_pence_quotes_need_pounds(self):
        self.ctl.quotes["AAPL"]["currency"] = "GBp"
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
        self.assertEqual(list(self.ctl.currencies_in_use()), ["GBP"])

    def test_first_trade_loads_current_rate_and_history_and_saves_both(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        self.assertTrue(wait_until(lambda: self.ctl.fx.now("USD") == 0.9 and self.ctl.fx.coverage("USD")))
        self.assertEqual(self.ctl.store.fx_latest()["USD"]["source"], "Yahoo Finance")
        self.assertIn(self.D1, self.ctl.store.fx_rates()["USD"])

    def test_history_starts_a_week_before_the_first_trade(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        self.assertTrue(wait_until(lambda: self.calls["history"]))
        self.assertEqual(self.calls["history"][0], ("USD", "EUR", self.D1 - dt.timedelta(days=7)))

    def test_no_rate_is_fetched_for_a_base_currency_portfolio(self):
        self.ctl.quotes["AAPL"]["currency"] = "EUR"
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        wait_until(lambda: False, 300)
        self.assertEqual(self.calls, {"rate": [], "history": []})

    def test_rates_are_there_after_a_restart_without_new_download(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        wait_until(lambda: self.ctl.fx.coverage("USD") and self.ctl.fx.now("USD"))
        self.ctl.shutdown()
        self.ctl.store.close()
        with mock.patch.object(sd, "fetch_fx_rate", side_effect=RuntimeError("offline")), \
                mock.patch.object(sd, "fetch_fx_history", side_effect=RuntimeError("offline")):
            self.ctl = w.Controller()
            self.assertEqual(self.ctl.fx.now("USD"), 0.9)
            self.assertEqual(self.ctl.fx.on("USD", self.D1), 0.9)

    def test_after_a_restart_only_new_days_are_loaded(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        wait_until(lambda: self.ctl.fx.coverage("USD") and self.ctl.fx.now("USD"))
        self.ctl.shutdown()
        self.ctl.store.close()
        self.calls["history"].clear()
        self.ctl = w.Controller()
        self.ctl.quotes = {s: dict(QUOTE) for s in self.ctl.symbols}
        self.ctl.refresh_fx()
        self.assertTrue(wait_until(lambda: self.calls["history"]))
        self.assertEqual(self.calls["history"][0][2], dt.date.today() - dt.timedelta(days=7))

    def test_history_is_loaded_once_per_start_but_again_after_a_new_trade(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        wait_until(lambda: len(self.calls["history"]) == 1)
        self.ctl.refresh_fx()
        self.ctl.refresh_fx()
        wait_until(lambda: False, 300)
        self.assertEqual(len(self.calls["history"]), 1)
        self.ctl.record_trade("AAPL", "buy", 1, 80, 0, dt.date(2025, 6, 1))  # älterer Kauf braucht mehr Historie
        self.assertTrue(wait_until(lambda: len(self.calls["history"]) == 2))
        self.assertEqual(self.calls["history"][1][2], dt.date(2025, 6, 1) - dt.timedelta(days=7))

    def test_failed_rate_download_is_reported_and_leaves_the_rate_unknown(self):
        seen = []
        self.ctl.status.connect(seen.append)
        with mock.patch.object(sd, "fetch_fx_rate", side_effect=RuntimeError("offline")), \
                mock.patch.object(sd, "fetch_fx_history", side_effect=RuntimeError("offline")):
            self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
            self.assertTrue(wait_until(lambda: "Wechselkurs USD → EUR nicht abrufbar" in seen))
        self.assertIsNone(self.ctl.fx.now("USD"))
        summary = self.ctl.portfolio_summary()
        self.assertEqual(summary.holdings, [])
        self.assertIn("AAPL: kein Wechselkurs USD → EUR, nicht enthalten", summary.warnings)


class PortfolioSummaryTests(AppTestCase):
    D1 = dt.date(2026, 1, 5)

    def setUp(self):
        super().setUp()
        self.ctl.fx.add_history("USD", {self.D1: 0.90})
        self.ctl.fx.set_latest("USD", 0.90, dt.datetime.now(), "Test")

    def test_summary_converts_with_the_stored_rates(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        summary = self.ctl.portfolio_summary()
        self.assertAlmostEqual(summary.value, 900.0)     # 10 * 100 * 0,90
        self.assertAlmostEqual(summary.invested, 720.0)  # 10 * 80 * 0,90
        self.assertEqual(summary.warnings, [])

    def test_summary_follows_the_watchlist_order_and_includes_carried_realized_profit(self):
        self.ctl.store.close()
        self.ctl.shutdown()
        self.ctl = w.Controller()
        self.ctl.quotes = {s: dict(QUOTE) for s in self.ctl.symbols}
        self.ctl.fx.add_history("USD", {self.D1: 0.90})
        self.ctl.fx.set_latest("USD", 0.90, dt.datetime.now(), "Test")
        self.ctl.store.db.execute("UPDATE watchlist SET opening_realized = 10 WHERE symbol = 'DELL'")
        self.ctl.store.db.commit()
        self.ctl.reload_ledger()
        self.ctl.record_trade("MSFT", "buy", 1, 100, 0, self.D1)
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
        summary = self.ctl.portfolio_summary()
        self.assertEqual([h.symbol for h in summary.holdings], ["AAPL", "MSFT"])
        self.assertAlmostEqual(summary.realized, 9.0)  # 10 USD * 0,90

    def test_removed_stock_with_holdings_is_reported_not_silently_dropped(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        self.ctl.remove("AAPL")
        summary = self.ctl.portfolio_summary()
        self.assertEqual(summary.holdings, [])
        self.assertIn("Entfernte Aktien mit Bestand sind nicht enthalten: AAPL", summary.warnings)

    def test_removed_stock_that_was_fully_sold_is_not_reported(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        self.ctl.record_trade("AAPL", "sell", 10, 90, 0, dt.date(2026, 2, 1))
        self.ctl.remove("AAPL")
        self.assertEqual(self.ctl.removed_holdings, [])

    def test_stale_quotes_are_mentioned(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        self.ctl.quote_errors["AAPL"] = "offline"
        warnings = self.ctl.portfolio_summary().warnings
        self.assertIn("1 Kurs veraltet: AAPL", warnings)

    def test_two_stale_quotes_use_the_plural(self):
        self.ctl.quote_errors.update({"AAPL": "offline", "MSFT": "offline"})
        self.assertIn("2 Kurse veraltet: AAPL, MSFT", self.ctl.portfolio_summary().warnings)

    def test_states_follow_the_ledger(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        self.assertEqual(self.ctl.states["AAPL"].shares, 10)
        self.ctl.record_trade("AAPL", "sell", 10, 90, 0, dt.date(2026, 2, 1))
        self.assertEqual(self.ctl.states["AAPL"].shares, 0)
        self.assertEqual(len(self.ctl.states["AAPL"].sales), 1)


class FakeSource(TransactionSource):
    name, label = "etoro", "eToro"

    def __init__(self, trades=(), cursor="c1", error=None):
        self.trades, self.next_cursor, self.error, self.cursors = list(trades), cursor, error, []
        self.cash = None

    def fetch(self, cursor):
        self.cursors.append(cursor)
        if self.error:
            raise self.error
        return SyncBatch(tuple(self.trades), self.next_cursor, cash=self.cash)


def trade(id, kind="buy", shares=10, price=100.0, day=DAY1, symbol="AAPL", fee=0.0):
    return ExternalTrade(id, symbol, kind, shares, price, day, fee)


class EtoroConnectionTests(AppTestCase):
    def test_not_connected_without_keys(self):
        self.assertNotIn("etoro", self.ctl.sources)

    def test_connect_registers_source_and_disconnect_keeps_transactions(self):
        self.ctl.connect_etoro("pub", "usr")
        self.assertEqual(self.ctl.sources["etoro"].label, "eToro")
        self.ctl.import_trades("etoro", [trade("1")])
        self.ctl.disconnect_etoro()
        self.assertNotIn("etoro", self.ctl.sources)
        self.assertEqual(len(self.ctl.transactions["AAPL"]), 1)

    def test_adopt_unknown_puts_new_stocks_on_the_watchlist(self):
        plan = self.ctl.import_trades("etoro", [trade("1", symbol="NVDA")], adopt_unknown=True)
        self.assertEqual(len(plan.new), 1)
        self.assertIn("NVDA", self.ctl.symbols)

    def test_notes_of_the_source_appear_in_the_sync_state(self):
        self.ctl.import_trades("etoro", [trade("1")], notes=("2 Position(en) übersprungen",))
        self.assertIn("2 Position(en) übersprungen", self.ctl.store.sync_state("etoro")["message"])


class OpeningSourceTests(AppTestCase):
    def test_start_position_is_marked_as_opening_balance(self):
        self.ctl.start_position("AAPL", 10, 0)
        [tx] = self.ctl.transactions["AAPL"]
        self.assertEqual((tx.source, tx.note), ("opening", "Startbestand"))

    def test_manual_trades_are_marked_manual(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        self.assertEqual(self.ctl.transactions["AAPL"][0].source, "manual")

    def test_legacy_positions_are_opening_balances(self):
        self.ctl.shutdown()
        self.ctl.store.close()
        with open(sd.LEGACY_FILE, "w", encoding="utf-8") as f:
            json.dump({"symbols": ["GEV"], "positions": {"GEV": {"shares": 7.36, "cost": 547.62, "realized": 0}}}, f)
        import os
        os.remove(sd.DB_FILE)
        self.ctl = w.Controller()
        self.assertEqual(self.ctl.transactions["GEV"][0].source, "opening")


class ImportTests(AppTestCase):
    def signals(self):
        seen = {"ledger": [], "history": [], "changed": []}
        self.ctl.ledger_changed.connect(lambda: seen["ledger"].append(1))
        self.ctl.history_changed.connect(lambda: seen["history"].append(1))
        self.ctl.changed.connect(lambda: seen["changed"].append(1))
        return seen

    def test_imported_trades_become_transactions_with_source_and_external_id(self):
        plan = self.ctl.import_trades("etoro", [trade("1", shares=10, price=100, fee=1.5)])
        self.assertEqual(len(plan.new), 1)
        [tx] = self.ctl.transactions["AAPL"]
        self.assertEqual((tx.source, tx.external_id, tx.shares, tx.price, tx.fee), ("etoro", "1", 10, 100, 1.5))
        self.assertAlmostEqual(self.ctl.positions["AAPL"]["shares"], 10)

    def test_available_cash_is_kept_with_its_time_and_survives_a_restart(self):
        self.ctl.import_trades("etoro", [trade("1")], cash=(2568.15, "USD"))
        amount, currency, at = self.ctl.cash["etoro"]
        self.assertEqual((amount, currency), (2568.15, "USD"))
        self.assertLess(abs((dt.datetime.now() - at).total_seconds()), 60)
        self.ctl.store.close()
        again = w.Controller()
        self.addCleanup(again.shutdown)
        self.assertEqual(again.cash["etoro"][:2], (2568.15, "USD"))

    def test_the_base_currency_can_be_switched_and_is_remembered(self):
        import fx
        self.addCleanup(fx.set_base, "EUR")
        self.assertEqual(fx.BASE, "EUR")
        seen = []
        self.ctl.base_changed.connect(seen.append)
        self.ctl.set_base("USD")
        self.assertEqual((fx.BASE, seen), ("USD", ["USD"]))
        self.ctl.store.close()
        again = w.Controller()
        self.addCleanup(again.shutdown)
        self.assertEqual(fx.BASE, "USD")
        again.set_base("EUR")
        self.assertEqual(fx.BASE, "EUR")

    def test_switching_the_base_drops_the_exchange_rates_of_the_old_base(self):
        import fx
        self.addCleanup(fx.set_base, "EUR")
        self.ctl.store.save_fx_rates("USD", {DAY1: 0.9}, "t")
        self.ctl.store.save_fx_latest("USD", 0.9, dt.datetime.now(), "t")
        self.ctl.fx.add_history("USD", {DAY1: 0.9})
        self.ctl.set_base("USD")
        self.assertEqual((self.ctl.store.fx_rates(), self.ctl.store.fx_latest(), self.ctl.fx.history), ({}, {}, {}))

    def test_old_euro_rates_are_dropped_when_the_database_remembers_another_base(self):
        import fx
        self.addCleanup(fx.set_base, "EUR")
        self.ctl.store.save_fx_rates("USD", {DAY1: 0.9}, "t")
        self.ctl.store.set_meta("base_currency", "USD")  # beim nächsten Start gilt Dollar, die Kurse sind aber Euro-Kurse
        self.ctl.store.close()
        again = w.Controller()
        self.addCleanup(again.shutdown)
        self.assertEqual(again.store.fx_rates(), {})

    def test_an_unknown_base_is_ignored(self):
        import fx
        self.ctl.set_base("GBP")
        self.assertEqual(fx.BASE, "EUR")

    def test_an_import_without_cash_keeps_the_last_known_cash(self):
        self.ctl.import_trades("etoro", [trade("1")], cash=(100.0, "USD"))
        self.ctl.import_trades("etoro", [trade("2", day=DAY2)], cash=None)
        self.assertEqual(self.ctl.cash["etoro"][:2], (100.0, "USD"))

    def test_importing_the_same_trades_again_books_nothing_twice(self):
        self.ctl.import_trades("etoro", [trade("1"), trade("2", day=DAY2)])
        plan = self.ctl.import_trades("etoro", [trade("1"), trade("2", day=DAY2), trade("3", day=DAY3)])
        self.assertEqual((plan.duplicates, len(plan.new)), (2, 1))
        self.assertEqual(len(self.ctl.transactions["AAPL"]), 3)

    def test_import_tells_the_ui_to_update(self):
        seen = self.signals()
        self.ctl.import_trades("etoro", [trade("1")])
        self.assertTrue(seen["ledger"] and seen["history"] and seen["changed"])

    def test_sync_state_is_saved_with_a_summary(self):
        self.ctl.import_trades("etoro", [trade("1"), trade("2", "sell", 99, day=DAY2), trade("3", symbol="ZZZ")],
                               cursor="stand-7")
        state = self.ctl.store.sync_state("etoro")
        self.assertEqual(state["cursor"], "stand-7")
        self.assertEqual(state["message"], "1 neu, 0 schon vorhanden, 1 abgelehnt, 1 mit unbekanntem Kürzel")

    def test_unknown_provider_symbol_is_reported_until_it_is_assigned(self):
        plan = self.ctl.import_trades("etoro", [trade("1", symbol="AAPL.US")])
        self.assertEqual((plan.unmapped, plan.new), ({"AAPL.US": 1}, []))
        self.assertNotIn("AAPL", self.ctl.transactions)
        self.ctl.set_alias("etoro", "AAPL.US", "aapl")
        plan = self.ctl.import_trades("etoro", [trade("1", symbol="AAPL.US")])
        self.assertEqual(len(plan.new), 1)
        self.assertIn("AAPL", self.ctl.transactions)

    def test_alias_survives_a_restart(self):
        self.ctl.set_alias("etoro", "AAPL.US", "AAPL")
        self.ctl.shutdown()
        self.ctl.store.close()
        self.ctl = w.Controller()
        plan = self.ctl.import_trades("etoro", [trade("1", symbol="AAPL.US")])
        self.assertEqual(len(plan.new), 1)

    def test_rejected_trades_are_not_stored_but_valid_ones_are(self):
        plan = self.ctl.import_trades("etoro", [trade("1", "buy", 10), trade("2", "sell", 50, day=DAY2)])
        self.assertEqual([t.external_id for t, _ in plan.rejected], ["2"])
        self.assertEqual(len(self.ctl.transactions["AAPL"]), 1)

    def test_manual_entries_are_untouched_by_an_import(self):
        self.ctl.record_trade("AAPL", "buy", 5, 90, 0, DAY1)
        self.ctl.import_trades("etoro", [trade("1", shares=10, day=DAY2)])
        self.assertEqual(sorted(t.source for t in self.ctl.transactions["AAPL"]), ["etoro", "manual"])
        self.assertAlmostEqual(self.ctl.positions["AAPL"]["shares"], 15)

    def test_real_purchases_can_replace_the_opening_balance(self):
        self.ctl.start_position("AAPL", 10, 0)
        self.ctl.start_position("MSFT", 5, 0)
        self.ctl.import_trades("etoro", [trade("1", shares=10, price=80, day=DAY1)], replace_openings=True)
        self.assertEqual([t.source for t in self.ctl.transactions["AAPL"]], ["etoro"])
        self.assertAlmostEqual(self.ctl.positions["AAPL"]["cost"], 80.0)
        self.assertEqual([t.source for t in self.ctl.transactions["MSFT"]], ["opening"])  # andere Aktie bleibt

    def test_without_the_flag_the_opening_balance_stays(self):
        self.ctl.start_position("AAPL", 10, 0)
        self.ctl.import_trades("etoro", [trade("1", shares=10, price=80, day=DAY1)])
        self.assertEqual(sorted(t.source for t in self.ctl.transactions["AAPL"]), ["etoro", "opening"])
        self.assertAlmostEqual(self.ctl.positions["AAPL"]["shares"], 20)

    def test_imported_trade_brings_back_a_removed_stock(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, DAY1)
        self.ctl.remove("AAPL")
        self.ctl.import_trades("etoro", [trade("1", "sell", 4, day=DAY2)])
        self.assertIn("AAPL", self.ctl.symbols)
        self.assertAlmostEqual(self.ctl.positions["AAPL"]["shares"], 6)

    def test_a_stock_unknown_so_far_is_added_when_its_alias_is_given(self):
        self.ctl.set_alias("etoro", "NEWCO.US", "NEWCO")
        self.ctl.import_trades("etoro", [trade("1", symbol="NEWCO.US")])
        self.assertIn("NEWCO", self.ctl.symbols)
        self.assertTrue(wait_until(lambda: "NEWCO" in self.ctl.quotes))

    def test_deleting_an_imported_entry_is_possible_like_any_other(self):
        self.ctl.import_trades("etoro", [trade("1")])
        self.ctl.delete_transaction("AAPL", self.ctl.transactions["AAPL"][0].id)
        self.assertNotIn("AAPL", self.ctl.positions)
        plan = self.ctl.import_trades("etoro", [trade("1")])  # der nächste Abgleich bringt ihn zurück
        self.assertEqual(len(plan.new), 1)


class SyncTests(AppTestCase):
    def sync(self, name="etoro"):
        results = []
        self.ctl.sync_source(name, results.append, lambda exc: results.append(exc))
        self.assertTrue(wait_until(lambda: results))
        return results[0]

    def test_sync_fetches_in_the_background_and_books_the_result(self):
        source = FakeSource([trade("1"), trade("2", day=DAY2)])
        self.ctl.sources["etoro"] = source
        plan = self.sync()
        self.assertEqual(len(plan.new), 2)
        self.assertEqual(len(self.ctl.transactions["AAPL"]), 2)

    def test_first_sync_starts_without_a_cursor_and_the_next_one_continues_with_the_saved_one(self):
        source = FakeSource([trade("1")], cursor="stand-1")
        self.ctl.sources["etoro"] = source
        self.sync()
        source.next_cursor = "stand-2"
        self.sync()
        self.assertEqual(source.cursors, ["", "stand-1"])
        self.assertEqual(self.ctl.store.sync_state("etoro")["cursor"], "stand-2")

    def test_repeated_sync_is_harmless(self):
        self.ctl.sources["etoro"] = FakeSource([trade("1")])
        self.sync()
        plan = self.sync()
        self.assertEqual((len(plan.new), plan.duplicates), (0, 1))

    def test_a_failing_source_reports_the_error_and_changes_nothing(self):
        self.ctl.sources["etoro"] = FakeSource([trade("1")], error=RuntimeError("API nicht erreichbar"))
        error = self.sync()
        self.assertIn("API nicht erreichbar", str(error))
        self.assertEqual(self.ctl.transactions, {})
        self.assertIsNone(self.ctl.store.sync_state("etoro"))

    def test_unknown_source_is_a_key_error(self):
        with self.assertRaises(KeyError):
            self.ctl.sync_source("nirgends", lambda plan: None, lambda exc: None)

    def test_sync_can_replace_opening_balances(self):
        self.ctl.start_position("AAPL", 10, 0)
        self.ctl.sources["etoro"] = FakeSource([trade("1", shares=10, price=70, day=DAY1)])
        results = []
        self.ctl.sync_source("etoro", results.append, results.append, replace_openings=True)
        wait_until(lambda: results)
        self.assertEqual([t.source for t in self.ctl.transactions["AAPL"]], ["etoro"])


class DailyClosesTests(AppTestCase):
    D1 = dt.date(2026, 1, 5)

    def setUp(self):
        super().setUp()
        self.calls = []

        def closes(symbol, start):
            self.calls.append((symbol, start))
            return {start + dt.timedelta(days=i): 100.0 + i for i in range((dt.date.today() - start).days + 1)}

        patcher = mock.patch.object(sd, "fetch_daily_closes", closes)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_closes_start_a_week_before_the_first_entry_of_that_stock(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
        self.assertTrue(wait_until(lambda: self.calls))
        self.assertEqual(self.calls[0], ("AAPL", self.D1 - dt.timedelta(days=7)))

    def test_only_stocks_with_entries_are_loaded(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
        wait_until(lambda: self.calls)
        wait_until(lambda: False, 300)
        self.assertEqual({symbol for symbol, _ in self.calls}, {"AAPL"})

    def test_closes_are_saved_and_announced(self):
        seen = []
        self.ctl.history_changed.connect(lambda: seen.append(1))
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.closes and seen))
        self.assertIn(self.D1, self.ctl.store.closes()["AAPL"])

    def test_loaded_once_per_start_but_again_after_a_new_entry(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
        wait_until(lambda: len(self.calls) == 1)
        self.ctl.refresh_closes()
        self.ctl.refresh_closes()
        wait_until(lambda: False, 300)
        self.assertEqual(len(self.calls), 1)
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, dt.date(2025, 6, 1))
        self.assertTrue(wait_until(lambda: len(self.calls) == 2))
        self.assertEqual(self.calls[1][1], dt.date(2025, 6, 1) - dt.timedelta(days=7))

    def test_after_a_restart_the_saved_closes_are_there_and_only_new_days_are_loaded(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
        wait_until(lambda: self.ctl.closes.get("AAPL") and self.calls)
        self.ctl.shutdown()
        self.ctl.store.close()
        self.calls.clear()
        self.ctl = w.Controller()
        self.assertIn(self.D1, self.ctl.closes["AAPL"])
        self.ctl.refresh_closes()
        self.assertTrue(wait_until(lambda: self.calls))
        self.assertEqual(self.calls[0][1], dt.date.today() - dt.timedelta(days=7))

    def test_failure_is_reported_and_leaves_no_fake_data(self):
        seen = []
        self.ctl.status.connect(seen.append)
        with mock.patch.object(sd, "fetch_daily_closes", side_effect=RuntimeError("offline")):
            self.ctl.record_trade("AAPL", "buy", 1, 100, 0, self.D1)
            self.assertTrue(wait_until(lambda: "AAPL: Kursverlauf nicht abrufbar" in seen))
        self.assertNotIn("AAPL", self.ctl.closes)

    def test_performance_uses_closes_exchange_rates_and_the_ledger(self):
        self.ctl.fx.add_history("USD", {self.D1: 0.90})
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, self.D1)
        wait_until(lambda: self.ctl.closes.get("AAPL"))
        result = self.ctl.performance()
        self.assertEqual(result.points[0].day, self.D1)
        self.assertAlmostEqual(result.points[0].invested, 900.0)
        self.assertAlmostEqual(result.points[0].value, 10 * 107.0 * 0.90)  # Testkurse: 100 + Tage seit Beginn (7 Tage vorher)

    def test_performance_ignores_stocks_that_left_the_watchlist(self):
        self.ctl.fx.add_history("USD", {self.D1: 0.90})
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, self.D1)
        self.ctl.remove("AAPL")
        self.assertEqual(self.ctl.performance().points, [])

    def test_performance_without_entries_is_empty(self):
        self.assertEqual(self.ctl.performance().points, [])


TODAY = dt.date.today()


def day(n):
    return TODAY + dt.timedelta(days=n)


class YahooEventsTests(AppTestCase):
    def record(self, fetched, symbol="AAPL"):
        return self.ctl.record_yahoo_events(symbol, fetched)

    def merged(self, symbol="AAPL"):
        return self.ctl.calendar_events(symbol)

    def test_a_reported_date_becomes_an_expected_event_from_yahoo(self):
        self.assertTrue(self.record([(day(10), "Quartalszahlen")]))
        [item] = self.merged()
        event = item.event
        self.assertEqual((event.kind, event.title, event.day, event.status), ("earnings", "Quartalszahlen", day(10), "expected"))
        self.assertEqual((event.source, event.relevance, event.precision), ("Yahoo Finance", 3, "day"))

    def test_yahoo_never_claims_confirmation(self):
        self.record([(day(3), "Quartalszahlen"), (day(5), "Ex-Dividende"), (day(9), "Dividendenzahlung")])
        self.assertEqual({m.status for m in self.merged()}, {"expected"})

    def test_each_kind_gets_its_own_relevance(self):
        self.record([(day(3), "Quartalszahlen"), (day(5), "Ex-Dividende"), (day(9), "Dividendenzahlung")])
        self.assertEqual({m.event.kind: m.event.relevance for m in self.merged()},
                         {"earnings": 3, "ex_dividend": 2, "dividend": 1})

    def test_two_dates_of_one_kind_are_a_range(self):
        self.record([(day(10), "Quartalszahlen"), (day(14), "Quartalszahlen")])
        [item] = self.merged()
        self.assertEqual((item.event.day, item.event.end), (day(10), day(14)))

    def test_reporting_the_same_again_changes_nothing_and_stays_silent(self):
        self.record([(day(10), "Quartalszahlen")])
        seen = []
        self.ctl.calendar_changed.connect(lambda: seen.append(1))
        self.assertFalse(self.record([(day(10), "Quartalszahlen")]))
        self.assertEqual((seen, len(self.ctl.store.events())), ([], 1))

    def test_a_postponed_date_replaces_the_old_one(self):
        self.record([(day(10), "Quartalszahlen")])
        self.assertTrue(self.record([(day(17), "Quartalszahlen")]))
        [event] = self.ctl.store.events()
        self.assertEqual(event.day, day(17))

    def test_a_date_that_has_passed_stays_as_occurred(self):
        self.record([(day(-3), "Quartalszahlen")])  # gemeldet, bevor es verging
        self.record([(day(60), "Quartalszahlen")])
        statuses = sorted(m.status for m in self.merged())
        self.assertEqual(statuses, ["expected", "occurred"])

    def test_a_kind_yahoo_does_not_mention_this_time_is_kept(self):
        self.record([(day(10), "Quartalszahlen"), (day(12), "Ex-Dividende")])
        self.record([(day(10), "Quartalszahlen")])
        self.assertEqual({m.event.kind for m in self.merged()}, {"earnings", "ex_dividend"})

    def test_an_empty_answer_deletes_nothing(self):
        self.record([(day(10), "Quartalszahlen")])
        self.assertFalse(self.record([]))
        self.assertFalse(self.record(None))
        self.assertEqual(len(self.ctl.store.events()), 1)

    def test_unknown_labels_are_ignored(self):
        self.assertFalse(self.record([(day(10), "Mondfinsternis")]))
        self.assertEqual(self.ctl.store.events(), [])

    def test_stocks_are_kept_apart(self):
        self.record([(day(10), "Quartalszahlen")], "AAPL")
        self.record([(day(11), "Quartalszahlen")], "MSFT")
        self.assertEqual([m.event.day for m in self.merged("MSFT")], [day(11)])

    def test_loading_in_the_background_fills_the_calendar_and_the_cards_column(self):
        with mock.patch.object(sd, "fetch_events", lambda s: [(day(6), "Ex-Dividende"), (day(20), "Quartalszahlen")]):
            self.ctl.load_events("AAPL")
            self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.events))
        self.assertEqual(self.ctl.events["AAPL"], [(day(6), "Ex-Dividende"), (day(20), "Quartalszahlen")])
        self.assertEqual(len(self.ctl.store.events()), 2)

    def test_a_failed_download_keeps_what_is_stored(self):
        self.record([(day(10), "Quartalszahlen")])
        with mock.patch.object(sd, "fetch_events", side_effect=RuntimeError("offline")):
            self.ctl.load_events("AAPL")
            wait_until(lambda: False, 300)
        self.assertEqual(len(self.merged()), 1)


class ManualEventsTests(AppTestCase):
    def add(self, **overrides):
        args = dict(symbol="AAPL", kind="product", title="Vision Pro 3", when="Mai 2027", status="speculative")
        args.update(overrides)
        return self.ctl.add_event(**args)

    def test_a_manual_event_is_saved_and_shown(self):
        event_id = self.add()
        [item] = self.ctl.calendar_events("AAPL")
        self.assertEqual((item.event.id, item.event.source, item.event.precision, item.status),
                         (event_id, "manual", "month", "speculative"))
        self.assertEqual(item.event.title, "Vision Pro 3")

    def test_the_stock_must_be_on_one_of_the_lists(self):
        with self.assertRaisesRegex(ValueError, "Aktie"):
            self.add(symbol="NIRGENDS")

    def test_invalid_input_is_reported_and_nothing_is_saved(self):
        for overrides in ({"when": "bald"}, {"status": "sicher"}, {"kind": "party"}):
            with self.assertRaises(ValueError):
                self.add(**overrides)
        self.assertEqual(self.ctl.store.events(), [])

    def test_the_stock_may_be_given_in_lower_case(self):
        self.add(symbol="aapl")
        self.assertEqual(self.ctl.calendar_events("AAPL")[0].event.symbol, "AAPL")

    def test_update_changes_the_event_but_not_its_stock_or_source(self):
        event_id = self.add()
        self.ctl.update_event(event_id, "product", "Neuer Titel", "15.05.2027", "confirmed", 3, "Notiz")
        [item] = self.ctl.calendar_events("AAPL")
        self.assertEqual((item.event.title, item.event.precision, item.event.relevance, item.event.note),
                         ("Neuer Titel", "day", 3, "Notiz"))
        self.assertEqual((item.status, item.event.symbol, item.event.source), ("confirmed", "AAPL", "manual"))

    def test_delete_removes_the_event(self):
        event_id = self.add()
        self.ctl.delete_event(event_id)
        self.assertEqual(self.ctl.calendar_events("AAPL"), [])

    def test_yahoo_events_cannot_be_changed_or_deleted(self):
        self.ctl.record_yahoo_events("AAPL", [(day(10), "Quartalszahlen")])
        event_id = self.ctl.store.events()[0].id
        with self.assertRaisesRegex(ValueError, "Yahoo Finance"):
            self.ctl.update_event(event_id, "earnings", "x", "01.01.2027", "confirmed")
        with self.assertRaisesRegex(ValueError, "Yahoo Finance"):
            self.ctl.delete_event(event_id)
        self.assertEqual(len(self.ctl.store.events()), 1)

    def test_a_missing_event_is_reported(self):
        with self.assertRaisesRegex(ValueError, "existiert nicht mehr"):
            self.ctl.delete_event(999)

    def test_a_manual_event_merges_with_the_same_event_from_yahoo(self):
        self.ctl.record_yahoo_events("AAPL", [(day(10), "Quartalszahlen")])
        self.add(kind="earnings", title="Zahlen laut IR", when=day(12).strftime("%d.%m.%Y"), status="confirmed")
        [item] = self.ctl.calendar_events("AAPL")
        self.assertEqual((item.status, item.sources), ("confirmed", ("Yahoo Finance", "Manuell")))
        self.assertEqual(item.event.title, "Zahlen laut IR")
        self.assertEqual(item.other_dates, [(evt.date_text(self.ctl.store.events()[0]), "Yahoo Finance")])

    def test_events_survive_a_restart(self):
        self.add()
        self.ctl.shutdown()
        self.ctl.store.close()
        self.ctl = w.Controller()
        self.assertEqual(len(self.ctl.calendar_events("AAPL")), 1)

    def test_events_of_a_removed_stock_are_hidden_and_come_back_with_it(self):
        self.add()
        self.ctl.remove("AAPL")
        self.assertEqual(self.ctl.calendar_events(), [])
        self.assertEqual(len(self.ctl.store.events()), 1)
        self.ctl.store.add_symbol("AAPL")
        self.ctl.symbols = self.ctl.store.symbols()
        self.assertEqual(len(self.ctl.calendar_events()), 1)

    def test_calendar_signals(self):
        seen = []
        self.ctl.calendar_changed.connect(lambda: seen.append(1))
        event_id = self.add()
        self.ctl.update_event(event_id, "product", "x", "2027", "speculative")
        self.ctl.delete_event(event_id)
        self.assertEqual(len(seen), 3)


class CalendarViewTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.record_yahoo_events("AAPL", [(day(3), "Quartalszahlen"), (day(25), "Ex-Dividende")])
        self.ctl.record_yahoo_events("MSFT", [(day(5), "Quartalszahlen")])
        self.ctl.add_event("DELL", "product", "Release", "Oktober 2099", "speculative")
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, day(-30))

    def symbols(self, window):
        return [m.event.symbol + ":" + m.event.kind for m in window]

    def test_windows_of_7_30_and_90_days(self):
        self.assertEqual(self.symbols(self.ctl.calendar_window(7)[0]), ["AAPL:earnings", "MSFT:earnings"])
        self.assertEqual(self.symbols(self.ctl.calendar_window(30)[0]),
                         ["AAPL:earnings", "MSFT:earnings", "AAPL:ex_dividend"])

    def test_only_positions(self):
        self.assertEqual(self.symbols(self.ctl.calendar_window(30, positions_only=True)[0]),
                         ["AAPL:earnings", "AAPL:ex_dividend"])

    def test_events_without_an_exact_day_are_listed_apart_and_only_when_they_overlap(self):
        self.ctl.add_event("DELL", "product", "Jetzt", TODAY.strftime("%m/%Y"), "expected")
        dated, undated = self.ctl.calendar_window(30)
        self.assertEqual([m.event.title for m in undated], ["Jetzt"])
        self.assertNotIn("DELL:product", self.symbols(dated))

    def test_the_cards_column_gets_precise_upcoming_events_only(self):
        self.ctl.events["DELL"] = None  # als geladen markieren
        self.ctl.add_event("DELL", "product", "Release", day(15).strftime("%d.%m.%Y"), "confirmed")
        self.ctl.add_event("DELL", "conference", "Messe", TODAY.strftime("%m/%Y"), "expected")
        self.assertEqual(self.ctl.events["DELL"], [(day(15), "Produktstart")])

    def test_the_cards_column_uses_the_kind_not_the_free_title(self):
        self.ctl.events["MSFT"] = []
        self.ctl.add_event("MSFT", "regulatory", "Sehr langer eigener Titel der Behörde", day(2).strftime("%d.%m.%Y"), "expected")
        self.assertEqual(self.ctl.events["MSFT"][0], (day(2), "Genehmigung oder Entscheidung"))

    def test_a_range_that_is_running_starts_today_in_the_cards_column(self):
        self.ctl.record_yahoo_events("DELL", [(day(-1), "Quartalszahlen"), (day(2), "Quartalszahlen")])
        self.assertEqual(self.ctl.upcoming_pairs("DELL")[0][0], TODAY)

    def test_stocks_not_loaded_yet_get_no_card_entry_from_the_calendar(self):
        self.ctl.add_event("DELL", "product", "Release", day(15).strftime("%d.%m.%Y"), "confirmed")
        self.assertNotIn("DELL", self.ctl.events)

    def test_short_names_exist_for_every_kind(self):
        for label in evt.KINDS.values():
            self.assertIn(label, sd.EVENT_SHORT)
        self.assertTrue(sd.format_event((day(5), "Produktstart"), short=True).startswith("Release"))


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
