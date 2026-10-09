"""Das zentrale Glossar: Vollständigkeit, Rechenbeispiele und dass jeder Verweis im Programm einen Begriff trifft."""
import os
import re
import unittest

import glossary
from glossary import GLOSSARY

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class GlossaryContentTests(unittest.TestCase):
    def test_keys_match_their_terms_and_are_simple_identifiers(self):
        for key, term in GLOSSARY.items():
            self.assertEqual(key, term.key)
            self.assertRegex(key, r"^[a-z][a-z0-9_]*$")

    def test_every_term_has_a_title_and_an_explanation(self):
        for term in GLOSSARY.values():
            self.assertTrue(term.title.strip(), term.key)
            self.assertGreater(len(term.text.strip()), 20, term.key)
            self.assertNotIn("TODO", term.text + term.interpretation)

    def test_titles_are_unique_so_one_word_never_means_two_things(self):
        titles = [t.title for t in GLOSSARY.values()]
        self.assertEqual(len(titles), len(set(titles)))

    def test_the_terms_of_the_plan_are_there(self):
        for key in ("kgv", "kuv", "peg", "ev_ebitda", "free_cashflow", "ebitda", "eps", "marge", "kursziel",
                    "konsens", "guidance", "dividendenrendite", "nettoverschuldung", "marktkapitalisierung"):
            self.assertIn(key, GLOSSARY)

    def test_every_term_used_in_the_current_screens_is_there(self):
        for key in ("kurs", "tag", "position", "positionswert", "startbestand", "einstand", "gebuehr", "fifo", "gv",
                    "gv_prozent", "unrealisiert", "realisiert", "gesamtergebnis", "gesamtrendite", "investiert",
                    "gesamtwert", "basiswaehrung", "kursgewinn", "waehrungseffekt", "kurs_waehrung", "anteil",
                    "aufteilung", "verlauf", "historie", "veraltet", "termin", "quartalszahlen", "ex_dividende",
                    "dividendenzahlung"):
            self.assertIn(key, GLOSSARY)

    def test_ratios_name_their_formula_and_unit(self):
        for key in ("kgv", "kuv", "peg", "ev_ebitda", "free_cashflow", "marge", "dividendenrendite"):
            term = GLOSSARY[key]
            self.assertTrue(term.formula and term.unit, key)

    def test_kgv_is_explained_with_formula_interpretation_and_example(self):
        kgv = glossary.term("kgv")
        self.assertEqual(kgv.full, "Kurs-Gewinn-Verhältnis (Price-to-Earnings Ratio, P/E)")
        self.assertEqual(kgv.formula, "Aktienkurs ÷ Gewinn je Aktie")
        self.assertIn("hohes KGV", kgv.interpretation)
        self.assertIn("niedriges KGV", kgv.interpretation)

    def test_sections_are_in_a_fixed_order_and_skip_empty_ones(self):
        self.assertEqual([name for name, _ in glossary.term("kgv").sections()],
                         ["Formel", "Einheit", "Deutung", "Beispiel"])
        self.assertEqual([name for name, _ in glossary.term("dividendenzahlung").sections()], ["Deutung"])
        self.assertEqual(glossary.term("basiswaehrung").sections(), [("Einheit", "EUR")])

    def test_worked_examples_are_correct(self):
        self.assertEqual(120 / 6, 20)                      # KGV
        self.assertIn("KGV 20", glossary.term("kgv").example)
        self.assertEqual(5 / 1, 5)                         # KUV
        self.assertIn("KUV 5", glossary.term("kuv").example)
        self.assertEqual(24 / 12, 2)                       # PEG
        self.assertIn("PEG 2", glossary.term("peg").example)
        self.assertEqual((10 * 100 + 10 * 120) / 20, 110)  # Einstand
        self.assertIn("Einstand 110", glossary.term("einstand").example)
        self.assertEqual(3 / 100 * 100, 3)                 # Dividendenrendite
        self.assertIn("3 %", glossary.term("dividendenrendite").example)
        self.assertEqual(round(168 / 1150 * 100, 1), 14.6)  # Gesamtrendite
        self.assertIn("14,6 %", glossary.term("gesamtrendite").example)
        self.assertEqual((100 - 80) * 10, 200)             # G/V
        self.assertIn("+200", glossary.term("gv").example)

    def test_fifo_example_matches_how_the_ledger_sells(self):
        import datetime as dt
        import ledger
        txs = [ledger.Transaction(1, "A", "buy", 10, 100, 0, dt.date(2026, 1, 1)),
               ledger.Transaction(2, "A", "buy", 10, 120, 0, dt.date(2026, 2, 1)),
               ledger.Transaction(3, "A", "sell", 15, 130, 0, dt.date(2026, 3, 1))]
        sale = ledger.replay(txs).sales[0]
        self.assertEqual([(p.shares, p.cost / p.shares) for p in sale.pieces], [(10, 100), (5, 120)])
        self.assertIn("10 zu 100 und 5 zu 120", glossary.term("fifo").example)

    def test_the_glossary_definitions_match_the_programs_formulas(self):
        import stock_data as sd
        self.assertAlmostEqual(sd.pl_percent({"cost": 80.0, "shares": 10}, 100.0), 25.0)
        self.assertAlmostEqual(sd.pl_amount({"cost": 80.0, "shares": 10}, 100.0), 200.0)
        self.assertIn("(Kurs ÷ Einstandskurs − 1) × 100", glossary.term("gv_prozent").formula)
        self.assertIn("(Kurs − Einstandskurs) × Stückzahl", glossary.term("gv").formula)


