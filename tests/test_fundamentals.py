"""Kennzahlen (Fundamentaldaten): Auswahl, Plausibilitätsprüfung, Formatierung, Speicher und Controller."""
import datetime as dt
import json
import math
import os
import tempfile
import unittest
from unittest import mock

import fundamentals as fund
import glossary
import stock_data as sd
from store import Store
from tests.support import AppTestCase, wait_until
from tests.test_data import patch_yf

NOW = dt.datetime(2026, 10, 9, 12, 0)
QUARTER = dt.datetime(2026, 6, 27, 12, 0, tzinfo=dt.timezone.utc).timestamp()


def info(**overrides):
    base = {"trailingPE": 30.5, "forwardPE": 27.0, "priceToBook": 45.0, "priceToSalesTrailing12Months": 8.1,
            "enterpriseToEbitda": 22.0, "pegRatio": 2.2, "grossMargins": 0.46, "operatingMargins": 0.31,
            "profitMargins": 0.25, "returnOnEquity": 1.5, "revenueGrowth": 0.08, "earningsGrowth": 0.11,
            "trailingEps": 6.5, "marketCap": 3.0e12, "totalRevenue": 4.0e11, "freeCashflow": 1.0e11,
            "totalCash": 5.0e10, "totalDebt": 1.1e11, "debtToEquity": 150.0, "currentRatio": 0.9,
            "dividendRate": 1.0, "currentPrice": 200.0, "trailingAnnualDividendYield": 0.005, "payoutRatio": 0.15,
            "beta": 1.2, "currency": "USD", "financialCurrency": "USD", "mostRecentQuarter": QUARTER}
    base.update(overrides)
    return base


def parsed(**overrides):
    return fund.from_info(info(**overrides))


def metric(key):
    return fund.BY_KEY[key]


class FromInfoTests(unittest.TestCase):
    def test_every_metric_is_read_from_its_yahoo_field(self):
        data = parsed()
        self.assertEqual(data["values"]["pe"], 30.5)
        self.assertEqual(data["values"]["forward_pe"], 27.0)
        self.assertEqual(data["values"]["gross_margin"], 0.46)
        self.assertEqual(data["values"]["market_cap"], 3.0e12)
        self.assertEqual(data["values"]["beta"], 1.2)
        self.assertEqual(set(data["values"]), {m.key for m in fund.METRICS})

    def test_missing_fields_stay_empty(self):
        data = fund.from_info({"trailingPE": 12.0, "currency": "EUR"})
        self.assertEqual(data["values"]["pe"], 12.0)
        self.assertIsNone(data["values"]["revenue"])
        self.assertIsNone(data["values"]["dividend_yield"])

    def test_text_nan_and_infinity_are_not_numbers(self):
        data = parsed(trailingPE="Infinity", forwardPE=float("nan"), priceToBook=float("inf"), beta="n/a", marketCap=True)
        for key in ("pe", "forward_pe", "pb", "beta", "market_cap"):
            self.assertIsNone(data["values"][key], key)

    def test_numbers_given_as_text_are_accepted(self):
        self.assertEqual(parsed(trailingPE="12.5")["values"]["pe"], 12.5)

    def test_the_debt_ratio_is_converted_from_percent_to_a_fraction(self):
        self.assertEqual(parsed(debtToEquity=150.0)["values"]["debt_to_equity"], 1.5)

    def test_currencies_come_from_the_report_and_the_listing(self):
        data = parsed(currency="TWD", financialCurrency="USD")
        self.assertEqual((data["currency"], data["price_currency"]), ("USD", "TWD"))

    def test_without_a_report_currency_the_listing_currency_is_used(self):
        data = fund.from_info({"currency": "EUR", "trailingPE": 10})
        self.assertEqual((data["currency"], data["price_currency"]), ("EUR", "EUR"))

    def test_the_last_reported_quarter_is_a_date(self):
        self.assertEqual(parsed()["period"], "2026-06-27")
        self.assertIsNone(parsed(mostRecentQuarter=None)["period"])
        self.assertIsNone(parsed(mostRecentQuarter=1e30)["period"])

    def test_the_result_can_be_stored_as_json(self):
        data = parsed()
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_nothing_from_yahoo_is_empty(self):
        self.assertTrue(fund.is_empty(fund.from_info({})))
        self.assertTrue(fund.is_empty(fund.from_info({"currency": "USD", "trailingPE": None})))
        self.assertTrue(fund.is_empty(None))
        self.assertFalse(fund.is_empty(parsed()))

    def test_every_metric_has_a_glossary_term_and_a_known_group(self):
        for item in fund.METRICS:
            self.assertIn(item.term, glossary.GLOSSARY, item.key)
            self.assertIn(item.group, fund.GROUPS, item.key)
        self.assertEqual(len({m.key for m in fund.METRICS}), len(fund.METRICS))


