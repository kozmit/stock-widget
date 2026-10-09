"""Steuerschätzung: Rechnung (tax.py), Controller (Einstellungen, Eingaben, Verkäufe in Euro) und Fenster."""
import datetime as dt
import unittest
from unittest import mock

import fx as fx_module
import glossary
import ledger
import stock_widget as w
import tax
from tests.support import AppTestCase, DialogDriver, QUOTE, wait_until

Y = 2026


def sale(gain, transaction_id=1, day=dt.date(Y, 6, 1), symbol="AAPL", cost=1000.0, estimated=False):
    return tax.SaleFigures(transaction_id, symbol, day, 1, cost + gain, cost, "EUR", estimated)


def year(sales, inputs=None, settings=None, years=None, y=Y):
    return tax.compute_years(sales, {y: inputs} if inputs else {}, settings or tax.Settings(), years)[y]


class RateTests(unittest.TestCase):
    def test_without_church_tax_the_total_is_26_375_percent(self):
        income, soli, church = tax.rates(0.0)
        self.assertAlmostEqual(income + soli + church, 0.26375)
        self.assertEqual(church, 0)

    def test_church_tax_lowers_the_income_tax_and_raises_the_total(self):
        for rate, total in ((0.08, 0.27819), (0.09, 0.27995)):
            income, soli, church = tax.rates(rate)
            self.assertAlmostEqual(income + soli + church, total, places=5)
            self.assertLess(income, 0.25)
        self.assertAlmostEqual(tax.rates(0.09)[2], 0.09 * tax.rates(0.09)[0])


class YearTests(unittest.TestCase):
    def test_a_gain_below_the_allowance_is_free_of_tax(self):
        report = year([sale(800)])
        self.assertEqual((report.taxable, report.tax), (0, 0))
        self.assertAlmostEqual(report.allowance_left, 200)

    def test_a_gain_above_the_allowance_is_taxed_with_soli(self):
        report = year([sale(3000)])
        self.assertAlmostEqual(report.taxable, 2000)
        self.assertEqual((report.income_tax, report.soli, report.tax), (500.0, 27.5, 527.5))

    def test_church_tax_is_added(self):
        report = year([sale(3000)], settings=tax.Settings(0.09))
        self.assertEqual((report.income_tax, report.soli, report.church, report.tax), (489.0, 26.89, 44.01, 559.9))

    def test_a_joint_assessment_doubles_the_allowance(self):
        report = year([sale(3000)], settings=tax.Settings(joint=True))
        self.assertEqual((report.allowance_available, report.taxable), (2000, 1000))
        self.assertEqual(tax.Settings(joint=True).allowance, 2000)

    def test_losses_reduce_gains_within_the_year(self):
        report = year([sale(3000, 1), sale(-1000, 2, symbol="MSFT")])
        self.assertEqual((report.gains, report.losses, report.net_stock), (3000, 1000, 2000))
        self.assertAlmostEqual(report.taxable, 1000)

    def test_a_net_loss_is_carried_forward_and_pays_no_tax(self):
        report = year([sale(-500)])
        self.assertEqual((report.tax, report.taxable, report.stock_carry_out), (0, 0, 500))

    def test_stock_losses_never_reduce_dividends(self):
        report = year([sale(-5000)], tax.YearInputs(other_income=3000))
        self.assertEqual(report.subtotal, 3000)
        self.assertAlmostEqual(report.taxable, 2000)
        self.assertEqual(report.stock_carry_out, 5000)

    def test_other_income_uses_the_allowance_together_with_gains(self):
        report = year([sale(600)], tax.YearInputs(other_income=700))
        self.assertAlmostEqual(report.subtotal, 1300)
        self.assertAlmostEqual(report.taxable, 300)

    def test_a_carry_from_the_input_reduces_the_gain(self):
        report = year([sale(3000)], tax.YearInputs(stock_loss_carry=1500))
        self.assertEqual((report.stock_carry_used, report.stock_carry_out), (1500, 0))
        self.assertAlmostEqual(report.taxable, 500)

    def test_a_carry_larger_than_the_gain_stays_partly_unused(self):
        report = year([sale(400)], tax.YearInputs(stock_loss_carry=1500))
        self.assertEqual((report.stock_carry_used, report.stock_carry_out), (400, 1100))
        self.assertEqual(report.tax, 0)

    def test_a_carry_is_not_used_up_by_a_loss_year(self):
        report = year([sale(-300)], tax.YearInputs(stock_loss_carry=1000))
        self.assertEqual((report.stock_carry_used, report.stock_carry_out), (0, 1300))

    def test_the_general_carry_offsets_only_other_income(self):
        report = year([sale(3000)], tax.YearInputs(other_income=400, general_loss_carry=1000))
        self.assertEqual((report.general_carry_used, report.general_carry_out), (400, 600))
        self.assertAlmostEqual(report.subtotal, 3000)

    def test_an_allowance_used_elsewhere_reduces_what_is_left(self):
        report = year([sale(1500)], tax.YearInputs(allowance_elsewhere=800))
        self.assertEqual((report.allowance_available, report.allowance_applied), (200, 200))
        self.assertAlmostEqual(report.taxable, 1300)

    def test_more_used_elsewhere_than_available_is_capped_at_zero(self):
        report = year([sale(100)], tax.YearInputs(allowance_elsewhere=5000))
        self.assertEqual((report.allowance_available, report.taxable), (0, 100))

    def test_no_sales_still_gives_a_report(self):
        report = year([], years=[Y])
        self.assertEqual((report.tax, report.proceeds, report.sales), (0, 0, []))
        self.assertEqual(report.allowance_left, 1000)

    def test_the_extra_tax_from_sales_leaves_out_the_tax_on_other_income(self):
        inputs = tax.YearInputs(other_income=3000)
        report = year([sale(2000)], inputs)
        only_other = year([], inputs, years=[Y])
        self.assertAlmostEqual(report.tax_on_sales, round(report.tax - only_other.tax, 2))
        self.assertGreater(only_other.tax, 0)

    def test_the_net_proceeds_are_proceeds_minus_the_extra_tax(self):
        report = year([sale(3000, cost=1000)])
        self.assertEqual(report.proceeds, 4000)
        self.assertAlmostEqual(report.net_proceeds, 4000 - 527.5)

    def test_sales_of_other_years_are_not_in_the_report(self):
        report = tax.compute_years([sale(3000), sale(5000, 2, day=dt.date(Y - 1, 6, 1))], {}, tax.Settings())[Y]
        self.assertEqual(report.net_stock, 3000)

    def test_rounding_keeps_the_total_equal_to_the_sum_of_its_parts(self):
        report = year([sale(2345.67)], settings=tax.Settings(0.08))
        self.assertAlmostEqual(report.tax, report.income_tax + report.soli + report.church)


