"""Kurse, Termine, News und Eingabe-Hilfen aus stock_data, mit gefälschtem yfinance."""
import datetime as dt
import unittest
from unittest import mock

import stock_data as sd


class FakeInfo(dict):
    pass


class FakeTicker:
    def __init__(self, symbol, info=None, calendar=None, news=None):
        self.fast_info = info if info is not None else FakeInfo(last_price=110.0, previous_close=100.0, currency="USD")
        self.calendar = calendar
        self.news = news or []


def patch_yf(**ticker_kwargs):
    fake = mock.Mock()
    fake.Ticker = lambda symbol: FakeTicker(symbol, **ticker_kwargs)
    fake.Search = mock.Mock(side_effect=RuntimeError("keine Suche"))
    return mock.patch.object(sd, "yf", fake), fake


class InputTests(unittest.TestCase):
    def test_parse_number_accepts_comma_and_dot(self):
        self.assertEqual(sd.parse_number("12,5"), 12.5)
        self.assertEqual(sd.parse_number(" 3.25 "), 3.25)

    def test_parse_number_rejects_text_with_readable_message(self):
        with self.assertRaisesRegex(ValueError, "Zahl"):
            sd.parse_number("abc")

    def test_parse_date_formats(self):
        self.assertEqual(sd.parse_date("08.10.2026"), dt.date(2026, 10, 8))
        self.assertEqual(sd.parse_date("08.10.26"), dt.date(2026, 10, 8))
        self.assertEqual(sd.parse_date("2026-10-08"), dt.date(2026, 10, 8))

    def test_parse_date_rejects_garbage_and_future(self):
        with self.assertRaises(ValueError):
            sd.parse_date("gestern")
        future = (dt.date.today() + dt.timedelta(days=3)).strftime("%d.%m.%Y")
        with self.assertRaisesRegex(ValueError, "Zukunft"):
            sd.parse_date(future)

    def test_profit_and_loss_of_a_position(self):
        position = {"shares": 10, "cost": 100.0}
        self.assertAlmostEqual(sd.pl_percent(position, 125.0), 25.0)
        self.assertAlmostEqual(sd.pl_amount(position, 125.0), 250.0)
        self.assertAlmostEqual(sd.pl_percent(position, 80.0), -20.0)


class SymbolLookupTests(unittest.TestCase):
    def test_foreign_suffixes_are_translated_to_yahoo(self):
        self.assertEqual(sd.symbol_variants("ORA.US"), ["ORA.US", "ORA"])
        self.assertEqual(sd.symbol_variants("abbn.zu"), ["ABBN.ZU", "ABBN.SW"])
        self.assertEqual(sd.symbol_variants("VOD.UK"), ["VOD.UK", "VOD.L"])
        self.assertEqual(sd.symbol_variants("DRO.AU"), ["DRO.AU", "DRO.AX"])

    def test_yahoo_symbols_and_plain_names_are_left_alone(self):
        for text in ("DRO.AX", "ABBN.SW", "SAP.DE", "AAPL", "BRK-B", "droneshield"):
            self.assertEqual(sd.symbol_variants(text), [text.upper()])

    def test_whitespace_is_ignored(self):
        self.assertEqual(sd.symbol_variants("  ora.us "), ["ORA.US", "ORA"])

    def test_search_returns_stocks_and_etfs_only(self):
        quotes = [
            {"symbol": "DRO.AX", "longname": "DroneShield Limited", "shortname": "DRONE FPO [DRO]",
             "exchDisp": "Australian", "quoteType": "EQUITY"},
            {"symbol": "DRSHF", "shortname": "Droneshield Ltd", "exchange": "PNK", "quoteType": "EQUITY"},
            {"symbol": "XYZ=X", "shortname": "Währung", "quoteType": "CURRENCY"},
            {"symbol": "ETFA", "shortname": "Ein ETF", "exchDisp": "NYSE", "quoteType": "ETF"},
            {"shortname": "ohne Symbol", "quoteType": "EQUITY"},
        ]
        fake = mock.Mock()
        fake.Search = lambda query, **kw: mock.Mock(quotes=quotes)
        with mock.patch.object(sd, "yf", fake):
            found = sd.search_symbols("droneshield")
        self.assertEqual([f["symbol"] for f in found], ["DRO.AX", "DRSHF", "ETFA"])
        self.assertEqual(found[0], {"symbol": "DRO.AX", "name": "DroneShield Limited",
                                    "exchange": "Australian", "type": "EQUITY"})
        self.assertEqual(found[1]["exchange"], "PNK")  # ohne Anzeigenamen der Börse


