"""Kurse, Termine, News und Eingabe-Hilfen aus stock_data, mit gefälschtem yfinance."""
import datetime as dt
import unittest
from unittest import mock

import stock_data as sd


class FakeInfo(dict):
    pass


class FakeTicker:
    created = []  # (Symbol, history-Aufrufe) der erzeugten Ticker, damit Tests die Abfragen prüfen können

    def __init__(self, symbol, info=None, calendar=None, news=None, details=None, isin="-", frame=None, metadata=None):
        self.symbol, self.frame, self.history_calls = symbol, frame, []
        FakeTicker.created.append(self)
        self.fast_info = info if info is not None else FakeInfo(last_price=110.0, previous_close=100.0, currency="USD")
        self.calendar = calendar
        self.metadata = metadata
        self.news = news or []
        self.info = details if details is not None else {}
        self._isin = isin

    def get_history_metadata(self):
        if self.metadata is None:
            raise RuntimeError("keine Metadaten")
        return self.metadata

    def history(self, **kwargs):
        self.history_calls.append(kwargs)
        return self.frame

    @property
    def isin(self):
        if isinstance(self._isin, Exception):
            raise self._isin
        return self._isin


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


class FxFetchTests(unittest.TestCase):
    def setUp(self):
        FakeTicker.created = []

    def test_pair_name_is_foreign_currency_followed_by_base(self):
        self.assertEqual(sd.fx_pair("USD", "EUR"), "USDEUR=X")

    def test_current_rate(self):
        patcher, _ = patch_yf(info=FakeInfo(last_price=0.8916))
        with patcher:
            self.assertAlmostEqual(sd.fetch_fx_rate("USD", "EUR"), 0.8916)
        self.assertEqual(FakeTicker.created[0].symbol, "USDEUR=X")

    def test_missing_or_nonpositive_rate_is_an_error(self):
        for bad in (None, 0, -1.0):
            patcher, _ = patch_yf(info=FakeInfo(last_price=bad))
            with patcher, self.assertRaises(ValueError):
                sd.fetch_fx_rate("USD", "EUR")

    def test_history_becomes_a_day_to_rate_mapping(self):
        import pandas
        frame = pandas.DataFrame({"Close": [0.90, 0.91]}, index=pandas.to_datetime(["2026-01-05", "2026-01-06"]))
        patcher, _ = patch_yf(frame=frame)
        with patcher:
            rates = sd.fetch_fx_history("USD", "EUR", dt.date(2026, 1, 1))
        self.assertEqual(rates, {dt.date(2026, 1, 5): 0.90, dt.date(2026, 1, 6): 0.91})
        ticker = FakeTicker.created[0]
        self.assertEqual(ticker.symbol, "USDEUR=X")
        self.assertEqual(ticker.history_calls, [{"start": "2026-01-01", "interval": "1d"}])

    def test_history_skips_gaps_and_zero_closes(self):
        import pandas
        frame = pandas.DataFrame({"Close": [0.90, float("nan"), 0.0, 0.92]},
                                 index=pandas.to_datetime(["2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08"]))
        patcher, _ = patch_yf(frame=frame)
        with patcher:
            rates = sd.fetch_fx_history("USD", "EUR", dt.date(2026, 1, 1))
        self.assertEqual(rates, {dt.date(2026, 1, 5): 0.90, dt.date(2026, 1, 8): 0.92})

    def test_empty_history_is_an_error(self):
        import pandas
        patcher, _ = patch_yf(frame=pandas.DataFrame({"Close": []}))
        with patcher, self.assertRaises(ValueError):
            sd.fetch_fx_history("USD", "EUR", dt.date(2026, 1, 1))