class CarryChainTests(unittest.TestCase):
    def test_a_loss_year_is_carried_into_the_next_year_automatically(self):
        sales = [sale(-2000, 1, day=dt.date(Y - 1, 5, 1)), sale(3000, 2)]
        reports = tax.compute_years(sales, {}, tax.Settings())
        self.assertEqual(reports[Y - 1].stock_carry_out, 2000)
        self.assertEqual((reports[Y].stock_carry_in, reports[Y].stock_carry_used, reports[Y].stock_carry_auto),
                         (2000, 2000, True))
        self.assertAlmostEqual(reports[Y].taxable, 0)

    def test_the_carry_runs_through_a_year_without_sales(self):
        sales = [sale(-2000, 1, day=dt.date(Y - 2, 5, 1)), sale(3000, 2)]
        reports = tax.compute_years(sales, {}, tax.Settings())
        self.assertEqual(reports[Y - 1].stock_carry_out, 2000)
        self.assertEqual(reports[Y].stock_carry_used, 2000)

    def test_a_manual_carry_replaces_the_computed_one(self):
        sales = [sale(-2000, 1, day=dt.date(Y - 1, 5, 1)), sale(3000, 2)]
        reports = tax.compute_years(sales, {Y: tax.YearInputs(stock_loss_carry=500)}, tax.Settings())
        self.assertEqual((reports[Y].stock_carry_in, reports[Y].stock_carry_auto), (500, False))

    def test_a_manual_zero_means_no_carry(self):
        sales = [sale(-2000, 1, day=dt.date(Y - 1, 5, 1)), sale(3000, 2)]
        reports = tax.compute_years(sales, {Y: tax.YearInputs(stock_loss_carry=0.0)}, tax.Settings())
        self.assertEqual(reports[Y].stock_carry_used, 0)

    def test_the_chain_starts_in_the_first_year_with_sales(self):
        reports = tax.compute_years([sale(100, 1, day=dt.date(Y - 3, 1, 1))], {}, tax.Settings())
        self.assertEqual(sorted(reports), [Y - 3, Y - 2, Y - 1, Y])