class QuoteTests(unittest.TestCase):
    def test_quote_with_daily_change(self):
        patcher, _ = patch_yf()
        with patcher:
            quote = sd.fetch_quote("X")
        self.assertAlmostEqual(quote["price"], 110.0)
        self.assertAlmostEqual(quote["change_pct"], 10.0)
        self.assertEqual(quote["currency"], "USD")

    def test_missing_price_is_an_error_not_a_zero(self):
        patcher, _ = patch_yf(info=FakeInfo(last_price=None, previous_close=100.0, currency="USD"))
        with patcher, self.assertRaises(ValueError):
            sd.fetch_quote("X")

    def test_missing_previous_close_is_an_error(self):
        patcher, _ = patch_yf(info=FakeInfo(last_price=10.0, previous_close=0, currency="USD"))
        with patcher, self.assertRaises(ValueError):
            sd.fetch_quote("X")


class EventTests(unittest.TestCase):
    def days(self, n):
        return dt.date.today() + dt.timedelta(days=n)

    def test_future_events_sorted_and_past_ones_dropped(self):
        calendar = {"Earnings Date": [self.days(30)], "Ex-Dividend Date": self.days(5),
                    "Dividend Date": self.days(-3)}
        patcher, _ = patch_yf(calendar=calendar)
        with patcher:
            events = sd.fetch_events("X")
        self.assertEqual(events, [(self.days(5), "Ex-Dividende"), (self.days(30), "Quartalszahlen")])

    def test_empty_calendar_gives_no_events(self):
        patcher, _ = patch_yf(calendar={})
        with patcher:
            self.assertEqual(sd.fetch_events("X"), [])

    def test_event_today_counts_as_upcoming(self):
        patcher, _ = patch_yf(calendar={"Earnings Date": [self.days(0)]})
        with patcher:
            self.assertEqual(len(sd.fetch_events("X")), 1)

    def test_event_formats(self):
        event = (self.days(12), "Ex-Dividende")
        self.assertIn("in 12 T", sd.format_event(event))
        self.assertTrue(sd.format_event(event, short=True).startswith("Ex-Div."))
        self.assertTrue(sd.format_event(event, short=True).endswith("12 T"))
        self.assertIn("heute", sd.format_event((self.days(0), "Quartalszahlen")))

    def test_describe_event_uses_singular_and_today(self):
        self.assertIn("in 1 Tag", sd.describe_event((self.days(1), "Quartalszahlen"))[1])
        self.assertNotIn("Tagen", sd.describe_event((self.days(1), "Quartalszahlen"))[1])
        self.assertIn("in 5 Tagen", sd.describe_event((self.days(5), "Quartalszahlen"))[1])
        self.assertIn("heute", sd.describe_event((self.days(0), "Quartalszahlen"))[1])
        self.assertEqual(sd.describe_event((self.days(2), "Ex-Dividende"))[0], "Ex-Dividende")


class NewsTests(unittest.TestCase):
    NEW_FORMAT = {"content": {"title": "Neu", "canonicalUrl": {"url": "https://x/neu"},
                              "provider": {"displayName": "Quelle A"}, "pubDate": "2026-10-08T10:00:00Z"}}
    OLD_FORMAT = {"title": "Alt", "link": "https://x/alt", "publisher": "Quelle B",
                  "providerPublishTime": 1_790_000_000}

    def search_returning(self, items):
        fake = mock.Mock()
        fake.Search = lambda symbol, **kw: mock.Mock(news=items)
        fake.Ticker = lambda symbol: FakeTicker(symbol)
        return mock.patch.object(sd, "yf", fake)

    def test_both_formats_are_read_and_sorted_newest_first(self):
        with self.search_returning([self.OLD_FORMAT, self.NEW_FORMAT]):
            news = sd.fetch_news("X")
        self.assertEqual([n[0] for n in news], ["Neu", "Alt"])
        self.assertEqual(news[0][1], "Quelle A")
        self.assertEqual(news[1][3], "https://x/alt")

    def test_items_without_title_or_link_are_dropped(self):
        broken = [{"title": "Ohne Link"}, {"link": "https://x"}, self.OLD_FORMAT]
        with self.search_returning(broken):
            self.assertEqual([n[0] for n in sd.fetch_news("X")], ["Alt"])

    def test_falls_back_to_ticker_news_when_search_is_empty(self):
        fake = mock.Mock()
        fake.Search = lambda symbol, **kw: mock.Mock(news=[])
        fake.Ticker = lambda symbol: FakeTicker(symbol, news=[self.OLD_FORMAT])
        with mock.patch.object(sd, "yf", fake):
            self.assertEqual([n[0] for n in sd.fetch_news("X")], ["Alt"])

    def test_falls_back_when_search_raises(self):
        patcher, fake = patch_yf(news=[self.OLD_FORMAT])
        with patcher:
            self.assertEqual([n[0] for n in sd.fetch_news("X")], ["Alt"])


if __name__ == "__main__":
    unittest.main()