class DividendYieldTests(unittest.TestCase):
    def test_yahoos_percent_field_is_converted_to_a_fraction(self):
        # echte Werte: NVIDIA meldet dividendYield 0.43 und meint 0,43 %
        self.assertAlmostEqual(fund.dividend_yield({"currency": "USD", "currentPrice": 231.33, "dividendRate": 1.0,
                                                    "dividendYield": 0.43}), 0.0043)

    def test_an_older_fraction_is_recognised_by_the_cross_check(self):
        self.assertAlmostEqual(fund.dividend_yield({"currency": "USD", "currentPrice": 100.0, "dividendRate": 4.0,
                                                    "dividendYield": 0.04}), 0.04)

    def test_without_a_rate_the_field_counts_as_percent(self):
        self.assertAlmostEqual(fund.dividend_yield({"dividendYield": 1.24}), 0.0124)

    def test_it_is_computed_when_the_field_is_missing(self):
        self.assertAlmostEqual(fund.dividend_yield({"currency": "USD", "dividendRate": 4.0, "currentPrice": 100.0}), 0.04)
        self.assertAlmostEqual(fund.dividend_yield({"currency": "USD", "dividendRate": 2.0, "regularMarketPrice": 50.0}), 0.04)

    def test_pence_quotes_are_converted_before_computing(self):
        # BAE Systems: Kurs in Pence (1845,5), Dividende in Pfund (0,36) → 1,95 %, nicht 0,02 %
        info = {"currency": "GBp", "currentPrice": 1845.5, "dividendRate": 0.36}
        self.assertAlmostEqual(fund.dividend_yield(info), 0.0195, places=4)
        self.assertAlmostEqual(fund.dividend_yield({**info, "dividendYield": 1.98}), 0.0198)

    def test_the_trailing_field_is_not_trusted(self):
        # TSM: Dividende in Taiwan-Dollar, Kurs in Dollar → Yahoo nennt für das Vorjahr 5,7 %, richtig sind 0,82 %
        info = {"currency": "USD", "currentPrice": 455.47, "dividendRate": 3.77, "dividendYield": 0.82,
                "trailingAnnualDividendYield": 0.0568}
        self.assertAlmostEqual(fund.dividend_yield(info), 0.0082)
        self.assertIsNone(fund.dividend_yield({"trailingAnnualDividendYield": 0.0568}))

    def test_an_implausible_yield_is_dropped(self):
        self.assertIsNone(fund.dividend_yield({"currency": "USD", "dividendRate": 50.0, "currentPrice": 100.0}))
        self.assertIsNotNone(fund.dividend_yield({"currency": "USD", "dividendRate": 25.0, "currentPrice": 100.0}))

    def test_no_dividend_gives_none(self):
        self.assertIsNone(fund.dividend_yield({}))
        self.assertIsNone(fund.dividend_yield({"dividendRate": None, "currentPrice": 10.0}))

    def test_a_zero_price_does_not_divide(self):
        self.assertIsNone(fund.dividend_yield({"dividendRate": 1.0, "currentPrice": 0}))

    def test_other_minor_units_are_known(self):
        self.assertEqual(fund.major_currency("GBp"), "GBP")
        self.assertEqual(fund.major_currency("GBX"), "GBP")
        self.assertEqual(fund.major_currency("ZAc"), "ZAR")
        self.assertEqual(fund.major_currency("USD"), "USD")
        self.assertEqual(fund.major_currency(""), "")

    def test_the_market_value_of_a_pence_stock_is_labelled_in_pounds(self):
        data = fund.from_info({"currency": "GBp", "financialCurrency": "GBP", "marketCap": 5.4e10})
        self.assertEqual(data["price_currency"], "GBP")
        self.assertEqual(fund.format_value(metric("market_cap"), 5.4e10, data), "54.00 Mrd. GBP")


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.data = parsed()

    def fmt(self, key, value):
        return fund.format_value(metric(key), value, self.data)

    def test_ratios_have_two_decimals(self):
        self.assertEqual(self.fmt("pe", 30.5), "30.50")
        self.assertEqual(self.fmt("beta", 1.2), "1.20")

    def test_percentages_show_one_decimal(self):
        self.assertEqual(self.fmt("gross_margin", 0.46), "46.0 %")
        self.assertEqual(self.fmt("net_margin", -0.052), "-5.2 %")

    def test_the_debt_ratio_shows_whole_percent(self):
        self.assertEqual(self.fmt("debt_to_equity", 1.5), "150 %")

    def test_amounts_are_scaled_and_carry_the_currency(self):
        self.assertEqual(self.fmt("revenue", 4.0e11), "400.00 Mrd. USD")
        self.assertEqual(self.fmt("market_cap", 3.0e12), "3.00 Bio. USD")
        self.assertEqual(self.fmt("cash", 2.5e7), "25.00 Mio. USD")
        self.assertEqual(self.fmt("debt", 12345.0), "12,345 USD")
        self.assertEqual(self.fmt("free_cashflow", -1.5e9), "-1.50 Mrd. USD")

    def test_the_market_value_uses_the_listing_currency_the_rest_the_report_currency(self):
        self.data = parsed(currency="TWD", financialCurrency="USD")
        self.assertTrue(self.fmt("market_cap", 1.0e12).endswith("TWD"))
        self.assertTrue(self.fmt("revenue", 1.0e12).endswith("USD"))
        self.assertTrue(self.fmt("eps", 6.5).endswith("USD"))

    def test_a_missing_value_is_a_dash(self):
        self.assertEqual(self.fmt("pe", None), "–")

    def test_a_loss_makes_the_valuation_ratios_negative_not_a_number(self):
        for key in ("pe", "forward_pe", "pb", "ps", "ev_ebitda", "peg"):
            self.assertEqual(self.fmt(key, -4.0), "negativ", key)
        self.assertEqual(self.fmt("pe", 0.0), "negativ")

    def test_margins_may_be_negative(self):
        self.assertEqual(self.fmt("op_margin", -0.2), "-20.0 %")

    def test_absurd_ratios_are_capped(self):
        self.assertEqual(self.fmt("pe", 40000.0), "über 1000")
        self.assertEqual(self.fmt("pe", 1000.0), "1000.00")

    def test_a_missing_currency_leaves_no_trailing_space(self):
        self.data = fund.from_info({"totalRevenue": 5.0e9})
        self.assertEqual(self.fmt("revenue", 5.0e9), "5.00 Mrd.")