class StepTests(unittest.TestCase):
    def steps(self, *args, **kwargs):
        return {s.key: s for s in year(*args, **kwargs).steps}

    def test_every_step_names_its_origin(self):
        report = year([sale(3000)], tax.YearInputs(other_income=100, allowance_elsewhere=50, stock_loss_carry=10))
        for step in report.steps:
            self.assertIn(step.source, (tax.CALCULATED, tax.MANUAL, tax.ESTIMATED))
        origins = {s.key: s.source for s in report.steps}
        self.assertEqual(origins["gains"], tax.CALCULATED)
        self.assertEqual(origins["other"], tax.MANUAL)
        self.assertEqual(origins["stock_carry"], tax.MANUAL)
        self.assertEqual(origins["allowance"], tax.MANUAL)
        self.assertEqual(origins["income_tax"], tax.ESTIMATED)
        self.assertEqual(origins["tax"], tax.ESTIMATED)

    def test_an_automatic_carry_is_calculated_not_manual(self):
        sales = [sale(-2000, 1, day=dt.date(Y - 1, 5, 1)), sale(3000, 2)]
        step = {s.key: s for s in tax.compute_years(sales, {}, tax.Settings())[Y].steps}["stock_carry"]
        self.assertEqual(step.source, tax.CALCULATED)
        self.assertIn("aus dem Vorjahr berechnet", step.note)

    def test_the_steps_follow_the_calculation_and_add_up(self):
        report = year([sale(3000, 1), sale(-1000, 2)], tax.YearInputs(other_income=200))
        keys = [s.key for s in report.steps]
        self.assertEqual(keys, ["gains", "losses", "net_stock", "other", "subtotal", "allowance", "taxable",
                                "income_tax", "soli", "tax"])
        values = {s.key: s.value for s in report.steps}
        self.assertAlmostEqual(values["gains"] + values["losses"], values["net_stock"])
        self.assertAlmostEqual(values["net_stock"] + values["other"], values["subtotal"])
        self.assertAlmostEqual(values["subtotal"] + values["allowance"], values["taxable"])
        self.assertAlmostEqual(values["income_tax"] + values["soli"], values["tax"])

    def test_optional_steps_appear_only_when_they_matter(self):
        keys = [s.key for s in year([sale(100)]).steps]
        for missing in ("stock_carry", "other", "general_carry", "church"):
            self.assertNotIn(missing, keys)
        self.assertIn("church", [s.key for s in year([sale(100)], settings=tax.Settings(0.09)).steps])

    def test_the_allowance_note_explains_the_remainder(self):
        step = self.steps([sale(300)], tax.YearInputs(allowance_elsewhere=200))["allowance"]
        self.assertIn("1000 € minus 200.00 € anderswo verbraucht", step.note)
        self.assertIn("500.00 € bleiben übrig", step.note)

    def test_every_term_of_a_step_exists_in_the_glossary(self):
        report = year([sale(3000), sale(-100, 2)], tax.YearInputs(other_income=1, general_loss_carry=1,
                                                                   stock_loss_carry=1), tax.Settings(0.09))
        for step in report.steps:
            if step.term:
                self.assertIn(step.term, glossary.GLOSSARY, step.key)

    def test_the_income_tax_label_shows_the_effective_rate(self):
        self.assertEqual(self.steps([sale(100)])["income_tax"].label, "Abgeltungsteuer (25.00 %)")
        self.assertEqual(self.steps([sale(100)], settings=tax.Settings(0.09))["income_tax"].label,
                         "Abgeltungsteuer (24.45 %)")


class ImpactTests(unittest.TestCase):
    def test_a_sale_inside_the_allowance_costs_nothing_and_uses_it_up(self):
        impact = tax.sale_impact([sale(400, 1)], 1, {}, tax.Settings())
        self.assertEqual((impact.tax, impact.allowance_before, impact.allowance_after), (0, 1000, 600))

    def test_a_sale_beyond_the_allowance_costs_tax(self):
        impact = tax.sale_impact([sale(500, 1, day=dt.date(Y, 2, 1)), sale(1500, 2, day=dt.date(Y, 3, 1))], 2, {},
                                 tax.Settings())
        self.assertEqual((impact.allowance_before, impact.allowance_after), (500, 0))
        self.assertEqual(impact.tax, round(1000 * 0.26375, 2))

    def test_a_loss_lowers_the_tax_of_the_year(self):
        impact = tax.sale_impact([sale(3000, 1), sale(-1000, 2)], 2, {}, tax.Settings())
        self.assertLess(impact.tax, 0)

    def test_an_unknown_sale_gives_nothing(self):
        self.assertIsNone(tax.sale_impact([sale(100, 1)], 99, {}, tax.Settings()))


