"""Tests der eToro-Anbindung ohne Netzwerk: Antworten kommen aus einem Ersatz für den HTTP-Aufruf."""
import datetime as dt
import json
import os
import tempfile
import unittest

import etoro
from etoro import EtoroClient, EtoroError, EtoroSource

TODAY = dt.date(2026, 10, 9)
INSTRUMENTS = {"instrumentDisplayDatas": [
    {"instrumentID": 1001, "symbolFull": "AAPL", "instrumentDisplayName": "Apple"},
    {"instrumentID": 1002, "symbolFull": "MSFT", "instrumentDisplayName": "Microsoft"}]}


def position(id, instrument=1001, units=2.0, rate=100.0, opened="2026-03-02T10:00:00Z", **extra):
    return {"positionID": id, "instrumentID": instrument, "isBuy": True, "leverage": 1, "units": units,
            "openRate": rate, "openDateTime": opened, "mirrorID": 0, **extra}


def closed(id, instrument=1001, units=3.0, open_rate=50.0, close_rate=60.0, fees=0.5, **extra):
    return {"positionId": id, "instrumentId": instrument, "isBuy": True, "leverage": 1, "units": units,
            "openRate": open_rate, "closeRate": close_rate, "fees": fees,
            "openTimestamp": "2026-01-05T09:00:00Z", "closeTimestamp": "2026-02-10T15:30:00Z", **extra}


class FakeApi:
    """Ersetzt den HTTP-Aufruf; merkt sich die Anfragen."""

    def __init__(self, portfolio=(), history=(), statuses=None, credit=None):
        self.portfolio, self.history, self.statuses = list(portfolio), list(history), list(statuses or [])
        self.credit = credit
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        if self.statuses:
            status, retry = self.statuses.pop(0)
            return status, {"Retry-After": str(retry)} if retry else {}, "{}"
        if "/trading/info/portfolio" in url:
            body = {"clientPortfolio": {"positions": self.portfolio,
                                        **({"credit": self.credit} if self.credit is not None else {})}}
        elif "/trade/history" in url:
            page = int(url.split("page=")[1].split("&")[0])
            body = self.history[(page - 1) * etoro.PAGE_SIZE: page * etoro.PAGE_SIZE]
        elif "/market-data/instruments" in url:
            body = INSTRUMENTS
        else:
            return 404, {}, "nicht gefunden"
        return 200, {}, json.dumps(body)


def source(api):
    return EtoroSource(EtoroClient("pub", "usr", opener=api, sleep=lambda s: None), today=lambda: TODAY)


class ClientTests(unittest.TestCase):
    def test_sends_both_keys_and_a_request_id_and_only_reads(self):
        api = FakeApi()
        EtoroClient("pub", "usr", opener=api).portfolio_positions()
        url, headers = api.calls[0]
        self.assertEqual((headers["x-api-key"], headers["x-user-key"]), ("pub", "usr"))
        self.assertTrue(headers["x-request-id"])
        self.assertEqual(headers["User-Agent"], etoro.USER_AGENT)
        self.assertTrue(url.startswith(etoro.BASE_URL + "/trading/info/portfolio"))

    def test_rejected_keys_give_a_clear_message(self):
        client = EtoroClient("pub", "bad", opener=FakeApi(statuses=[(401, 0)]))
        with self.assertRaisesRegex(EtoroError, "lehnt die Schlüssel ab"):
            client.portfolio_positions()

    def test_rate_limit_waits_and_retries(self):
        waits = []
        api = FakeApi(statuses=[(429, 3)])
        client = EtoroClient("pub", "usr", opener=api, sleep=waits.append)
        self.assertEqual(client.portfolio_positions(), [])
        self.assertEqual(waits, [3.0])

    def test_gives_up_after_repeated_rate_limits(self):
        client = EtoroClient("pub", "usr", opener=FakeApi(statuses=[(429, 1)] * 10), sleep=lambda s: None)
        with self.assertRaisesRegex(EtoroError, "bremst"):
            client.portfolio_positions()

    def test_history_is_read_page_by_page(self):
        rows = [closed(i) for i in range(etoro.PAGE_SIZE + 5)]
        self.assertEqual(len(EtoroClient("p", "u", opener=FakeApi(history=rows)).history(TODAY)), len(rows))