class ToneTests(unittest.TestCase):
    def test_profit_and_growth_are_coloured_by_their_sign(self):
        self.assertEqual(fund.tone(metric("net_margin"), 0.1), "good")
        self.assertEqual(fund.tone(metric("net_margin"), -0.1), "bad")
        self.assertEqual(fund.tone(metric("revenue_growth"), -0.01), "bad")
        self.assertEqual(fund.tone(metric("free_cashflow"), -5.0), "bad")
        self.assertEqual(fund.tone(metric("eps"), 1.0), "good")

    def test_ratios_without_a_good_or_bad_direction_stay_neutral(self):
        for key in ("pe", "beta", "debt_to_equity", "current_ratio", "dividend_yield", "gross_margin"):
            self.assertEqual(fund.tone(metric(key), 5.0), "", key)

    def test_zero_and_missing_are_neutral(self):
        self.assertEqual(fund.tone(metric("net_margin"), 0.0), "")
        self.assertEqual(fund.tone(metric("net_margin"), None), "")


class GroupsTests(unittest.TestCase):
    def test_groups_come_in_a_fixed_order_with_all_their_metrics(self):
        result = fund.groups(parsed())
        self.assertEqual([name for name, _ in result], list(fund.GROUPS))
        self.assertEqual(sum(len(lines) for _, lines in result), len(fund.METRICS))

    def test_a_group_without_any_value_is_left_out(self):
        data = fund.from_info({"trailingPE": 10.0, "currency": "USD"})
        self.assertEqual([name for name, _ in fund.groups(data)], ["Bewertung"])

    def test_missing_values_inside_a_shown_group_are_listed_as_dashes(self):
        data = fund.from_info({"trailingPE": 10.0, "currency": "USD"})
        [(_, lines)] = fund.groups(data)
        texts = {m.key: text for m, text, _ in lines}
        self.assertEqual((texts["pe"], texts["pb"]), ("10.00", "–"))