class ConversionTests(unittest.TestCase):
    def setUp(self):
        self.sale = ledger.Sale(7, dt.date(Y, 6, 1), 10, 1500.0, 1000.0,
                                (ledger.Piece(dt.date(Y - 1, 1, 1), 6, 400.0), ledger.Piece(dt.date(Y, 1, 1), 4, 600.0, True)))

    def rate(self, table):
        return lambda currency, day: table.get((currency, day))

    def test_every_part_uses_the_rate_of_its_own_day(self):
        table = {("USD", dt.date(Y, 6, 1)): 0.9, ("USD", dt.date(Y - 1, 1, 1)): 0.8, ("USD", dt.date(Y, 1, 1)): 0.95}
        figures = tax.convert_sale("AAPL", self.sale, "USD", self.rate(table))
        self.assertAlmostEqual(figures.proceeds, 1500 * 0.9)
        self.assertAlmostEqual(figures.cost, 400 * 0.8 + 600 * 0.95)
        self.assertEqual((figures.symbol, figures.transaction_id, figures.day), ("AAPL", 7, dt.date(Y, 6, 1)))

    def test_an_opening_part_marks_the_sale_as_estimated(self):
        table = {("USD", d): 1.0 for d in (dt.date(Y, 6, 1), dt.date(Y - 1, 1, 1), dt.date(Y, 1, 1))}
        self.assertTrue(tax.convert_sale("AAPL", self.sale, "USD", self.rate(table)).estimated)
        plain = ledger.Sale(8, dt.date(Y, 6, 1), 1, 10.0, 5.0, (ledger.Piece(dt.date(Y - 1, 1, 1), 1, 5.0),))
        self.assertFalse(tax.convert_sale("AAPL", plain, "USD", self.rate(table)).estimated)

    def test_a_missing_rate_gives_none_instead_of_a_wrong_number(self):
        self.assertIsNone(tax.convert_sale("AAPL", self.sale, "USD", self.rate({("USD", dt.date(Y, 6, 1)): 0.9})))
        self.assertIsNone(tax.convert_sale("AAPL", self.sale, "USD", self.rate({})))

    def test_euro_stocks_convert_one_to_one(self):
        fx = fx_module.FxTable()
        rate = tax.eur_rates(fx)
        self.assertEqual(rate("EUR", dt.date(Y, 1, 1)), 1.0)

    def test_rates_come_from_the_fx_table_in_euro_mode(self):
        old = fx_module.BASE
        self.addCleanup(fx_module.set_base, old)
        fx_module.set_base("EUR")
        fx = fx_module.FxTable({"USD": {dt.date(Y, 1, 1): 0.9}, "GBP": {dt.date(Y, 1, 1): 1.2}})
        rate = tax.eur_rates(fx)
        self.assertEqual(rate("USD", dt.date(Y, 1, 1)), 0.9)
        self.assertAlmostEqual(rate("GBp", dt.date(Y, 1, 1)), 0.012)  # Pence
        self.assertIsNone(rate("CHF", dt.date(Y, 1, 1)))

    def test_with_a_dollar_base_the_rates_go_through_the_euro_rate(self):
        old = fx_module.BASE
        self.addCleanup(fx_module.set_base, old)
        fx_module.set_base("USD")
        fx = fx_module.FxTable({"EUR": {dt.date(Y, 1, 1): 1.25}, "GBP": {dt.date(Y, 1, 1): 1.5}})
        rate = tax.eur_rates(fx)
        self.assertAlmostEqual(rate("USD", dt.date(Y, 1, 1)), 0.8)
        self.assertAlmostEqual(rate("GBP", dt.date(Y, 1, 1)), 1.2)
        self.assertEqual(rate("EUR", dt.date(Y, 1, 1)), 1.0)
        self.assertIsNone(rate("USD", dt.date(Y, 1, 1)) if False else tax.eur_rates(fx_module.FxTable())("USD", dt.date(Y, 1, 1)))


