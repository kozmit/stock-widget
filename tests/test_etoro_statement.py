"""Import des eToro-Kontoauszugs: Einlesen der xlsx-Datei und Umrechnung in Käufe und Verkäufe, ohne Netzwerk."""
import datetime as dt
import os
import tempfile
import unittest
import zipfile

import etoro_statement as es
import sources
import xlsx_reader

CLOSED_HEAD = ["Positions-ID", "Aktion", "Long / Short", "Betrag", "Einheiten", "Eröffnungsdatum", "Schließungsdatum",
               "Hebel", "Eröffnungskurs", "Schlusskurs", "Art"]
ACTIVITY_HEAD = ["Datum", "Art", "Details", "Betrag", "Einheiten", "Positions-ID", "Anlagentyp"]


def closed(pid, name, units, opened, closed_on, rate_open, rate_close, art="Aktien", leverage="1", side="Long"):
    return [pid, name, side, "100", units, opened, closed_on, leverage, rate_open, rate_close, art]


def opened(pid, details, units, day, amount="100", kind="Aktien"):
    return [day, "Position eröffnen", details, amount, units, pid, kind]


def book(closed_rows, activity_rows):
    return {"Konto": [["x"]], "Geschlossene Positionen": [CLOSED_HEAD] + closed_rows,
            "Kontoaktivität": [ACTIVITY_HEAD] + activity_rows}