class AgeTests(unittest.TestCase):
    def test_a_fresh_note_names_source_time_and_quarter(self):
        note = fund.age_note(parsed(), NOW - dt.timedelta(hours=2), NOW)
        self.assertIn("Stand 09.10.2026 10:00.", note)
        self.assertIn("Quelle: Yahoo Finance.", note)
        self.assertIn("Letztes Quartal laut Bilanz: 27.06.2026.", note)
        self.assertNotIn("Veraltet", note)

    def test_an_old_note_says_so(self):
        note = fund.age_note(parsed(), NOW - dt.timedelta(days=8), NOW)
        self.assertTrue(note.startswith("Veraltet: Stand 01.10.2026."))

    def test_different_currencies_are_pointed_out(self):
        note = fund.age_note(parsed(currency="TWD", financialCurrency="USD"), NOW, NOW)
        self.assertIn("Bilanzzahlen in USD, Kurs in TWD.", note)
        self.assertNotIn("Bilanzzahlen in", fund.age_note(parsed(), NOW, NOW))

    def test_the_staleness_limit_is_seven_days(self):
        self.assertFalse(fund.is_stale(NOW - dt.timedelta(days=7), NOW))
        self.assertTrue(fund.is_stale(NOW - dt.timedelta(days=7, seconds=1), NOW))

    def test_refresh_after_twelve_hours_or_when_never_loaded(self):
        self.assertFalse(fund.needs_refresh(NOW - dt.timedelta(hours=12), NOW))
        self.assertTrue(fund.needs_refresh(NOW - dt.timedelta(hours=12, seconds=1), NOW))
        self.assertTrue(fund.needs_refresh(None, NOW))


class FetchTests(unittest.TestCase):
    def test_the_data_comes_from_yahoo_info(self):
        patcher, _ = patch_yf(details=info())
        with patcher:
            data = sd.fetch_fundamentals("AAPL")
        self.assertEqual(data["values"]["pe"], 30.5)
        self.assertEqual(data["currency"], "USD")

    def test_an_instrument_without_figures_is_an_error_with_a_reason(self):
        patcher, _ = patch_yf(details={"currency": "USD", "quoteType": "ETF"})
        with patcher, self.assertRaises(ValueError) as context:
            sd.fetch_fundamentals("SPY")
        self.assertIn("keine Kennzahlen", str(context.exception))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.path = os.path.join(self.dir.name, "test.db")
        self.store = Store(self.path)
        self.addCleanup(self.store.close)

    def test_values_roundtrip_with_source_and_time(self):
        data = parsed()
        self.store.save_fundamentals("AAPL", data, "Yahoo Finance", NOW)
        self.assertEqual(self.store.fundamentals(), {"AAPL": {"data": data, "source": "Yahoo Finance", "fetched_at": NOW}})

    def test_saving_again_replaces_the_old_values(self):
        self.store.save_fundamentals("AAPL", parsed(trailingPE=10.0), "Yahoo Finance", NOW)
        self.store.save_fundamentals("AAPL", parsed(trailingPE=20.0), "Yahoo Finance", NOW + dt.timedelta(days=1))
        found = self.store.fundamentals()["AAPL"]
        self.assertEqual(found["data"]["values"]["pe"], 20.0)
        self.assertEqual(found["fetched_at"], NOW + dt.timedelta(days=1))

    def test_a_damaged_entry_is_skipped_not_fatal(self):
        self.store.save_fundamentals("MSFT", parsed(), "Yahoo Finance", NOW)
        self.store.db.execute("INSERT INTO fundamentals VALUES ('BAD', '{kaputt', 'x', ?)", (NOW.isoformat(),))
        self.store.db.commit()
        self.assertEqual(list(self.store.fundamentals()), ["MSFT"])

    def test_the_values_survive_a_restart(self):
        self.store.save_fundamentals("AAPL", parsed(), "Yahoo Finance", NOW)
        self.store.close()
        again = Store(self.path)
        self.addCleanup(again.close)
        self.assertIn("AAPL", again.fundamentals())

    def test_an_older_database_gets_the_table(self):
        self.store.db.execute("DROP TABLE fundamentals")
        self.store.db.commit()
        self.store.close()
        again = Store(self.path)
        self.addCleanup(again.close)
        self.assertEqual(again.fundamentals(), {})
        self.assertEqual(again.meta("schema_version"), "7")