class ControllerTaxTests(AppTestCase):
    def setUp(self):
        super().setUp()
        old = fx_module.BASE
        self.addCleanup(fx_module.set_base, old)
        fx_module.set_base("EUR")
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "EUR"}

    def trade(self, kind, shares, price, day, fee=0.0, symbol="AAPL"):
        return self.ctl.record_trade(symbol, kind, shares, price, fee, day)

    def test_settings_default_and_roundtrip(self):
        self.assertEqual(self.ctl.tax_settings(), tax.Settings(0.0, False))
        self.ctl.set_tax_settings(0.09, True)
        self.assertEqual(self.ctl.tax_settings(), tax.Settings(0.09, True))

    def test_a_wrong_church_rate_is_refused(self):
        with self.assertRaises(ValueError):
            self.ctl.set_tax_settings(0.5, False)
        self.assertEqual(self.ctl.tax_settings(), tax.Settings())

    def test_settings_survive_a_restart(self):
        self.ctl.set_tax_settings(0.08, False)
        self.assertEqual(w.Controller.tax_settings(self.ctl), tax.Settings(0.08, False))
        again = type(self.ctl.store)(self.ctl.store.db.execute("PRAGMA database_list").fetchone()[2])
        self.addCleanup(again.close)
        self.assertIn("0.08", again.meta("tax_settings"))

    def test_broken_stored_settings_fall_back_to_the_defaults(self):
        self.ctl.store.set_meta("tax_settings", "{kaputt")
        self.assertEqual(self.ctl.tax_settings(), tax.Settings())
        self.ctl.store.set_meta("tax_settings", '{"church_rate": 0.77, "joint": 1}')
        self.assertEqual(self.ctl.tax_settings(), tax.Settings(0.0, True))

    def test_inputs_roundtrip_per_year_and_notify(self):
        seen = []
        self.ctl.tax_changed.connect(lambda: seen.append(1))
        self.ctl.set_tax_inputs(Y, 120.0, 30.0, None, 5.0)
        self.assertEqual(self.ctl.tax_inputs(Y), tax.YearInputs(120.0, 30.0, None, 5.0))
        self.assertEqual(self.ctl.tax_inputs(Y - 1), tax.YearInputs())
        self.assertEqual(seen, [1])

    def test_a_given_carry_of_zero_differs_from_an_automatic_one(self):
        self.ctl.set_tax_inputs(Y, 0.0, 0.0, 0.0, 0.0)
        self.assertEqual(self.ctl.tax_inputs(Y).stock_loss_carry, 0.0)
        self.ctl.set_tax_inputs(Y, 0.0, 0.0, None, 0.0)
        self.assertIsNone(self.ctl.tax_inputs(Y).stock_loss_carry)

    def test_negative_inputs_are_refused(self):
        with self.assertRaises(ValueError):
            self.ctl.set_tax_inputs(Y, -1.0, 0.0, None, 0.0)
        with self.assertRaises(ValueError):
            self.ctl.set_tax_inputs(Y, 0.0, 0.0, -5.0, 0.0)
        self.assertEqual(self.ctl.tax_inputs(Y), tax.YearInputs())

    def test_broken_stored_inputs_are_ignored(self):
        self.ctl.store.set_meta(f"tax_year:{Y}", '{"other_income": "viel", "allowance_elsewhere": -3}')
        self.assertEqual(self.ctl.tax_inputs(Y), tax.YearInputs())

    def test_a_sale_in_euro_gives_the_gain_with_fees(self):
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5), fee=5.0)
        self.trade("sell", 10, 150.0, dt.date(Y, 3, 5), fee=5.0)
        sales, notes = self.ctl.tax_sales()
        [figures] = sales
        self.assertEqual((figures.proceeds, figures.cost, figures.gain), (1495.0, 1005.0, 490.0))
        self.assertEqual(notes, [])

    def test_a_sale_in_dollars_is_converted_with_the_rates_of_both_days(self):
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "USD"}
        self.ctl.fx.add_history("USD", {dt.date(Y, 1, 5): 0.8, dt.date(Y, 3, 5): 0.9})
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5))
        self.trade("sell", 10, 150.0, dt.date(Y, 3, 5))
        [figures] = self.ctl.tax_sales()[0]
        self.assertAlmostEqual(figures.cost, 1000 * 0.8)
        self.assertAlmostEqual(figures.proceeds, 1500 * 0.9)

    def test_a_missing_rate_is_named_and_the_sale_is_left_out(self):
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "CHF"}
        self.ctl.fx.history.pop("CHF", None)
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5))
        self.trade("sell", 10, 150.0, dt.date(Y, 3, 5))
        sales, notes = self.ctl.tax_sales()
        self.assertEqual(sales, [])
        self.assertEqual(notes, ["Wechselkurs fehlt, Verkäufe nicht enthalten: AAPL"])

    def test_an_unknown_currency_is_named_too(self):
        self.ctl.quotes.pop("AAPL")
        self.ctl.instruments.pop("AAPL", None)
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5))
        self.trade("sell", 10, 150.0, dt.date(Y, 3, 5))
        self.assertEqual(self.ctl.tax_sales()[1], ["Währung unbekannt, Verkäufe nicht enthalten: AAPL"])

    def test_opening_stock_is_pointed_out_as_estimated(self):
        self.ctl.start_position("AAPL", 10, 20.0)
        self.trade("sell", 10, 150.0, dt.date.today())
        sales, notes = self.ctl.tax_sales()
        self.assertTrue(sales[0].estimated)
        self.assertTrue(any("Startbestand" in n for n in notes))

    def test_carried_over_realized_gains_are_pointed_out(self):
        self.ctl.opening = {"AAPL": 123.0}
        self.assertTrue(any("keinem Jahr" in n for n in self.ctl.tax_sales()[1]))

    def test_the_years_are_those_with_sales_plus_the_current_one_newest_first(self):
        self.trade("buy", 10, 100.0, dt.date(Y - 2, 1, 5))
        self.trade("sell", 5, 150.0, dt.date(Y - 2, 3, 5))
        self.assertEqual(self.ctl.tax_years(), sorted({Y - 2, dt.date.today().year}, reverse=True))

    def test_the_report_uses_settings_inputs_and_the_carry_of_the_year_before(self):
        self.trade("buy", 10, 100.0, dt.date(Y - 1, 1, 5))
        self.trade("sell", 10, 50.0, dt.date(Y - 1, 3, 5))          # Verlust 500
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5))
        self.trade("sell", 10, 400.0, dt.date(Y, 3, 5))             # Gewinn 3000
        self.ctl.set_tax_settings(0.0, False)
        report = self.ctl.tax_report(Y)
        self.assertEqual((report.net_stock, report.stock_carry_in, report.stock_carry_used), (3000, 500, 500))
        self.assertAlmostEqual(report.taxable, 1500)
        self.ctl.set_tax_inputs(Y, 0.0, 400.0, None, 0.0)
        self.assertAlmostEqual(self.ctl.tax_report(Y).taxable, 1900)

    def test_the_report_carries_the_notes(self):
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5))
        self.trade("sell", 5, 150.0, dt.date(Y, 3, 5))
        self.ctl.opening = {"AAPL": 5.0}
        self.assertTrue(self.ctl.tax_report(Y).warnings)

    def test_recording_a_sale_returns_its_id_and_the_impact_is_known(self):
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5))
        sold = self.trade("sell", 10, 400.0, dt.date(Y, 3, 5))
        self.assertIsInstance(sold, int)
        impact = self.ctl.tax_impact(sold)
        self.assertEqual((impact.allowance_before, impact.allowance_after), (1000, 0))
        self.assertEqual(impact.tax, round(2000 * 0.26375, 2))

    def test_the_impact_of_an_unconvertible_sale_is_none(self):
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "CHF"}
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5))
        sold = self.trade("sell", 10, 400.0, dt.date(Y, 3, 5))
        self.ctl.fx.history.pop("CHF", None)
        self.assertIsNone(self.ctl.tax_impact(sold))

    def test_a_dollar_base_gives_euro_figures(self):
        fx_module.set_base("USD")
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "USD"}
        self.ctl.fx.add_history("EUR", {dt.date(Y, 1, 5): 1.25, dt.date(Y, 3, 5): 1.25})
        self.trade("buy", 10, 100.0, dt.date(Y, 1, 5))
        self.trade("sell", 10, 150.0, dt.date(Y, 3, 5))
        [figures] = self.ctl.tax_sales()[0]
        self.assertAlmostEqual(figures.cost, 1000 / 1.25)
        self.assertAlmostEqual(figures.proceeds, 1500 / 1.25)