class StatementTests(unittest.TestCase):
    def read(self, closed_rows, activity_rows=()):
        return es.trades_from_book(book(closed_rows, list(activity_rows)))

    def test_closed_position_becomes_a_buy_and_a_sell_with_the_ids_of_the_api(self):
        trades, _ = self.read([closed("42", "NVIDIA Corporation (NVDA)", "6.23", "21/04/2025 13:30:23",
                                      "26/09/2025 16:11:49", "99.13", "175.95")])
        self.assertEqual([(t.external_id, t.kind, t.symbol, t.shares, t.price, t.day) for t in trades],
                         [("42:open", "buy", "NVDA", 6.23, 99.13, dt.date(2025, 4, 21)),
                          ("42:close", "sell", "NVDA", 6.23, 175.95, dt.date(2025, 9, 26))])

    def test_currency_decides_the_exchange_when_the_symbol_has_none(self):
        trades, _ = self.read(
            [closed("1", "iShares Core MSCI World UCITS ETF (SWDA)", "2", "14/11/2024 08:00:06", "03/06/2025 07:00:30",
                    "8616", "8377", art="ETF"),
             closed("2", "DroneShield Ltd (DRO)", "10", "14/11/2024 08:00:06", "03/06/2025 07:00:30", "3", "4")],
            [opened("1", "SWDA/GBX", "2", "14/11/2024 08:00:06", kind="ETF"),
             opened("2", "DRO/AUD", "10", "14/11/2024 08:00:06")])
        self.assertEqual({t.symbol for t in trades}, {"SWDA.L", "DRO.AX"})

    def test_symbols_with_an_exchange_suffix_keep_it(self):
        trades, _ = self.read([closed("3", "Rheinmetall AG (RHM.DE)", "1", "21/02/2025 12:52:46",
                                      "23/09/2025 09:02:52", "894", "1906.5")],
                              [opened("3", "RHM.DE/EUR", "1", "21/02/2025 12:52:46")])
        self.assertEqual({t.symbol for t in trades}, {"RHM.DE"})

    def test_leveraged_long_cfds_count_as_equivalent_trades_shorts_and_crypto_are_skipped(self):
        trades, notes = self.read([
            closed("4", "Beyond Meat Inc. (BYND)", "144.3", "22/10/2025 14:42:37", "22/10/2025 15:34:28", "6.93",
                   "4.65", art="CFD", leverage="2"),
            closed("5", "Tesla (TSLA)", "1", "01/01/2025 10:00:00", "02/01/2025 10:00:00", "1", "2", side="Short"),
            closed("6", "Bitcoin (BTC)", "1", "01/01/2025 10:00:00", "02/01/2025 10:00:00", "1", "2", art="Krypto")])
        buy, sell = trades
        self.assertEqual((buy.external_id, buy.symbol), ("4:open", "BYND"))
        self.assertAlmostEqual(buy.shares * buy.price, 500.0, places=1)
        self.assertAlmostEqual(sell.shares * sell.price - buy.shares * buy.price, 144.3 * (4.65 - 6.93), places=1)
        self.assertIn("Hebel 2", buy.note)
        self.assertIn("2 geschlossene Position(en) übersprungen", notes[0])
        self.assertIn("Tesla", notes[0])

    def test_allocation_without_purchase_amount_costs_nothing_so_it_is_all_profit(self):
        trades, notes = self.read([], [opened("7", "TKMS.DE/EUR", "2.63158", "23/10/2025 07:32:03", amount="0")])
        [trade] = trades
        self.assertEqual((trade.external_id, trade.symbol, trade.kind, trade.shares, trade.price, trade.day),
                         ("7:open", "TKMS.DE", "buy", 2.63158, 0.0, dt.date(2025, 10, 23)))
        self.assertIn("Einstand 0", notes[0])

    def test_a_free_allocation_passes_the_plan_but_a_free_manual_buy_does_not(self):
        import ledger
        trades, _ = self.read([], [opened("7", "TKMS.DE/EUR", "2", "23/10/2025 07:32:03", amount="0")])
        plan = sources.plan_import("etoro", trades, [], {}, {"TKMS.DE"})
        self.assertEqual((len(plan.new), plan.rejected), (1, []))
        manual = ledger.Transaction(1, "X", "buy", 1, 0, 0, dt.date(2026, 1, 1), "", "manual")
        with self.assertRaises(ValueError):
            ledger.validate(manual)

    def test_normal_opens_and_closed_zero_amount_positions_are_not_allocations(self):
        trades, _ = self.read(
            [closed("8", "Foo (FOO)", "1", "01/01/2025 10:00:00", "02/01/2025 10:00:00", "1", "2")],
            [opened("8", "FOO/USD", "1", "01/01/2025 10:00:00", amount="0"),
             opened("9", "BAR/USD", "1", "01/01/2025 10:00:00", amount="100")])
        self.assertEqual([t.external_id for t in trades], ["8:open", "8:close"])

    def test_wrong_file_gives_a_readable_error(self):
        with self.assertRaises(es.StatementError) as caught:
            es.trades_from_book({"Tabelle1": [["a"]]})
        self.assertIn("Geschlossene Positionen", str(caught.exception))
        broken = {"Geschlossene Positionen": [["Positions-ID"]], "Kontoaktivität": [ACTIVITY_HEAD]}
        with self.assertRaises(es.StatementError):
            es.trades_from_book(broken)

    def test_what_the_api_booked_already_is_recognised_as_duplicate_and_the_rest_is_new(self):
        trades, _ = self.read([closed("42", "NVIDIA Corporation (NVDA)", "6.23", "21/04/2025 13:30:23",
                                      "26/09/2025 16:11:49", "99.13", "175.95"),
                               closed("43", "Rheinmetall AG (RHM.DE)", "1", "21/02/2025 12:52:46",
                                      "23/09/2025 09:02:52", "894", "1906.5")],
                              [opened("43", "RHM.DE/EUR", "1", "21/02/2025 12:52:46")])
        import ledger
        api = ledger.Transaction(1, "RHM.DE", "buy", 1, 894, 0, dt.date(2025, 2, 21), "", "etoro", "43:open")
        plan = sources.plan_import("etoro", trades, [api], {}, {"NVDA", "RHM.DE"})
        self.assertEqual(plan.duplicates, 1)
        self.assertEqual({t.external_id for _, t in plan.new}, {"42:open", "42:close", "43:close"})
        self.assertEqual(plan.rejected, [])


class ReaderTests(unittest.TestCase):
    def make_xlsx(self, path):
        main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
        rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
        parts = {
            "xl/workbook.xml": f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>'
                               '<sheet name="Blatt A" sheetId="1" r:id="rId1"/></sheets></workbook>',
            "xl/_rels/workbook.xml.rels": '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                                          '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>',
            "xl/sharedStrings.xml": f'<sst xmlns="{main}"><si><t>Kopf</t></si><si><t>Zelle</t></si></sst>',
            "xl/worksheets/sheet1.xml": f'<worksheet xmlns="{main}"><sheetData>'
                                        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
                                        '<row r="2"><c r="B2"><v>12.5</v></c><c r="C2" t="inlineStr"><is><t>roh</t></is></c></row>'
                                        '</sheetData></worksheet>'}
        with zipfile.ZipFile(path, "w") as z:
            for name, content in parts.items():
                z.writestr(name, content)

    def test_reads_shared_strings_numbers_inline_text_and_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "t.xlsx")
            self.make_xlsx(path)
            self.assertEqual(xlsx_reader.read_workbook(path),
                             {"Blatt A": [["Kopf", None, "Zelle"], [None, "12.5", "roh"]]})


if __name__ == "__main__":
    unittest.main()