class ControllerTests(AppTestCase):
    def serve(self, **overrides):
        calls = []

        def fetch(symbol):
            calls.append(symbol)
            return fund.from_info(info(**overrides))
        patcher = mock.patch.object(sd, "fetch_fundamentals", fetch)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    def test_loading_stores_values_with_source_and_time_and_tells_the_windows(self):
        self.serve()
        seen = []
        self.ctl.fundamentals_changed.connect(seen.append)
        self.assertTrue(self.ctl.load_fundamentals("AAPL"))
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals))
        entry = self.ctl.fundamentals["AAPL"]
        self.assertEqual((entry["source"], entry["data"]["values"]["pe"]), ("Yahoo Finance", 30.5))
        self.assertIn("AAPL", self.ctl.store.fundamentals())
        self.assertEqual(seen, ["AAPL"])

    def test_fresh_values_are_not_loaded_again(self):
        calls = self.serve()
        self.ctl.load_fundamentals("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals))
        self.assertFalse(self.ctl.load_fundamentals("AAPL"))
        self.assertEqual(calls, ["AAPL"])

    def test_old_values_are_loaded_again_and_force_always_loads(self):
        calls = self.serve()
        self.ctl.load_fundamentals("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals))
        self.ctl.fundamentals["AAPL"]["fetched_at"] -= dt.timedelta(hours=13)
        self.assertTrue(self.ctl.load_fundamentals("AAPL"))
        self.assertTrue(wait_until(lambda: len(calls) == 2))
        self.assertTrue(wait_until(lambda: not self.ctl.fundamentals_loading))
        self.assertTrue(self.ctl.load_fundamentals("AAPL", force=True))

    def test_a_request_in_flight_is_not_doubled(self):
        self.serve()
        self.assertTrue(self.ctl.load_fundamentals("AAPL"))
        self.assertFalse(self.ctl.load_fundamentals("AAPL", force=True))

    def test_a_failure_is_remembered_and_announced(self):
        seen = []
        self.ctl.fundamentals_changed.connect(seen.append)
        self.ctl.load_fundamentals("AAPL")  # die Vorgabe der Tests: Yahoo nennt keine Kennzahlen
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals_errors))
        self.assertIn("keine Kennzahlen", self.ctl.fundamentals_errors["AAPL"])
        self.assertNotIn("AAPL", self.ctl.fundamentals)
        self.assertEqual(seen, ["AAPL"])
        self.assertFalse(self.ctl.fundamentals_loading)

    def test_a_failed_refresh_keeps_the_old_values(self):
        self.serve()
        self.ctl.load_fundamentals("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals))
        before = self.ctl.fundamentals["AAPL"]
        mock.patch.object(sd, "fetch_fundamentals", mock.Mock(side_effect=ValueError("offline"))).start()
        self.addCleanup(mock.patch.stopall)
        self.ctl.load_fundamentals("AAPL", force=True)
        self.assertTrue(wait_until(lambda: self.ctl.fundamentals_errors.get("AAPL") == "offline"))
        self.assertIs(self.ctl.fundamentals["AAPL"], before)

    def test_a_success_clears_an_earlier_error(self):
        self.ctl.load_fundamentals("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals_errors))
        self.serve()
        self.ctl.load_fundamentals("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals))
        self.assertNotIn("AAPL", self.ctl.fundamentals_errors)

    def test_removing_a_stock_forgets_its_values(self):
        self.serve()
        self.ctl.load_fundamentals("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals))
        self.ctl.remove("AAPL")
        self.assertNotIn("AAPL", self.ctl.fundamentals)

    def test_a_result_for_a_removed_stock_is_dropped(self):
        self.serve()
        self.ctl.load_fundamentals("AAPL")
        self.ctl.remove("AAPL")
        self.assertTrue(wait_until(lambda: not self.ctl.fundamentals_loading))
        self.assertNotIn("AAPL", self.ctl.fundamentals)
        self.assertNotIn("AAPL", self.ctl.store.fundamentals())

    def test_values_are_there_after_a_restart(self):
        self.serve()
        self.ctl.load_fundamentals("AAPL")
        self.assertTrue(wait_until(lambda: "AAPL" in self.ctl.fundamentals))
        self.assertEqual(self.ctl.store.fundamentals()["AAPL"]["data"], self.ctl.fundamentals["AAPL"]["data"])


if __name__ == "__main__":
    unittest.main()