class TaxWindowTests(AppTestCase):
    def setUp(self):
        super().setUp()
        old = fx_module.BASE
        self.addCleanup(fx_module.set_base, old)
        fx_module.set_base("EUR")
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "EUR"}
        today = dt.date.today()
        self.ctl.record_trade("AAPL", "buy", 10, 100.0, 0.0, today - dt.timedelta(days=60))
        self.sold = self.ctl.record_trade("AAPL", "sell", 10, 400.0, 0.0, today - dt.timedelta(days=30))
        self.main = w.MainWindow(self.ctl)
        self.addCleanup(lambda: [win.close() for win in list(w.TAX_WINDOWS.values())])

    def open(self, year=None):
        w.open_tax(self.ctl, year)
        return w.TAX_WINDOWS["window"]

    def texts(self, window):
        w.QApplication.processEvents()  # neue Widgets in einem sichtbaren Fenster werden erst danach eingeblendet
        return [label.text() for label in window.findChildren(w.QLabel) if not label.isHidden()]

    def test_it_opens_once_docks_and_frees_its_slot(self):
        window = self.open()
        w.open_tax(self.ctl)
        self.assertEqual(len(w.TAX_WINDOWS), 1)
        self.assertIn(window, w.Dock.windows)
        window.close()
        self.assertNotIn("window", w.TAX_WINDOWS)
        self.assertNotIn(window, w.Dock.windows)

    def test_the_tiles_show_tax_extra_tax_allowance_and_net_proceeds(self):
        window = self.open()
        report = window.report
        self.assertEqual(window.tax_tile.value.text(), tax.eur(report.tax))
        self.assertEqual(window.sales_tile.value.text(), tax.eur(report.tax_on_sales))
        self.assertEqual(window.allowance_tile.value.text(), "0.00 €")
        self.assertEqual(window.net_tile.value.text(), tax.eur(report.net_proceeds))
        self.assertEqual(report.tax, round(2000 * 0.26375, 2))

    def test_every_calculation_step_is_a_row_with_its_origin(self):
        window = self.open()
        rows = window.findChildren(w.TaxStepRow)
        self.assertEqual([r.step.key for r in rows], [s.key for s in window.report.steps])
        pills = {r.step.key: r.source.text() for r in rows}
        self.assertEqual((pills["gains"], pills["allowance"], pills["tax"]), ("berechnet", "berechnet", "geschätzt"))

    def test_step_labels_explain_themselves(self):
        window = self.open()
        rows = {r.step.key: r for r in window.findChildren(w.TaxStepRow)}
        self.assertEqual(rows["allowance"].label._term_anchor.key, "sparer_pauschbetrag")
        self.assertEqual(rows["income_tax"].label._term_anchor.key, "abgeltungsteuer")

    def test_the_sales_of_the_year_are_listed_with_the_result(self):
        window = self.open()
        [row] = window.findChildren(w.TaxSaleRow)
        self.assertEqual(row.gain.text(), "+3,000.00 €")
        self.assertIn("AAPL · 10 Stück", row.title.text())
        self.assertTrue(row.flag.isHidden())

    def test_assumptions_and_the_disclaimer_are_shown(self):
        window = self.open()
        shown = " ".join(self.texts(window))
        self.assertIn("keine Steuerberatung", shown)
        self.assertIn("Quellensteuer", shown)

    def test_another_year_can_be_selected(self):
        window = self.open()
        previous = dt.date.today().year - 1
        self.ctl.record_trade("MSFT", "buy", 4, 50.0, 0.0, dt.date(previous, 1, 5))
        self.ctl.quotes["MSFT"] = {**QUOTE, "currency": "EUR"}
        self.ctl.record_trade("MSFT", "sell", 4, 75.0, 0.0, dt.date(previous, 2, 5))
        self.assertTrue(wait_until(lambda: previous in window.year_buttons))
        window.year_buttons[previous].click()
        self.assertEqual((window.year, window.report.year), (previous, previous))
        [row] = window.findChildren(w.TaxSaleRow)
        self.assertEqual(row.sale.symbol, "MSFT")
        self.assertEqual(row.gain.text(), "+100.00 €")
        window.year_buttons[dt.date.today().year].click()
        self.assertEqual([r.sale.symbol for r in window.findChildren(w.TaxSaleRow)], ["AAPL"])

    def test_a_year_without_sales_says_so(self):
        window = self.open()
        window.select_year(dt.date.today().year - 5)
        self.assertTrue(any("Keine Verkäufe im Jahr" in t for t in self.texts(window)))
        self.assertEqual(window.findChildren(w.TaxSaleRow), [])

    def test_opening_for_a_year_selects_it(self):
        window = self.open()
        w.open_tax(self.ctl, dt.date.today().year)
        self.assertEqual(window.year, dt.date.today().year)

    def test_the_settings_dialog_changes_the_calculation_live(self):
        window = self.open()
        before = window.report.tax
        driver = DialogDriver(lambda d: (d.entries[0].setCurrentIndex(2), d.submit()))
        window.settings_button.click()
        driver.check()
        self.assertEqual(self.ctl.tax_settings().church_rate, 0.09)
        self.assertTrue(wait_until(lambda: window.report.tax > before))
        self.assertIn("Kirchensteuer (9 % der Steuer)", " ".join(self.texts(window)).replace("\n", " "))

    def test_the_inputs_dialog_saves_for_the_shown_year_and_accepts_automatic(self):
        window = self.open()
        seen = []

        def fill(dialog):
            seen.append(dialog.entries[2].text())
            dialog.entries[0].setText("500,00")
            dialog.entries[1].setText("250")
            dialog.entries[2].setText("automatisch")
            dialog.submit()
        driver = DialogDriver(fill)
        window.inputs_button.click()
        driver.check()
        self.assertEqual(seen, ["automatisch"])
        self.assertEqual(self.ctl.tax_inputs(window.year), tax.YearInputs(500.0, 250.0, None, 0.0))
        self.assertTrue(wait_until(lambda: any(r.step.key == "other" for r in window.findChildren(w.TaxStepRow))))

    def test_a_negative_input_is_refused_in_the_dialog(self):
        window = self.open()
        errors = []

        def fill(dialog):
            dialog.entries[0].setText("-5")
            dialog.submit()
            errors.append(dialog.error.text())
        driver = DialogDriver(fill)
        window.inputs_button.click()
        driver.check()
        self.assertIn("nicht negativ", errors[0])
        self.assertEqual(self.ctl.tax_inputs(window.year), tax.YearInputs())

    def test_a_new_sale_updates_the_open_window(self):
        window = self.open()
        before = len(window.findChildren(w.TaxSaleRow))
        self.ctl.record_trade("AAPL", "buy", 10, 100.0, 0.0, dt.date.today() - dt.timedelta(days=20))
        self.ctl.record_trade("AAPL", "sell", 10, 110.0, 0.0, dt.date.today() - dt.timedelta(days=10))
        self.assertTrue(wait_until(lambda: len(window.findChildren(w.TaxSaleRow)) == before + 1))

    def test_a_missing_rate_shows_as_a_note(self):
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "CHF"}
        self.ctl.fx.history.pop("CHF", None)
        window = self.open()
        self.assertTrue(any("Wechselkurs fehlt" in t for t in self.texts(window)))
        self.assertEqual(window.findChildren(w.TaxSaleRow), [])

    def test_an_estimated_sale_is_flagged(self):
        self.ctl.start_position("MSFT", 5, 10.0)
        self.ctl.quotes["MSFT"] = {**QUOTE, "currency": "EUR"}
        self.ctl.record_trade("MSFT", "sell", 5, 120.0, 0.0, dt.date.today())
        window = self.open()
        rows = {r.sale.symbol: r for r in window.findChildren(w.TaxSaleRow)}
        self.assertFalse(rows["MSFT"].flag.isHidden())
        self.assertTrue(rows["AAPL"].flag.isHidden())

    def test_the_portfolio_has_a_button_for_it(self):
        portfolio = w.PortfolioWindow(self.ctl)
        self.addCleanup(portfolio.close)
        portfolio.tax_button.click()
        self.assertIn("window", w.TAX_WINDOWS)

    def test_it_can_be_pinned_and_comes_back(self):
        window = self.open()
        window.pin_button.click()
        self.assertTrue(self.ctl.is_pinned("tax"))
        window.close()
        w.restore_pinned(self.ctl, self.main)
        self.assertIn("window", w.TAX_WINDOWS)

    def test_closing_disconnects_the_window(self):
        window = self.open()
        window.close()
        self.ctl.tax_changed.emit()
        self.ctl.ledger_changed.emit()
        self.ctl.changed.emit()