class GlossaryLinkTests(unittest.TestCase):
    def test_unknown_key_is_an_error(self):
        with self.assertRaises(KeyError):
            glossary.term("gibt-es-nicht")
        with self.assertRaises(KeyError):
            glossary.link("Text", "gibt-es-nicht")

    def test_link_roundtrip(self):
        html = glossary.link("Einstand", "einstand")
        self.assertEqual(html, '<a href="term:einstand">Einstand</a>')
        self.assertEqual(glossary.key_of_link("term:einstand"), "einstand")

    def test_link_text_is_escaped(self):
        self.assertEqual(glossary.link("a < b & c", "kgv"), '<a href="term:kgv">a &lt; b &amp; c</a>')

    def test_foreign_and_unknown_links_are_ignored(self):
        for url in ("https://example.com", "term:", "term:gibt-es-nicht", "", "mailto:x"):
            self.assertIsNone(glossary.key_of_link(url))


class UsedKeysTests(unittest.TestCase):
    """Jeder Schlüssel, mit dem die Oberfläche einen Begriff anbindet, muss im Glossar stehen."""

    def used_keys(self):
        with open(os.path.join(ROOT, "stock_widget.py"), encoding="utf-8") as f:
            source = f.read()
        keys = set(re.findall(r"""(?:explain|term_link|StatTile)\(.*?, ["']([a-z][a-z0-9_]*)["']""", source))
        import stock_widget
        return keys | set(stock_widget.HEAD_TERMS.values()) | set(stock_widget.EVENT_TERMS.values())

    def test_the_search_actually_finds_the_connections(self):
        self.assertGreaterEqual(len(self.used_keys()), 20)

    def test_every_connected_key_exists_in_the_glossary(self):
        missing = sorted(key for key in self.used_keys() if key not in GLOSSARY)
        self.assertEqual(missing, [])

    def test_every_event_type_the_program_knows_has_an_explanation(self):
        import stock_data as sd
        import stock_widget
        self.assertEqual(set(stock_widget.EVENT_TERMS), set(sd.EVENT_LABELS.values()))

    def test_every_sortable_column_with_a_meaning_has_an_explanation(self):
        import stock_widget
        keys = {key for key, *_ in stock_widget.COLUMNS}
        self.assertTrue(set(stock_widget.HEAD_TERMS) <= keys)
        self.assertEqual(keys - set(stock_widget.HEAD_TERMS), {"symbol"})  # nur das Kürzel braucht keine Erklärung


if __name__ == "__main__":
    unittest.main()