class QuoteTests(unittest.TestCase):
    def test_quote_with_daily_change(self):
        patcher, _ = patch_yf()
        with patcher:
            quote = sd.fetch_quote("X")
        self.assertAlmostEqual(quote["price"], 110.0)
        self.assertAlmostEqual(quote["change_pct"], 10.0)
        self.assertEqual(quote["currency"], "USD")

    @staticmethod
    def meta(start, last, tz="America/New_York"):
        return {"currentTradingPeriod": {"regular": {"start": start}}, "regularMarketTime": last,
                "exchangeTimezoneName": tz}

    def test_change_is_measured_against_the_regular_close_not_the_after_hours_price(self):
        """Regression GEV: fast_info["previous_close"] enthält die Nachbörse des Vortags (998,80), der offizielle
        Schlusskurs war 997,09."""
        info = FakeInfo(last_price=1000.0, previous_close=998.8, regular_market_previous_close=990.0, currency="USD")
        patcher, _ = patch_yf(info=info)
        with patcher:
            self.assertAlmostEqual(sd.fetch_quote("X")["change_pct"], (1000 / 990 - 1) * 100)

    def test_without_the_regular_close_the_old_previous_close_is_the_fallback(self):
        info = FakeInfo(last_price=110.0, previous_close=100.0, regular_market_previous_close=None, currency="USD")
        patcher, _ = patch_yf(info=info)
        with patcher:
            self.assertAlmostEqual(sd.fetch_quote("X")["change_pct"], 10.0)

    def test_before_the_session_starts_there_is_no_change_yet(self):
        # Freitag 9.10.2026, 10:00 New York; Sitzung beginnt 13:30 UTC, letzter Handel gestern
        now = dt.datetime(2026, 10, 9, 14, 0, tzinfo=dt.timezone.utc).timestamp()
        start = dt.datetime(2026, 10, 9, 13, 30, tzinfo=dt.timezone.utc).timestamp()
        last = start - 15 * 3600
        info = FakeInfo(last_price=999.35, previous_close=998.8, regular_market_previous_close=997.09, currency="USD")
        patcher, _ = patch_yf(info=info, metadata=self.meta(start, last))
        with patcher, mock.patch.object(sd.time, "time", lambda: start - 3600):
            self.assertEqual(sd.fetch_quote("X")["change_pct"], 0.0)
        with patcher, mock.patch.object(sd.time, "time", lambda: now):  # Sitzung läuft schon
            self.assertAlmostEqual(sd.fetch_quote("X")["change_pct"], (999.35 / 997.09 - 1) * 100)

    def test_pandas_timestamps_in_the_metadata_work_like_epoch_seconds(self):
        """yfinance 1.7 liefert Timestamps statt Zahlen."""
        import pandas as pd
        start = pd.Timestamp("2026-10-09 09:30", tz="America/New_York")
        last = pd.Timestamp("2026-10-08 16:00", tz="America/New_York")
        info = FakeInfo(last_price=999.35, previous_close=998.8, regular_market_previous_close=997.09, currency="USD")
        patcher, _ = patch_yf(info=info, metadata=self.meta(start, last))
        with patcher, mock.patch.object(sd.time, "time", lambda: start.timestamp() - 7200):
            self.assertEqual(sd.fetch_quote("X")["change_pct"], 0.0)

    def test_on_the_weekend_the_change_of_the_last_session_stays(self):
        saturday = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc).timestamp()
        start = saturday + 3600 * 25  # die Metadaten nennen schon den nächsten Handelstag
        info = FakeInfo(last_price=110.0, previous_close=100.0, regular_market_previous_close=100.0, currency="USD")
        patcher, _ = patch_yf(info=info, metadata=self.meta(start, saturday - 40 * 3600))
        with patcher, mock.patch.object(sd.time, "time", lambda: saturday):
            self.assertAlmostEqual(sd.fetch_quote("X")["change_pct"], 10.0)

    def test_quote_reports_the_market_state_of_its_listing(self):
        for yahoo, ours in (("REGULAR", "open"), ("PRE", "extended"), ("POST", "extended"),
                            ("POSTPOST", "closed"), ("CLOSED", "closed")):
            patcher, _ = patch_yf(details={"marketState": yahoo})
            with patcher:
                self.assertEqual(sd.fetch_quote("X")["market_state"], ours, yahoo)

    def test_unknown_or_missing_market_state_is_none_and_never_breaks_the_quote(self):
        for details in ({}, {"marketState": "SOMETHING_NEW"}):
            patcher, _ = patch_yf(details=details)
            with patcher:
                quote = sd.fetch_quote("X")
            self.assertIsNone(quote["market_state"])
            self.assertAlmostEqual(quote["price"], 110.0)

    def test_quote_names_its_source(self):
        patcher, _ = patch_yf()
        with patcher:
            self.assertEqual(sd.fetch_quote("X")["source"], "Yahoo Finance")

    def test_missing_price_is_an_error_not_a_zero(self):
        patcher, _ = patch_yf(info=FakeInfo(last_price=None, previous_close=100.0, currency="USD"))
        with patcher, self.assertRaises(ValueError):
            sd.fetch_quote("X")

    def test_missing_previous_close_is_an_error(self):
        patcher, _ = patch_yf(info=FakeInfo(last_price=10.0, previous_close=0, currency="USD"))
        with patcher, self.assertRaises(ValueError):
            sd.fetch_quote("X")


class InstrumentTests(unittest.TestCase):
    DETAILS = {"longName": "Siemens Energy AG", "shortName": "SIEMENS ENERGY", "fullExchangeName": "XETRA",
               "exchange": "GER", "currency": "EUR", "sector": "Industrials",
               "industry": "Specialty Industrial Machinery", "country": "Germany"}

    def test_master_data_is_read_from_yahoo_info(self):
        patcher, _ = patch_yf(details=self.DETAILS)
        with patcher:
            info = sd.fetch_instrument("ENR.DE")
        self.assertEqual(info, {"name": "Siemens Energy AG", "exchange": "XETRA", "currency": "EUR",
                                "sector": "Industrials", "industry": "Specialty Industrial Machinery",
                                "country": "Germany", "isin": "", "source": "Yahoo Finance"})

    def test_short_name_and_plain_exchange_are_fallbacks(self):
        details = {"shortName": "ABB", "exchange": "EBS", "currency": "CHF"}
        patcher, _ = patch_yf(details=details)
        with patcher:
            info = sd.fetch_instrument("ABBN.SW")
        self.assertEqual((info["name"], info["exchange"], info["sector"], info["country"]), ("ABB", "EBS", "", ""))

    def test_dash_isin_from_yahoo_means_unknown(self):
        patcher, _ = patch_yf(details=self.DETAILS, isin="-")
        with patcher:
            self.assertEqual(sd.fetch_instrument("ENR.DE")["isin"], "")

    def test_real_isin_is_kept(self):
        patcher, _ = patch_yf(details=self.DETAILS, isin="US78462F1030")
        with patcher:
            self.assertEqual(sd.fetch_instrument("SPY")["isin"], "US78462F1030")

    def test_failing_isin_lookup_does_not_break_master_data(self):
        patcher, _ = patch_yf(details=self.DETAILS, isin=RuntimeError("offline"))
        with patcher:
            info = sd.fetch_instrument("ENR.DE")
        self.assertEqual((info["name"], info["isin"]), ("Siemens Energy AG", ""))

    def test_unknown_symbol_without_a_name_is_an_error(self):
        patcher, _ = patch_yf(details={})
        with patcher, self.assertRaisesRegex(ValueError, "Stammdaten"):
            sd.fetch_instrument("NIX")


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
