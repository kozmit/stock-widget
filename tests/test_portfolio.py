import datetime as dt
import json
import os
import tempfile
import unittest

from ledger import Transaction, replay
from store import Store


def tx(id, kind, shares, price, fee=0.0, day=(2026, 1, 1)):
    return Transaction(id, "TEST", kind, shares, price, fee, dt.date(*day))


class LedgerTests(unittest.TestCase):
    def test_buy_buy_partial_sell_leaves_correct_remainder(self):
        state = replay([tx(1, "buy", 10, 100, day=(2026, 1, 1)),
                        tx(2, "buy", 10, 120, day=(2026, 2, 1)),
                        tx(3, "sell", 5, 130, day=(2026, 3, 1))])
        self.assertAlmostEqual(state.shares, 15)
        # FIFO: verkauft werden 5 Stück zu 100, übrig 5 zu 100 und 10 zu 120 = 1700
        self.assertAlmostEqual(state.cost_total, 1700)
        self.assertAlmostEqual(state.avg_cost, 1700 / 15)
        self.assertAlmostEqual(state.realized, 5 * 130 - 5 * 100)

    def test_fees_raise_cost_and_lower_proceeds(self):
        state = replay([tx(1, "buy", 10, 100, fee=10, day=(2026, 1, 1)),
                        tx(2, "sell", 10, 110, fee=5, day=(2026, 2, 1))])
        self.assertAlmostEqual(state.realized, (1100 - 5) - (1000 + 10))
        self.assertEqual(state.shares, 0)

    def test_sale_spanning_two_lots(self):
        state = replay([tx(1, "buy", 10, 100, day=(2026, 1, 1)),
                        tx(2, "buy", 10, 200, day=(2026, 2, 1)),
                        tx(3, "sell", 15, 150, day=(2026, 3, 1))])
        sale = state.sales[0]
        self.assertAlmostEqual(sale.cost, 10 * 100 + 5 * 200)
        self.assertAlmostEqual(sale.gain, 15 * 150 - 2000)
        self.assertAlmostEqual(state.cost_total, 5 * 200)

    def test_oversell_is_rejected(self):
        with self.assertRaises(ValueError):
            replay([tx(1, "buy", 10, 100), tx(2, "sell", 11, 100, day=(2026, 2, 1))])

    def test_sell_before_buy_is_rejected(self):
        with self.assertRaises(ValueError):
            replay([tx(1, "buy", 10, 100, day=(2026, 3, 1)), tx(2, "sell", 5, 100, day=(2026, 2, 1))])

    def test_same_day_buy_counts_before_sell(self):
        state = replay([tx(2, "sell", 5, 110), tx(1, "buy", 5, 100)])
        self.assertAlmostEqual(state.realized, 50)

    def test_later_sale_does_not_change_earlier_sale(self):
        first = replay([tx(1, "buy", 10, 100), tx(2, "sell", 4, 120, day=(2026, 2, 1))])
        later = replay([tx(1, "buy", 10, 100), tx(2, "sell", 4, 120, day=(2026, 2, 1)),
                        tx(3, "sell", 6, 90, day=(2026, 3, 1))])
        self.assertEqual(first.sales[0], later.sales[0])

    def test_invalid_numbers_are_rejected(self):
        for bad in (tx(1, "buy", 0, 100), tx(1, "buy", 1, 0), tx(1, "buy", 1, 100, fee=-1)):
            with self.assertRaises(ValueError):
                replay([bad])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.json = os.path.join(self.dir.name, "watchlist.json")
        self.store = Store(os.path.join(self.dir.name, "test.db"))
        self.addCleanup(self.store.close)  # läuft vor dem Löschen des Ordners

    def test_legacy_import_turns_positions_into_opening_purchases(self):
        with open(self.json, "w", encoding="utf-8") as f:
            json.dump({"symbols": ["AAA", "BBB"],
                       "positions": {"AAA": {"shares": 7.36, "cost": 547.62, "realized": 12.5}}}, f)
        today = dt.date(2026, 10, 8)
        self.store.import_legacy(self.json, today)
        self.assertEqual(self.store.symbols(), ["AAA", "BBB"])
        [opening] = self.store.transactions()
        self.assertEqual((opening.symbol, opening.kind, opening.shares, opening.price, opening.day),
                         ("AAA", "buy", 7.36, 547.62, today))
        self.assertEqual(self.store.opening_realized()["AAA"], 12.5)
        self.assertTrue(os.path.exists(self.json + ".bak"))

    def test_legacy_import_reads_old_list_format_and_runs_once(self):
        with open(self.json, "w", encoding="utf-8") as f:
            json.dump(["AAA", "BBB"], f)
        self.store.import_legacy(self.json)
        self.store.remove_symbol("AAA")
        self.store.import_legacy(self.json)  # darf nichts erneut anlegen
        self.assertEqual(self.store.symbols(), ["BBB"])

    def test_fresh_install_gets_default_symbols(self):
        self.store.import_legacy(self.json)
        self.assertEqual(self.store.symbols(), ["AAPL", "MSFT", "DELL"])

    def test_removing_symbol_keeps_transactions(self):
        self.store.add_symbol("AAA")
        self.store.add_transaction("AAA", "buy", 1, 10, 0, dt.date(2026, 1, 1))
        self.store.remove_symbol("AAA")
        self.assertEqual(len(self.store.transactions()), 1)

    def test_database_rejects_invalid_rows(self):
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_transaction("AAA", "buy", -1, 10, 0, dt.date(2026, 1, 1))


class SnapshotAndInstrumentTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "test.db")
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    QUOTE = {"price": 123.45, "change_pct": -1.5, "currency": "EUR", "source": "Yahoo Finance"}
    INFO = {"name": "Siemens Energy AG", "exchange": "XETRA", "currency": "EUR", "sector": "Industrials",
            "industry": "Machinery", "country": "Germany", "isin": "", "source": "Yahoo Finance"}

    def test_snapshot_roundtrip_keeps_price_currency_source_and_time(self):
        moment = dt.datetime(2026, 10, 8, 14, 3, 12)
        self.store.save_snapshot("ENR.DE", self.QUOTE, moment)
        self.assertEqual(self.store.snapshots()["ENR.DE"],
                         {"price": 123.45, "change_pct": -1.5, "currency": "EUR",
                          "source": "Yahoo Finance", "fetched_at": moment})

    def test_new_snapshot_replaces_the_old_one(self):
        self.store.save_snapshot("ENR.DE", self.QUOTE, dt.datetime(2026, 10, 8, 14, 0, 0))
        self.store.save_snapshot("ENR.DE", {**self.QUOTE, "price": 130.0}, dt.datetime(2026, 10, 8, 14, 5, 0))
        snapshots = self.store.snapshots()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots["ENR.DE"]["price"], 130.0)

    def test_instrument_roundtrip(self):
        moment = dt.datetime(2026, 10, 8, 14, 3, 12)
        self.store.save_instrument("ENR.DE", self.INFO, moment)
        self.assertEqual(self.store.instruments()["ENR.DE"], {**self.INFO, "fetched_at": moment})

    def test_instrument_with_missing_fields_stores_empty_strings(self):
        self.store.save_instrument("SPY", {"name": "ETF", "sector": None, "source": "Yahoo Finance"},
                                   dt.datetime(2026, 1, 1))
        stored = self.store.instruments()["SPY"]
        self.assertEqual((stored["name"], stored["sector"], stored["country"], stored["isin"]), ("ETF", "", "", ""))

    def test_data_survives_reopening_the_database(self):
        self.store.save_snapshot("ENR.DE", self.QUOTE, dt.datetime(2026, 10, 8, 14, 3, 12))
        self.store.close()
        reopened = Store(self.path)
        self.addCleanup(reopened.close)
        self.assertIn("ENR.DE", reopened.snapshots())

    def test_database_from_schema_version_1_is_upgraded_without_losing_data(self):
        import sqlite3
        old_path = os.path.join(self.dir.name, "old.db")
        old = sqlite3.connect(old_path)
        old.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE watchlist (symbol TEXT PRIMARY KEY, sort INTEGER NOT NULL,
                opening_realized REAL NOT NULL DEFAULT 0);
            CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('buy', 'sell')), shares REAL NOT NULL, price REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0, executed_on TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL);
            INSERT INTO meta VALUES ('schema_version', '1'), ('legacy_imported', '2026-10-08');
            INSERT INTO watchlist VALUES ('GEV', 1, 5.0);
            INSERT INTO transactions (symbol, kind, shares, price, fee, executed_on, note, created_at)
                VALUES ('GEV', 'buy', 7.36, 547.62, 0, '2026-10-08', 'Startbestand', '2026-10-08T22:00:00');
        """)
        old.commit()
        old.close()
        upgraded = Store(old_path)
        self.addCleanup(upgraded.close)
        self.assertEqual(upgraded.meta("schema_version"), "4")
        self.assertEqual(upgraded.symbols(), ["GEV"])
        self.assertEqual(upgraded.opening_realized(), {"GEV": 5.0})
        self.assertEqual(upgraded.transactions()[0].shares, 7.36)
        self.assertEqual(upgraded.snapshots(), {})  # neue Tabellen sind da und leer
        upgraded.save_snapshot("GEV", self.QUOTE, dt.datetime(2026, 10, 8, 23, 0, 0))
        self.assertIn("GEV", upgraded.snapshots())


class FxStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "test.db")
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_daily_rates_roundtrip_per_currency(self):
        self.store.save_fx_rates("USD", {dt.date(2026, 1, 5): 0.9, dt.date(2026, 1, 6): 0.91}, "Yahoo Finance")
        self.store.save_fx_rates("CHF", {dt.date(2026, 1, 5): 1.07}, "Yahoo Finance")
        self.assertEqual(self.store.fx_rates(),
                         {"USD": {dt.date(2026, 1, 5): 0.9, dt.date(2026, 1, 6): 0.91},
                          "CHF": {dt.date(2026, 1, 5): 1.07}})

    def test_saving_a_day_again_replaces_its_rate(self):
        self.store.save_fx_rates("USD", {dt.date(2026, 1, 5): 0.9}, "Yahoo Finance")
        self.store.save_fx_rates("USD", {dt.date(2026, 1, 5): 0.95, dt.date(2026, 1, 6): 0.96}, "Yahoo Finance")
        self.assertEqual(self.store.fx_rates()["USD"], {dt.date(2026, 1, 5): 0.95, dt.date(2026, 1, 6): 0.96})

    def test_latest_rate_keeps_source_and_time(self):
        moment = dt.datetime(2026, 10, 9, 12, 30, 5)
        self.store.save_fx_latest("USD", 0.8916, moment, "Yahoo Finance")
        self.store.save_fx_latest("USD", 0.8920, moment + dt.timedelta(minutes=1), "Yahoo Finance")
        self.assertEqual(self.store.fx_latest(),
                         {"USD": {"rate": 0.8920, "source": "Yahoo Finance", "fetched_at": moment + dt.timedelta(minutes=1)}})

    def test_rates_survive_reopening(self):
        self.store.save_fx_rates("USD", {dt.date(2026, 1, 5): 0.9}, "Yahoo Finance")
        self.store.close()
        reopened = Store(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.fx_rates()["USD"][dt.date(2026, 1, 5)], 0.9)

    def test_empty_database_has_no_rates(self):
        self.assertEqual((self.store.fx_rates(), self.store.fx_latest()), ({}, {}))


class SourceStoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "test.db")
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def add(self, source="manual", external_id=None, symbol="AAA", day=dt.date(2026, 1, 5)):
        return self.store.add_transaction(symbol, "buy", 1, 10, 0, day, "", source, external_id)

    def test_new_transactions_default_to_manual_without_external_id(self):
        self.store.add_transaction("AAA", "buy", 1, 10, 0, dt.date(2026, 1, 5))
        [t] = self.store.transactions()
        self.assertEqual((t.source, t.external_id), ("manual", ""))

    def test_source_and_external_id_roundtrip(self):
        self.add("etoro", "ET-1")
        [t] = self.store.transactions()
        self.assertEqual((t.source, t.external_id), ("etoro", "ET-1"))

    def test_the_same_external_id_cannot_be_booked_twice(self):
        import sqlite3
        self.add("etoro", "ET-1")
        with self.assertRaises(sqlite3.IntegrityError):
            self.add("etoro", "ET-1", day=dt.date(2026, 1, 6))

    def test_the_same_id_from_another_source_and_many_manual_rows_are_fine(self):
        self.add("etoro", "1")
        self.add("trading212", "1")
        self.add()
        self.add()
        self.assertEqual(len(self.store.transactions()), 4)

    def test_known_external_ids_of_one_source(self):
        self.add("etoro", "1")
        self.add("etoro", "2")
        self.add("trading212", "3")
        self.add()
        self.assertEqual(self.store.external_ids("etoro"), {"1", "2"})

    def test_delete_several_transactions_at_once(self):
        ids = [self.add(day=dt.date(2026, 1, d)) for d in (5, 6, 7)]
        self.store.delete_transactions(ids[:2])
        self.assertEqual([t.id for t in self.store.transactions()], [ids[2]])

    def test_legacy_import_marks_opening_balances(self):
        path = os.path.join(self.dir.name, "watchlist.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"symbols": ["GEV"], "positions": {"GEV": {"shares": 7.36, "cost": 547.62, "realized": 0}}}, f)
        self.store.import_legacy(path)
        self.assertEqual(self.store.transactions()[0].source, "opening")

    def test_daily_closes_roundtrip_and_replace(self):
        self.store.save_closes("AAA", {dt.date(2026, 1, 5): 100.0, dt.date(2026, 1, 6): 101.0}, "Yahoo Finance")
        self.store.save_closes("AAA", {dt.date(2026, 1, 6): 102.0}, "Yahoo Finance")
        self.store.save_closes("BBB", {dt.date(2026, 1, 5): 5.0}, "Yahoo Finance")
        self.assertEqual(self.store.closes(), {"AAA": {dt.date(2026, 1, 5): 100.0, dt.date(2026, 1, 6): 102.0},
                                               "BBB": {dt.date(2026, 1, 5): 5.0}})

    def test_aliases_are_kept_per_source(self):
        self.store.set_alias("etoro", "AAPL.US", "AAPL")
        self.store.set_alias("etoro", "AAPL.US", "AAPL2")
        self.store.set_alias("trading212", "AAPL.US", "AAPL")
        self.assertEqual(self.store.aliases("etoro"), {"AAPL.US": "AAPL2"})
        self.assertEqual(self.store.aliases("other"), {})

    def test_sync_state_roundtrip_and_replace(self):
        self.assertIsNone(self.store.sync_state("etoro"))
        self.store.save_sync_state("etoro", "c1", dt.datetime(2026, 10, 9, 12, 0, 0), "3 neu")
        self.store.save_sync_state("etoro", "c2", dt.datetime(2026, 10, 9, 13, 0, 0), "0 neu")
        self.assertEqual(self.store.sync_state("etoro"),
                         {"cursor": "c2", "last_sync_at": dt.datetime(2026, 10, 9, 13, 0, 0), "message": "0 neu"})

    def test_database_from_schema_version_3_is_upgraded_and_opening_rows_are_recognised(self):
        import sqlite3
        old_path = os.path.join(self.dir.name, "v3.db")
        old = sqlite3.connect(old_path)
        old.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE watchlist (symbol TEXT PRIMARY KEY, sort INTEGER NOT NULL,
                opening_realized REAL NOT NULL DEFAULT 0);
            CREATE TABLE transactions (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT NOT NULL,
                kind TEXT NOT NULL CHECK (kind IN ('buy', 'sell')), shares REAL NOT NULL, price REAL NOT NULL,
                fee REAL NOT NULL DEFAULT 0, executed_on TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL);
            INSERT INTO meta VALUES ('schema_version', '3'), ('legacy_imported', '2026-10-08');
            INSERT INTO watchlist VALUES ('GEV', 1, 0);
            INSERT INTO transactions (symbol, kind, shares, price, fee, executed_on, note, created_at)
                VALUES ('GEV', 'buy', 7.36, 547.62, 0, '2026-10-08', 'Startbestand (aus dem Widget)', '2026-10-08T22:00:00'),
                       ('GEV', 'buy', 1, 900, 0, '2026-10-08', 'Nachkauf', '2026-10-08T23:00:00');
        """)
        old.commit()
        old.close()
        upgraded = Store(old_path)
        self.addCleanup(upgraded.close)
        self.assertEqual(upgraded.meta("schema_version"), "4")
        first, second = upgraded.transactions()
        self.assertEqual((first.source, second.source), ("opening", "manual"))
        self.assertEqual((first.shares, second.price), (7.36, 900))
        upgraded.add_transaction("GEV", "buy", 1, 10, 0, dt.date(2026, 1, 5), "", "etoro", "X")  # neue Spalten benutzbar
        self.assertEqual(upgraded.external_ids("etoro"), {"X"})

    def test_upgrading_twice_changes_nothing(self):
        self.add("etoro", "1")
        self.store.close()
        again = Store(self.path)
        self.addCleanup(again.close)
        self.assertEqual([(t.source, t.external_id) for t in again.transactions()], [("etoro", "1")])


if __name__ == "__main__":
    unittest.main()