class AfterSaleTests(AppTestCase):
    def setUp(self):
        super().setUp()
        old = fx_module.BASE
        self.addCleanup(fx_module.set_base, old)
        fx_module.set_base("EUR")
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "EUR"}
        self.ctl.record_trade("AAPL", "buy", 10, 100.0, 0.0, dt.date.today() - dt.timedelta(days=60))
        self.addCleanup(lambda: [win.close() for win in list(w.TAX_WINDOWS.values())])

    def hint(self, transaction_id):
        """Der Text des Steuerhinweises zu einem Verkauf."""
        texts = []
        driver = DialogDriver(lambda d: (texts.append("\n".join(l.text() for l in d.findChildren(w.QLabel))), d.reject()))
        w.tax_after_sale(self.ctl, transaction_id)
        driver.check()
        return texts[0] if texts else None

    def test_selling_in_the_dialog_leads_to_the_tax_hint_for_that_sale(self):
        with mock.patch.object(w, "tax_after_sale") as hint, mock.patch.object(w, "show_message"):
            driver = DialogDriver(lambda d: (d.entries[0].setText("10"), d.entries[1].setText("400"), d.submit()))
            w.trade_dialog(self.ctl, "AAPL", "sell")
            driver.check()
        [(ctl, transaction_id)] = [c.args for c in hint.call_args_list]
        self.assertIs(ctl, self.ctl)
        sale = [t for t in self.ctl.transactions["AAPL"] if t.kind == "sell"][0]
        self.assertEqual(transaction_id, sale.id)

    def test_cancelling_the_sale_shows_no_hint(self):
        with mock.patch.object(w, "tax_after_sale") as hint:
            driver = DialogDriver(lambda d: d.reject())
            w.trade_dialog(self.ctl, "AAPL", "sell")
            driver.check()
        hint.assert_not_called()

    def test_a_refused_sale_shows_no_hint(self):
        with mock.patch.object(w, "tax_after_sale") as hint:
            driver = DialogDriver(lambda d: (d.entries[0].setText("99"), d.entries[1].setText("400"), d.submit()))
            w.trade_dialog(self.ctl, "AAPL", "sell")  # mehr Stück als vorhanden: der Dialog bleibt offen, die Notbremse schließt ihn
        hint.assert_not_called()

    def test_after_a_sale_the_tax_effect_is_shown(self):
        sold = self.ctl.record_trade("AAPL", "sell", 10, 400.0, 0.0, dt.date.today())
        text = self.hint(sold)
        self.assertIn("Gewinn 3,000.00 €", text)
        self.assertIn("Sparer-Pauschbetrag übrig: 1,000.00 € → 0.00 €", text)
        self.assertIn("Zusätzliche Steuer durch diesen Verkauf (Schätzung): 527.50 €", text)
        self.assertIn("Keine Steuerberatung", text)

    def test_a_loss_is_called_a_loss(self):
        sold = self.ctl.record_trade("AAPL", "sell", 10, 50.0, 0.0, dt.date.today())
        self.assertIn("Verlust 500.00 €", self.hint(sold))

    def test_the_button_of_the_dialog_opens_the_report_for_the_year_of_the_sale(self):
        sold = self.ctl.record_trade("AAPL", "sell", 10, 400.0, 0.0, dt.date.today())
        driver = DialogDriver(lambda d: d.submit())
        w.tax_after_sale(self.ctl, sold)
        driver.check()
        self.assertEqual(w.TAX_WINDOWS["window"].year, dt.date.today().year)

    def test_nothing_is_shown_when_the_sale_cannot_be_converted(self):
        self.ctl.quotes["AAPL"] = {**QUOTE, "currency": "CHF"}
        self.ctl.fx.history.pop("CHF", None)
        sold = self.ctl.record_trade("AAPL", "sell", 10, 400.0, 0.0, dt.date.today())
        self.assertIsNone(w.tax_after_sale(self.ctl, sold))

    def test_an_estimated_cost_is_warned_about(self):
        self.ctl.record_trade("AAPL", "sell", 10, 400.0, 0.0, dt.date.today() - dt.timedelta(days=1))
        self.ctl.start_position("AAPL", 5, 10.0)
        sold = self.ctl.record_trade("AAPL", "sell", 5, 400.0, 0.0, dt.date.today())
        texts = []
        driver = DialogDriver(lambda d: (texts.append("\n".join(l.text() for l in d.findChildren(w.QLabel))), d.reject()))
        w.tax_after_sale(self.ctl, sold)
        driver.check()
        self.assertIn("Startbestand", texts[0])


class GlossaryTests(unittest.TestCase):
    def test_the_tax_terms_exist_with_formula_or_interpretation(self):
        for key in ("steuerbericht", "sparer_pauschbetrag", "abgeltungsteuer", "soli", "kirchensteuer",
                    "aktienverlusttopf", "verlustvortrag", "sonstige_kapitalertraege", "steuerpflichtiger_betrag",
                    "nettoerloes"):
            term = glossary.term(key)
            self.assertTrue(term.text and (term.formula or term.interpretation), key)

    def test_the_allowance_is_explained_with_both_amounts(self):
        term = glossary.term("sparer_pauschbetrag")
        self.assertIn("1.000", term.formula)
        self.assertIn("2.000", term.formula)


if __name__ == "__main__":
    unittest.main()