class MappingTests(unittest.TestCase):
    def fetch(self, portfolio=(), history=(), cursor=""):
        return source(FakeApi(portfolio, history)).fetch(cursor)

    def test_open_position_becomes_a_buy(self):
        [trade] = self.fetch([position(7)]).trades
        self.assertEqual((trade.external_id, trade.symbol, trade.kind, trade.shares, trade.price, trade.day),
                         ("7:open", "AAPL", "buy", 2.0, 100.0, dt.date(2026, 3, 2)))

    def test_closed_position_becomes_buy_and_sell_with_fees_on_the_sale(self):
        buy, sell = self.fetch(history=[closed(9)]).trades
        self.assertEqual((buy.external_id, buy.kind, buy.price, buy.day), ("9:open", "buy", 50.0, dt.date(2026, 1, 5)))
        self.assertEqual((sell.external_id, sell.kind, sell.price, sell.fee, sell.day),
                         ("9:close", "sell", 60.0, 0.5, dt.date(2026, 2, 10)))

    def test_a_position_that_closes_later_keeps_its_open_id(self):
        first = self.fetch([position(7)]).trades
        later = self.fetch(history=[closed(7, units=2.0, open_rate=100.0, close_rate=110.0)]).trades
        self.assertEqual(first[0].external_id, later[0].external_id)

    def test_open_leveraged_short_and_copied_positions_and_closed_shorts_are_skipped_with_a_note(self):
        batch = self.fetch([position(1, leverage=5), position(2, isBuy=False), position(3, mirrorID=55),
                            position(4)], [closed(5, isBuy=False)])
        self.assertEqual([t.external_id for t in batch.trades], ["4:open"])
        self.assertTrue(any("4 Position(en) übersprungen" in n for n in batch.notes))

    def test_closed_leveraged_long_counts_as_an_equivalent_buy_and_sell(self):
        """BYND-CFD mit Hebel 2: 144,3 Stück von 6,93 auf 4,65 = -329 bei 500 eingesetzt."""
        batch = self.fetch(history=[closed(8, units=144.3, open_rate=6.93, close_rate=4.65, fees=0.0, leverage=2)])
        buy, sell = batch.trades
        self.assertAlmostEqual(buy.shares * buy.price, 500.0, places=1)       # eingesetzter Betrag
        self.assertAlmostEqual(sell.shares * sell.price - buy.shares * buy.price, 144.3 * (4.65 - 6.93), places=1)
        self.assertIn("Hebel 2", buy.note)

    def test_unleveraged_trades_are_not_touched_by_the_leverage_conversion(self):
        batch = self.fetch(history=[closed(9, units=3.0, open_rate=50.0, close_rate=60.0)])
        self.assertEqual([(t.shares, t.price) for t in batch.trades], [(3.0, 50.0), (3.0, 60.0)])

    def test_available_cash_is_reported_in_us_dollars_and_unknown_when_missing(self):
        self.assertEqual(source(FakeApi(credit=2568.15)).fetch("").cash, (2568.15, "USD"))
        self.assertIsNone(source(FakeApi()).fetch("").cash)

    def test_unknown_instrument_is_reported(self):
        batch = self.fetch([position(1, instrument=9999)])
        self.assertEqual(batch.trades, ())
        self.assertTrue(any("ohne Kürzel" in n for n in batch.notes))

    def test_first_sync_notes_the_one_year_limit_and_sets_cursor(self):
        batch = self.fetch()
        self.assertEqual(batch.cursor, TODAY.isoformat())
        self.assertTrue(any("nur ab" in n for n in batch.notes))

    def test_follow_up_sync_starts_a_week_before_the_cursor(self):
        api = FakeApi()
        source(api).fetch("2026-10-01")
        history_url = next(url for url, _ in api.calls if "/trade/history" in url)
        self.assertIn("minDate=2026-09-24", history_url)

    def test_first_sync_starts_less_than_a_year_back(self):
        api = FakeApi()
        source(api).fetch("")
        history_url = next(url for url, _ in api.calls if "/trade/history" in url)
        self.assertIn("minDate=" + (TODAY - dt.timedelta(days=etoro.HISTORY_DAYS)).isoformat(), history_url)


class SymbolTests(unittest.TestCase):
    def test_etoro_symbols_become_yahoo_symbols(self):
        for etoro_symbol, yahoo in (("AAPL", "AAPL"), ("ORA.US", "ORA"), ("ABBN.ZU", "ABBN.SW"), ("01211.HK", "1211.HK"),
                                    ("00700.HK", "0700.HK"), ("ENR.DE", "ENR.DE"), ("BA.L", "BA.L"), ("GOLD", "GC=F"),
                                    ("air.fr", "AIR.PA")):
            self.assertEqual(etoro.yahoo_symbol(etoro_symbol), yahoo)


class CredentialsTests(unittest.TestCase):
    def test_roundtrip_and_delete(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(etoro.load_credentials(folder))
            etoro.save_credentials(folder, " pub ", "usr\n")
            self.assertEqual(etoro.load_credentials(folder), ("pub", "usr"))
            etoro.delete_credentials(folder)
            self.assertIsNone(etoro.load_credentials(folder))
            etoro.delete_credentials(folder)  # ohne Datei kein Fehler

    def test_empty_key_is_refused_and_broken_file_ignored(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                etoro.save_credentials(folder, "pub", "  ")
            with open(os.path.join(folder, "etoro.json"), "w") as f:
                f.write("{kaputt")
            self.assertIsNone(etoro.load_credentials(folder))


if __name__ == "__main__":
    unittest.main()
