"""Oberfläche: Fensteranordnung, Dialoge und Fokus, Menü, Karten, Aufblinken, Fenster, Beenden."""
import datetime as dt
import os
import re
import subprocess
import sys
import unittest
from unittest import mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

import stock_data as sd
import stock_widget as w
from tests.support import AppTestCase, DialogDriver, qapp, wait_until

TODAY = dt.date.today()
NUM = sd.parse_number
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def texts(widget):
    return [label.text() for label in widget.findChildren(QLabel)]


def reset_windows():
    for window in list(w.Dock.windows):
        window.close()
    w.Dock.windows.clear()
    w.TX_WINDOWS.clear()
    w.PIN["on"] = True


class DockTests(unittest.TestCase):
    def setUp(self):
        qapp()
        reset_windows()
        self.addCleanup(reset_windows)
        self.windows = []
        area = QApplication.primaryScreen().availableGeometry()
        self.right = area.right() + 1 - w.Dock.MARGIN
        self.bottom = area.bottom() + 1 - w.Dock.MARGIN
        self.left_edge = area.left()

    def window(self, width, height):
        widget = QWidget()
        widget.setFixedSize(width, height)
        self.windows.append(widget)
        w.Dock.add(widget)
        widget.show()
        return widget

    def test_first_window_sits_bottom_right(self):
        a = self.window(100, 200)
        self.assertEqual((a.x() + a.width(), a.y() + a.height()), (self.right, self.bottom))

    def test_next_windows_open_to_the_left_side_by_side_on_the_same_bottom(self):
        a, b, c = self.window(100, 200), self.window(150, 100), self.window(80, 300)
        self.assertEqual(b.x() + b.width(), a.x() - w.Dock.GAP)
        self.assertEqual(c.x() + c.width(), b.x() - w.Dock.GAP)
        for window in (a, b, c):
            self.assertEqual(window.y() + window.height(), self.bottom)

    def test_closing_a_window_closes_the_gap(self):
        a, b = self.window(100, 200), self.window(150, 100)
        w.Dock.remove(a)
        self.assertEqual(b.x() + b.width(), self.right)

    def test_adding_the_same_window_twice_keeps_one_slot(self):
        a, b = self.window(100, 200), self.window(150, 100)
        w.Dock.add(a)
        self.assertEqual(w.Dock.windows, [a, b])

    def test_windows_never_leave_the_screen_on_the_left(self):
        for _ in range(6):
            window = self.window(300, 100)
        self.assertGreaterEqual(window.x(), self.left_edge)

    def test_pin_toggle_keeps_order_and_sets_flag(self):
        a, b = self.window(100, 200), self.window(150, 100)
        w.PIN["on"] = False
        w.apply_pin()
        self.assertEqual(w.Dock.windows, [a, b])
        self.assertFalse(a.windowFlags() & Qt.WindowStaysOnTopHint)
        self.assertEqual(b.x() + b.width(), a.x() - w.Dock.GAP)
        w.PIN["on"] = True
        w.apply_pin()
        self.assertEqual(w.Dock.windows, [a, b])
        self.assertTrue(b.windowFlags() & Qt.WindowStaysOnTopHint)


class FieldDialogTests(unittest.TestCase):
    def setUp(self):
        qapp()
        reset_windows()
        self.addCleanup(reset_windows)

    def make(self, fields, apply=lambda values: None, **kwargs):
        return w.FieldDialog("Test", "Einleitung", fields, apply, **kwargs)

    def test_ok_passes_parsed_values_to_apply_and_returns_true(self):
        values = []
        dialog = self.make([("A", "", NUM), ("B", "2,5", NUM), ("Datum", "08.10.2026", sd.parse_date)],
                           values.extend)
        driver = DialogDriver(lambda d: (d.entries[0].setText("4"), d.submit()))
        self.assertTrue(dialog.run())
        driver.check()
        self.assertEqual(values, [4.0, 2.5, dt.date(2026, 10, 8)])

    def test_cancel_returns_false_and_does_not_apply(self):
        values = []
        dialog = self.make([("A", "1", NUM)], values.extend)
        driver = DialogDriver(lambda d: d.reject())
        self.assertFalse(dialog.run())
        driver.check()
        self.assertEqual(values, [])

    def test_invalid_number_shows_error_in_dialog_and_keeps_it_open(self):
        dialog = self.make([("A", "", NUM)])

        def check(d):
            d.entries[0].setText("abc")
            d.submit()
            self.assertIs(QApplication.activeModalWidget(), d)
            self.assertFalse(d.error.isHidden())
            self.assertIn("Zahl", d.error.text())

        driver = DialogDriver(check)
        self.assertFalse(dialog.run())
        driver.check()

    def test_error_from_apply_is_shown_and_user_can_correct_it(self):
        def apply(values):
            if values[0] < 0:
                raise ValueError("darf nicht negativ sein")

        dialog = self.make([("A", "", NUM)], apply)

        def check(d):
            d.entries[0].setText("-1")
            d.submit()
            self.assertIs(QApplication.activeModalWidget(), d)
            self.assertEqual(d.error.text(), "darf nicht negativ sein")
            d.entries[0].setText("1")
            d.submit()

        driver = DialogDriver(check)
        self.assertTrue(dialog.run())
        driver.check()

    def test_bad_date_is_reported(self):
        dialog = self.make([("Datum", "gestern", sd.parse_date)])

        def check(d):
            d.submit()
            self.assertIn("Datum", d.error.text())

        driver = DialogDriver(check)
        dialog.run()
        driver.check()

    def test_message_dialog_has_only_an_ok_button(self):
        def check(d):
            self.assertIn("Hinweistext", texts(d))
            buttons = [b.text() for b in d.findChildren(QPushButton)]
            self.assertIn("OK", buttons)
            self.assertNotIn("Abbrechen", buttons)

        driver = DialogDriver(check)
        w.show_message("Titel", "Hinweistext")
        driver.check()

    def test_dialog_opens_in_the_dock_left_of_other_windows(self):
        other = QWidget()
        other.setFixedSize(100, 100)
        w.Dock.add(other)
        other.show()
        dialog = self.make([("A", "", NUM)])

        def check(d):
            self.assertEqual(w.Dock.windows, [other, d])
            self.assertLess(d.x(), other.x())
            area = QApplication.primaryScreen().availableGeometry()
            self.assertEqual(d.y() + d.height(), area.bottom() + 1 - w.Dock.MARGIN)

        driver = DialogDriver(check)
        dialog.run()
        driver.check()
        self.assertEqual(w.Dock.windows, [other])

    def test_dialog_asks_the_window_system_for_activation(self):
        """Der Fokus im Dialog hängt unter Windows an der Fensteraktivierung, nicht nur am Feld."""
        dialog = self.make([("A", "", NUM)])
        with mock.patch.object(w.FieldDialog, "activateWindow") as activate:
            driver = DialogDriver(lambda d: None)
            dialog.run()
        driver.check()
        self.assertTrue(activate.called)

    def test_first_field_has_focus_so_typing_works_without_a_click(self):
        dialog = self.make([("Stück", "", NUM), ("Kurs", "5", NUM)])

        def check(d):
            self.assertIs(QApplication.focusWidget(), d.entries[0])
            QTest.keyClicks(QApplication.focusWidget(), "12")
            self.assertEqual(d.entries[0].text(), "12")

        driver = DialogDriver(check)
        dialog.run()
        driver.check()

    def test_prefilled_first_field_is_selected_so_typing_replaces_it(self):
        dialog = self.make([("Stück", "7", NUM)])

        def check(d):
            self.assertIs(QApplication.focusWidget(), d.entries[0])
            QTest.keyClicks(QApplication.focusWidget(), "3")
            self.assertEqual(d.entries[0].text(), "3")

        driver = DialogDriver(check)
        dialog.run()
        driver.check()

    def test_enter_in_a_field_confirms(self):
        dialog = self.make([("A", "", NUM)])
        driver = DialogDriver(lambda d: (QTest.keyClicks(QApplication.focusWidget(), "8"),
                                         QTest.keyClick(QApplication.focusWidget(), Qt.Key_Return)))
        self.assertTrue(dialog.run())
        driver.check()


class TradeDialogTests(AppTestCase):
    def fill(self, values):
        def callback(dialog):
            for entry, value in zip(dialog.entries, values):
                if value is not None:
                    entry.setText(value)
            dialog.submit()
        return callback

    def test_buy_dialog_defaults_and_stored_transaction(self):
        seen = []
        callback = self.fill(["5", "99,5", "1,25", None])

        def check(d):
            seen.append([e.text() for e in d.entries])
            callback(d)

        driver = DialogDriver(check)
        w.trade_dialog(self.ctl, "AAPL", "buy")
        driver.check()
        self.assertEqual(seen[0], ["", "100.00", "0", TODAY.strftime("%d.%m.%Y")])
        [tx] = self.ctl.transactions["AAPL"]
        self.assertEqual((tx.kind, tx.shares, tx.price, tx.fee, tx.day), ("buy", 5, 99.5, 1.25, TODAY))

    def test_buy_with_future_date_is_refused(self):
        future = (TODAY + dt.timedelta(days=2)).strftime("%d.%m.%Y")
        driver = DialogDriver(self.fill(["5", None, None, future]))
        w.trade_dialog(self.ctl, "AAPL", "buy")
        driver.check()
        self.assertNotIn("AAPL", self.ctl.transactions)

    def test_start_dialog_creates_position_from_profit(self):
        driver = DialogDriver(self.fill(["10", "25"]))
        w.trade_dialog(self.ctl, "AAPL", "start")
        driver.check()
        self.assertAlmostEqual(self.ctl.positions["AAPL"]["cost"], 80.0)

    def test_start_is_refused_when_a_position_exists(self):
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, TODAY)
        driver = DialogDriver(lambda d: self.assertTrue(any("schon eine Position" in t for t in texts(d))))
        w.trade_dialog(self.ctl, "AAPL", "start")
        driver.check()
        self.assertEqual(len(self.ctl.transactions["AAPL"]), 1)

    def test_sell_without_position_shows_a_message(self):
        driver = DialogDriver(lambda d: self.assertTrue(any("nichts zu verkaufen" in t for t in texts(d))))
        w.trade_dialog(self.ctl, "AAPL", "sell")
        driver.check()

    def test_missing_quote_shows_a_message_instead_of_a_dialog_with_blank_price(self):
        self.ctl.quotes.clear()
        driver = DialogDriver(lambda d: self.assertTrue(any("Kurs ist noch nicht geladen" in t for t in texts(d))))
        w.trade_dialog(self.ctl, "AAPL", "buy")
        driver.check()

    def test_overselling_shows_error_and_stores_nothing(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY - dt.timedelta(days=1))

        def check(d):
            d.entries[0].setText("11")
            d.submit()
            self.assertIs(QApplication.activeModalWidget(), d)
            self.assertIn("nur 10", d.error.text())

        driver = DialogDriver(check)
        w.trade_dialog(self.ctl, "AAPL", "sell")
        driver.check()
        self.assertEqual(len(self.ctl.transactions["AAPL"]), 1)

    def test_selling_everything_reports_the_realized_profit(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY - dt.timedelta(days=1))
        first = DialogDriver(self.fill(["10"]), delay=150)  # Verkaufskurs-Vorgabe: 100 -> +200
        second = DialogDriver(lambda d: self.assertTrue(
            any("+200.00 USD" in t for t in texts(d)) or any("Position geschlossen" in t for t in texts(d))),
            delay=900)
        w.trade_dialog(self.ctl, "AAPL", "sell")
        first.check()
        second.check()
        self.assertNotIn("AAPL", self.ctl.positions)
        self.assertAlmostEqual(self.ctl.realized["AAPL"], 200.0)

    def test_partial_sale_shows_no_closing_message(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY - dt.timedelta(days=1))
        driver = DialogDriver(self.fill(["4"]))
        w.trade_dialog(self.ctl, "AAPL", "sell")
        driver.check()
        self.assertAlmostEqual(self.ctl.positions["AAPL"]["shares"], 6)
        self.assertIsNone(QApplication.activeModalWidget())


class MainWindowTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.main = w.MainWindow(self.ctl)

    def test_one_card_per_symbol_in_watchlist_order(self):
        self.assertEqual(list(self.main.cards), ["AAPL", "MSFT", "DELL"])

    def test_card_shows_price_and_day_change(self):
        card = self.main.cards["AAPL"]
        self.assertEqual(card.price.text(), "100.00")
        self.assertEqual(card.day.text(), "▲ +1.50 %")

    def test_card_without_quote_shows_placeholder(self):
        self.ctl.quotes.pop("AAPL")
        self.ctl.changed.emit()
        card = self.main.cards["AAPL"]
        self.assertEqual((card.price.text(), card.day.text(), card.pl.text()), ("", "", ""))

    def test_negative_change_uses_down_arrow(self):
        self.ctl.quotes["AAPL"]["change_pct"] = -2.0
        self.ctl.changed.emit()
        self.assertEqual(self.main.cards["AAPL"].day.text(), "▼ -2.00 %")

    def test_card_shows_position_value_and_profit_only_when_held(self):
        card = self.main.cards["AAPL"]
        self.assertEqual((card.value.text(), card.pl.text(), card.amount.text()), ("", "", ""))
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY)
        self.assertEqual((card.value.text(), card.pl.text(), card.amount.text()),
                         ("1,000.00", "+25.00 %", "+200.00"))
        self.assertEqual(card.value.toolTip(), "10 Stück")
        self.assertEqual(self.main.cards["MSFT"].value.text(), "")

    def test_card_shows_next_event(self):
        self.ctl.events["AAPL"] = [(TODAY + dt.timedelta(days=5), "Ex-Dividende")]
        self.ctl.changed.emit()
        self.assertTrue(self.main.cards["AAPL"].event.text().startswith("Ex-Div."))
        self.ctl.events["MSFT"] = []
        self.ctl.changed.emit()
        self.assertEqual(self.main.cards["MSFT"].event.text(), "kein Termin")

    def symbols_on_screen(self):
        """Die Zeilen in der Reihenfolge, in der sie im Layout stehen."""
        cards = [self.main.list.itemAt(i).widget() for i in range(self.main.list.count())]
        return [c.symbol for c in cards if isinstance(c, w.StockCard)]

    def test_each_column_sorts_ascending_then_descending(self):
        self.ctl.quotes["AAPL"]["price"], self.ctl.quotes["MSFT"]["price"], self.ctl.quotes["DELL"]["price"] = 30, 10, 20
        self.ctl.changed.emit()
        self.main.heads["price"].click()
        self.assertEqual(self.symbols_on_screen(), ["MSFT", "DELL", "AAPL"])
        self.main.heads["price"].click()
        self.assertEqual(self.symbols_on_screen(), ["AAPL", "DELL", "MSFT"])
        self.main.heads["symbol"].click()
        self.assertEqual(self.symbols_on_screen(), ["AAPL", "DELL", "MSFT"])

    def test_empty_cells_sort_last_in_both_directions(self):
        self.ctl.record_trade("DELL", "buy", 10, 80, 0, TODAY)
        self.main.heads["pl"].click()
        self.assertEqual(self.symbols_on_screen()[0], "DELL")
        self.main.heads["pl"].click()
        self.assertEqual(self.symbols_on_screen()[0], "DELL")

    def test_sort_survives_refresh_and_marks_the_active_header(self):
        self.main.heads["symbol"].click()
        self.main.heads["symbol"].click()
        self.ctl.changed.emit()
        self.assertEqual(self.symbols_on_screen(), ["MSFT", "DELL", "AAPL"])
        self.assertTrue(self.main.heads["symbol"].text().endswith("▼"))
        self.assertEqual(self.main.heads["price"].text(), "Kurs")

    def test_window_is_exactly_as_wide_as_the_columns_need(self):
        self.main.show()
        for card in self.main.cards.values():
            self.assertLessEqual(card.minimumSizeHint().width(), self.main.area.viewport().width())
        before = self.main.width()
        self.ctl.events["AAPL"] = [(TODAY + dt.timedelta(days=5), "Ex-Dividende")]
        self.ctl.changed.emit()
        self.assertGreater(self.main.width(), before)
        for card in self.main.cards.values():
            self.assertLessEqual(card.minimumSizeHint().width(), self.main.area.viewport().width())
            self.assertGreaterEqual(card.event.width(), card.event.sizeHint().width())

    def test_removed_symbol_loses_its_card_and_empty_hint_appears_at_the_end(self):
        for symbol in list(self.ctl.symbols):
            self.ctl.remove(symbol)
        self.assertEqual(self.main.cards, {})
        self.assertFalse(self.main.empty.isHidden())

    def test_menu_without_position_offers_start_and_buy_but_no_sell(self):
        menu = self.main.build_menu("AAPL")  # Referenz halten, sonst räumt Qt das Menü sofort ab
        entries = [a.text() for a in menu.actions() if a.text()]
        self.assertEqual(entries, ["Details", "Startbestand festlegen …", "Aufstocken …",
                                   "Transaktionen …", "Entfernen"])

    def test_menu_with_position_offers_sell_but_no_start(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY)
        menu = self.main.build_menu("AAPL")
        entries = [a.text() for a in menu.actions() if a.text()]
        self.assertEqual(entries, ["Details", "Aufstocken …", "Verkleinern …", "Transaktionen …", "Entfernen"])

    def test_menu_remove_action_removes_symbol(self):
        menu = self.main.build_menu("AAPL")
        [remove] = [a for a in menu.actions() if a.text() == "Entfernen"]
        remove.trigger()
        self.assertTrue(wait_until(lambda: "AAPL" not in self.ctl.symbols))

    def test_menu_actions_run_only_after_the_menu_has_closed(self):
        """Regression: Ein Dialog, der noch im offenen Menü startet, bekommt den Tastaturfokus nicht."""
        menu = self.main.build_menu("AAPL")
        [buy] = [a for a in menu.actions() if a.text() == "Aufstocken …"]
        with mock.patch.object(w, "trade_dialog") as dialog:
            buy.trigger()
            self.assertFalse(dialog.called)  # noch nicht synchron im Menü-Klick
            self.assertTrue(wait_until(lambda: dialog.called))
        dialog.assert_called_once_with(self.ctl, "AAPL", "buy")

    def test_main_window_docks_bottom_right_and_leaves_dock_when_hidden(self):
        self.main.show_docked()
        area = QApplication.primaryScreen().availableGeometry()
        self.assertEqual(self.main.x() + self.main.width(), area.right() + 1 - w.Dock.MARGIN)
        self.assertEqual(self.main.y() + self.main.height(), area.bottom() + 1 - w.Dock.MARGIN)
        self.main.hide_docked()
        self.assertEqual(w.Dock.windows, [])
        self.assertFalse(self.main.isVisible())

    def test_all_windows_share_the_bottom_edge(self):
        self.main.show_docked()
        self.main.open_detail("AAPL")
        w.open_transactions(self.ctl, "AAPL")
        bottoms = {win.y() + win.height() for win in w.Dock.windows}
        self.assertEqual(len(w.Dock.windows), 3)
        self.assertEqual(len(bottoms), 1)

    def test_pin_toggle_applies_to_all_open_windows(self):
        self.main.show_docked()
        self.main.open_detail("AAPL")
        order = list(w.Dock.windows)
        self.main.pin.setChecked(False)
        self.main.toggle_pin()
        self.assertEqual(w.Dock.windows, order)
        self.assertFalse(any(win.windowFlags() & Qt.WindowStaysOnTopHint for win in order))


class StaleDisplayTests(AppTestCase):
    def setUp(self):
        super().setUp()
        now = dt.datetime.now()
        self.ctl.quote_times = {s: now for s in self.ctl.symbols}
        self.main = w.MainWindow(self.ctl)

    def make_stale(self, symbol="AAPL", error="offline"):
        self.ctl.quote_errors[symbol] = error
        self.ctl.changed.emit()

    def test_fresh_quote_is_not_marked_and_the_tooltip_names_time_and_source(self):
        card = self.main.cards["AAPL"]
        self.assertNotIn(w.AMBER, card.price.styleSheet())
        self.assertIn("Stand", card.price.toolTip())
        self.assertIn("Testquelle", card.price.toolTip())
        self.assertNotIn("Veraltet", card.price.toolTip())
        self.assertTrue(self.main.stale_note.isHidden())

    def test_failed_refresh_marks_the_price_amber_and_italic_with_the_reason(self):
        self.make_stale()
        card = self.main.cards["AAPL"]
        self.assertIn(w.AMBER, card.price.styleSheet())
        self.assertIn("italic", card.price.styleSheet())
        self.assertIn("Veraltet: letzter Abruf fehlgeschlagen (offline)", card.price.toolTip())
        self.assertEqual(card.price.text(), "100.00")  # der letzte Kurs bleibt sichtbar
        self.assertNotIn(w.AMBER, self.main.cards["MSFT"].price.styleSheet())

    def test_footer_counts_stale_quotes_and_lists_them_in_the_tooltip(self):
        self.make_stale("AAPL")
        self.assertFalse(self.main.stale_note.isHidden())
        self.assertEqual(self.main.stale_note.text(), "⚠ 1 Kurs veraltet")
        self.make_stale("MSFT", "kein Netz")
        self.assertEqual(self.main.stale_note.text(), "⚠ 2 Kurse veraltet")
        self.assertIn("AAPL", self.main.stale_note.toolTip())
        self.assertIn("MSFT", self.main.stale_note.toolTip())
        self.assertIn("kein Netz", self.main.stale_note.toolTip())

    def test_marker_disappears_after_a_successful_refresh(self):
        self.make_stale()
        self.ctl.quote_errors.clear()
        self.ctl.changed.emit()
        self.assertTrue(self.main.stale_note.isHidden())
        self.assertNotIn(w.AMBER, self.main.cards["AAPL"].price.styleSheet())

    def test_quote_turns_stale_with_age_even_without_new_events(self):
        self.ctl.quote_times["AAPL"] = dt.datetime.now() - dt.timedelta(seconds=w.STALE_SECONDS + 30)
        self.assertNotIn(w.AMBER, self.main.cards["AAPL"].price.styleSheet())  # noch nichts ausgelöst
        self.main.fresh_timer.timeout.emit()
        self.assertIn(w.AMBER, self.main.cards["AAPL"].price.styleSheet())
        self.assertIn("länger nicht aktualisiert", self.main.cards["AAPL"].price.toolTip())
        self.assertFalse(self.main.stale_note.isHidden())

    def test_timer_runs_every_fifteen_seconds(self):
        self.assertTrue(self.main.fresh_timer.isActive())
        self.assertEqual(self.main.fresh_timer.interval(), 15_000)

    def test_old_quote_from_an_earlier_day_shows_date_in_the_tooltip(self):
        self.ctl.quote_times["AAPL"] = dt.datetime.now() - dt.timedelta(days=2)
        self.ctl.changed.emit()
        tip = self.main.cards["AAPL"].price.toolTip()
        self.assertRegex(tip, r"Stand \d\d\.\d\d\. \d\d:\d\d")

    def test_cards_without_a_quote_have_no_tooltip_or_marker(self):
        self.ctl.quotes.pop("AAPL")
        self.ctl.changed.emit()
        self.assertEqual(self.main.cards["AAPL"].price.toolTip(), "")
        self.assertEqual(self.main.cards["AAPL"].price.text(), "")

    def test_detail_window_shows_age_source_and_stale_state(self):
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.assertIn("Testquelle", detail.price_note.text())
        self.assertNotIn(w.AMBER, detail.price.styleSheet())
        self.make_stale()
        self.assertIn(w.AMBER, detail.price.styleSheet())
        self.assertIn(w.AMBER, detail.price_note.styleSheet())
        self.assertIn("Veraltet", detail.price_note.text())

    def test_detail_window_notices_aging_by_itself(self):
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.ctl.quote_times["AAPL"] = dt.datetime.now() - dt.timedelta(seconds=w.STALE_SECONDS + 30)
        detail.fresh_timer.timeout.emit()
        self.assertIn(w.AMBER, detail.price.styleSheet())


class MasterDataDisplayTests(AppTestCase):
    INFO = {"name": "Apple Inc.", "exchange": "NASDAQ", "currency": "USD", "sector": "Technology",
            "industry": "Consumer Electronics", "country": "United States", "isin": "",
            "source": "Yahoo Finance", "fetched_at": dt.datetime(2026, 10, 8, 23, 50)}

    def setUp(self):
        super().setUp()
        self.main = w.MainWindow(self.ctl)

    def test_without_master_data_the_header_stays_empty(self):
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.assertTrue(detail.company.isHidden())
        self.assertTrue(detail.company_meta.isHidden())

    def test_detail_shows_name_exchange_sector_industry_and_country(self):
        self.ctl.instruments["AAPL"] = dict(self.INFO)
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.assertFalse(detail.company.isHidden())
        self.assertEqual(detail.company.text(), "Apple Inc.")
        self.assertEqual(detail.company_meta.text(),
                         "NASDAQ · Technology · Consumer Electronics · United States")

    def test_isin_is_shown_only_when_known(self):
        self.ctl.instruments["AAPL"] = {**self.INFO, "isin": "US0378331005"}
        self.main.open_detail("AAPL")
        self.assertTrue(self.main.details["AAPL"].company_meta.text().endswith("ISIN US0378331005"))

    def test_tooltip_names_source_and_date_of_the_master_data(self):
        self.ctl.instruments["AAPL"] = dict(self.INFO)
        self.main.open_detail("AAPL")
        tip = self.main.details["AAPL"].company_meta.toolTip()
        self.assertIn("Yahoo Finance", tip)
        self.assertIn("08.10.2026 23:50", tip)

    def test_open_detail_window_updates_when_master_data_arrives(self):
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.assertTrue(detail.company.isHidden())
        self.ctl.load_instrument("AAPL")
        self.assertTrue(wait_until(lambda: detail.company.text() == "AAPL Inc."))
        self.assertFalse(detail.company.isHidden())


class NumberFormatTests(unittest.TestCase):
    def test_signed_amounts_have_a_sign_and_thousands_separators(self):
        self.assertEqual(w.signed_money(1234.5), "+1,234.50 €")
        self.assertEqual(w.signed_money(-0.75), "-0.75 €")
        self.assertEqual(w.money(1350), "1,350.00 €")

    def test_tiny_negative_values_do_not_show_minus_zero(self):
        self.assertEqual(w.signed_money(-0.001), "+0.00 €")
        self.assertEqual(w.signed_number(-0.004), "+0.00")
        self.assertEqual(w.signed_percent(-0.003), "+0.00 %")
        self.assertEqual(w.signed_money(-0.005), "-0.01 €")  # ab einem halben Cent wird gerundet

    def test_missing_percentage_is_a_dash(self):
        self.assertEqual(w.signed_percent(None), "–")


class PortfolioWindowTests(AppTestCase):
    D1 = dt.date(2026, 1, 5)

    def setUp(self):
        super().setUp()
        self.ctl.fx.add_history("USD", {self.D1: 0.90})
        self.ctl.fx.set_latest("USD", 0.90, dt.datetime.now(), "Test")
        self.ctl.instruments = {
            "AAPL": {"name": "Apple Inc.", "sector": "Technology", "country": "United States", "currency": "USD"},
            "MSFT": {"name": "Microsoft", "sector": "Technology", "country": "United States", "currency": "USD"},
            "DELL": {"name": "Dell", "sector": "", "country": "", "currency": "USD"}}
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)    # Kosten 720 €, Wert 900 €
        self.ctl.record_trade("MSFT", "buy", 5, 100, 0, self.D1)    # Kosten 450 €, Wert 450 €
        self.main = w.MainWindow(self.ctl)
        self.opened = []
        w.open_portfolio(self.ctl, self.opened.append)
        self.pw = w.PORTFOLIO_WINDOWS["window"]

    def legend_labels(self):
        rows = []
        for index in range(self.pw.legend.count()):
            widget = self.pw.legend.itemAt(index).widget()
            if widget:
                labels = widget.findChildren(QLabel) or ([widget] if isinstance(widget, QLabel) else [])
                rows.append([label.text() for label in labels if label.text()])  # ohne den Farbpunkt
        return rows

    def test_total_value_and_invested_capital(self):
        self.assertEqual(self.pw.total.text(), "1,350.00 €")
        self.assertEqual(self.pw.total_sub.text(), "investiert 1,170.00 €")

    def test_unrealized_tile_shows_amount_percentage_and_the_split(self):
        self.assertEqual(self.pw.unrealized.value.text(), "+180.00 €")
        self.assertIn("+15.38 %", self.pw.unrealized.sub.text())
        self.assertIn("Kurs +180.00 €", self.pw.unrealized.sub.text())
        self.assertIn("Währung +0.00 €", self.pw.unrealized.sub.text())

    def test_gain_color_follows_the_sign(self):
        self.assertIn(w.GREEN, self.pw.unrealized.value.styleSheet())
        self.ctl.quotes["AAPL"]["price"] = 50.0
        self.pw.refresh()
        self.assertEqual(self.pw.unrealized.value.text(), "-270.00 €")  # 450 + 450 - 1170
        self.assertIn(w.RED, self.pw.unrealized.value.styleSheet())

    def test_currency_effect_appears_when_the_rate_moves(self):
        self.ctl.fx.set_latest("USD", 0.80, dt.datetime.now(), "Test")
        self.pw.refresh()
        # Wert 1000 * 0,80 + 500 * 0,80 = 1200; Kosten 1170 -> +30; Kurs +180 (zum Kaufkurs 0,90 gerechnet), Währung -150
        self.assertEqual(self.pw.unrealized.value.text(), "+30.00 €")
        self.assertIn("Währung -150.00 €", self.pw.unrealized.sub.text())

    def test_realized_tile_and_total_result_after_a_sale(self):
        self.ctl.record_trade("AAPL", "sell", 2, 110, 0, dt.date(2026, 2, 1))  # 60 USD Gewinn = 54 €
        self.pw.refresh()
        self.assertEqual(self.pw.realized.value.text(), "+54.00 €")
        self.assertIn("aus Verkäufen", self.pw.realized.sub.text())
        # unrealisiert: 8 AAPL (Kosten 576, Wert 720) + MSFT (450/450) = +144; Summe +198
        self.assertEqual(self.pw.unrealized.value.text(), "+144.00 €")
        self.assertEqual(self.pw.result.value.text(), "+198.00 €")
        self.assertIn("Rendite +", self.pw.result.sub.text())

    def test_warnings_are_shown_and_hidden(self):
        self.assertTrue(self.pw.notes.isHidden())
        self.ctl.quote_errors["AAPL"] = "offline"
        self.pw.refresh()
        self.assertFalse(self.pw.notes.isHidden())
        self.assertIn("1 Kurs veraltet: AAPL", self.pw.notes.text())
        self.ctl.quote_errors.clear()
        self.pw.refresh()
        self.assertTrue(self.pw.notes.isHidden())

    def test_missing_rate_is_named_in_the_notes(self):
        self.ctl.quotes["DELL"]["currency"] = "CHF"
        self.ctl.record_trade("DELL", "buy", 1, 100, 0, self.D1)
        self.pw.refresh()
        self.assertIn("DELL: kein Wechselkurs CHF → EUR", self.pw.notes.text())
        self.assertNotIn("DELL", self.pw.rows)

    def test_allocation_by_position_is_the_default_and_sorted(self):
        labels = self.legend_labels()
        self.assertEqual([l[0] for l in labels][:2], ["AAPL", "MSFT"])
        self.assertEqual(labels[0][1], "66.7 %")
        self.assertEqual(self.pw.dimension, "position")

    def test_switching_the_allocation_to_sector_country_and_currency(self):
        self.pw.segments["sector"].click()
        self.assertEqual(self.legend_labels(), [["Technology", "100.0 %"]])
        self.pw.segments["country"].click()
        self.assertEqual(self.legend_labels(), [["United States", "100.0 %"]])
        self.pw.segments["currency"].click()
        self.assertEqual(self.legend_labels(), [["USD", "100.0 %"]])
        self.assertTrue(self.pw.segments["currency"].isChecked())

    def test_unknown_sector_is_named_unknown(self):
        self.ctl.record_trade("DELL", "buy", 1, 100, 0, self.D1)
        self.pw.segments["sector"].click()
        self.pw.refresh()
        self.assertIn(["Unbekannt", "6.2 %"], self.legend_labels())  # 90 von 1.440 €

    def test_chosen_allocation_survives_a_refresh(self):
        self.pw.segments["country"].click()
        self.pw.refresh()
        self.assertEqual(self.pw.dimension, "country")
        self.assertTrue(self.pw.segments["country"].isChecked())

    def test_donut_gets_one_slice_per_legend_entry_and_can_be_painted(self):
        self.assertEqual(len(self.pw.donut.slices), 2)
        self.assertFalse(self.pw.donut.grab().isNull())
        self.pw.segments["sector"].click()
        self.assertEqual(len(self.pw.donut.slices), 1)
        self.assertFalse(self.pw.donut.grab().isNull())

    def test_position_rows_are_sorted_by_value_with_profit_and_share(self):
        self.assertEqual(list(self.pw.rows), ["AAPL", "MSFT"])
        row = self.pw.rows["AAPL"].labels
        self.assertEqual((row["shares"].text(), row["avg_cost"].text(), row["price"].text()), ("10", "80.00", "100.00"))
        self.assertEqual((row["value"].text(), row["pl"].text(), row["pl_pct"].text(), row["share"].text()),
                         ("900.00", "+180.00", "+25.00 %", "66.7 %"))
        self.assertIn("Kurs", row["pl"].toolTip())
        self.assertEqual(row["symbol"].toolTip(), "Apple Inc.")
        self.assertEqual(row["price"].toolTip(), "in USD")

    def test_rows_reorder_when_values_change(self):
        self.ctl.quotes["MSFT"]["price"] = 500.0
        self.pw.refresh()
        order = [self.pw.holdings_layout.itemAt(i).widget().symbol for i in range(self.pw.holdings_layout.count())]
        self.assertEqual(order, ["MSFT", "AAPL"])

    def test_closed_position_leaves_the_table(self):
        self.ctl.record_trade("MSFT", "sell", 5, 100, 0, dt.date(2026, 2, 1))
        self.pw.refresh()
        self.assertEqual(list(self.pw.rows), ["AAPL"])
        self.assertEqual(self.pw.holdings_layout.count(), 1)

    def test_empty_portfolio_shows_a_hint_and_zero_values(self):
        for symbol in ("AAPL", "MSFT"):
            self.ctl.record_trade(symbol, "sell", self.ctl.positions[symbol]["shares"], 100, 0, dt.date(2026, 2, 1))
        self.pw.refresh()
        self.assertFalse(self.pw.empty.isHidden())
        self.assertEqual(self.pw.total.text(), "0.00 €")
        self.assertEqual(self.legend_labels(), [["Keine Daten"]])
        self.assertEqual(self.pw.donut.slices, [])
        self.assertFalse(self.pw.donut.grab().isNull())

    def test_clicking_a_row_opens_that_stock(self):
        self.pw.rows["MSFT"].clicked.emit("MSFT")
        self.assertEqual(self.opened, ["MSFT"])

    def test_window_opens_once_docks_and_frees_its_slot_on_close(self):
        w.open_portfolio(self.ctl, self.opened.append)
        self.assertEqual(len(w.PORTFOLIO_WINDOWS), 1)
        self.assertIn(self.pw, w.Dock.windows)
        self.pw.close()
        self.assertNotIn("window", w.PORTFOLIO_WINDOWS)
        self.assertNotIn(self.pw, w.Dock.windows)

    def test_many_changes_in_a_row_cause_one_refresh(self):
        refreshes = []
        self.pw.update_timer.timeout.disconnect()
        self.pw.update_timer.timeout.connect(lambda: refreshes.append(1))
        for _ in range(8):
            self.ctl.changed.emit()
        self.assertTrue(wait_until(lambda: refreshes))
        wait_until(lambda: False, 400)
        self.assertEqual(len(refreshes), 1)

    def test_window_follows_live_changes(self):
        self.ctl.quotes["AAPL"]["price"] = 200.0
        self.ctl.changed.emit()
        self.assertTrue(wait_until(lambda: self.pw.total.text() == "2,250.00 €"))  # 2000 * 0,9 + 450

    def test_main_window_button_opens_the_portfolio(self):
        self.pw.close()
        self.main.portfolio_button.click()
        self.assertIn("window", w.PORTFOLIO_WINDOWS)

    def test_table_fits_the_window_width(self):
        self.pw.show()
        row = self.pw.rows["AAPL"]
        self.assertLessEqual(row.minimumSizeHint().width(), self.pw.width() - 74)


class FlashTests(AppTestCase):
    def setUp(self):
        super().setUp()
        patcher = mock.patch.object(w, "FLASH_MS", 150)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.main = w.MainWindow(self.ctl)

    @staticmethod
    def color_of(widget):
        match = re.search(r"background: #([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})", widget.styleSheet())
        return tuple(int(part, 16) for part in match.groups()) if match else None

    def test_buy_flashes_the_row_green_and_then_fades_out(self):
        card = self.main.cards["AAPL"]
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY)
        r, g, b = self.color_of(card)
        self.assertGreater(g, r)
        self.assertTrue(wait_until(lambda: card.styleSheet() == ""))

    def test_sell_flashes_the_row_red(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY - dt.timedelta(days=1))
        card = self.main.cards["AAPL"]
        wait_until(lambda: card.styleSheet() == "")
        self.ctl.record_trade("AAPL", "sell", 5, 100, 0, TODAY)
        r, g, b = self.color_of(card)
        self.assertGreater(r, g)

    def test_start_position_flashes_green(self):
        self.ctl.start_position("AAPL", 10, 0)
        r, g, b = self.color_of(self.main.cards["AAPL"])
        self.assertGreater(g, r)

    def test_only_the_traded_row_flashes(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY)
        self.assertEqual(self.main.cards["MSFT"].styleSheet(), "")
        self.assertEqual(self.main.cards["DELL"].styleSheet(), "")

    def test_rejected_trade_does_not_flash(self):
        with self.assertRaises(ValueError):
            self.ctl.record_trade("AAPL", "sell", 5, 100, 0, TODAY)
        self.assertEqual(self.main.cards["AAPL"].styleSheet(), "")

    def test_deleting_a_transaction_does_not_flash(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY)
        card = self.main.cards["AAPL"]
        wait_until(lambda: card.styleSheet() == "")
        self.ctl.delete_transaction("AAPL", self.ctl.transactions["AAPL"][0].id)
        self.assertEqual(card.styleSheet(), "")

    def test_second_trade_restarts_the_flash_without_leaving_a_stuck_color(self):
        card = self.main.cards["AAPL"]
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY - dt.timedelta(days=1))
        self.ctl.record_trade("AAPL", "sell", 1, 100, 0, TODAY)
        r, g, b = self.color_of(card)
        self.assertGreater(r, g)
        self.assertTrue(wait_until(lambda: card.styleSheet() == ""))

    def test_card_stays_flash_free_for_its_next_hover_style(self):
        card = self.main.cards["AAPL"]
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY)
        wait_until(lambda: card.styleSheet() == "")
        self.assertEqual(card.styleSheet(), "")

    def test_position_card_in_detail_window_flashes_for_its_own_symbol_only(self):
        self.main.open_detail("AAPL")
        self.main.open_detail("MSFT")
        aapl, msft = self.main.details["AAPL"], self.main.details["MSFT"]
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY)
        self.assertIsNotNone(self.color_of(aapl.position_card))
        self.assertEqual(msft.position_card.styleSheet(), "")


class WindowTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.main = w.MainWindow(self.ctl)

    def test_detail_buttons_follow_the_position_state(self):
        self.main.open_detail("AAPL")
        buttons = self.main.details["AAPL"].buttons
        self.assertEqual((buttons["start"].isEnabled(), buttons["buy"].isEnabled(),
                          buttons["sell"].isEnabled(), buttons["history"].isEnabled()),
                         (True, True, False, True))
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY)
        self.assertEqual((buttons["start"].isEnabled(), buttons["sell"].isEnabled()), (False, True))

    def test_detail_shows_profit_cost_and_value(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY)
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.assertEqual(detail.pl_percent.text(), "+25.00 %")
        self.assertIn("10 Stück", detail.position_info.text())
        self.assertIn("Einstand 80.00", detail.position_info.text())
        self.assertIn("Wert 1000.00 USD", detail.position_info.text())

    def test_detail_without_position_mentions_realized_profit_of_closed_trades(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY - dt.timedelta(days=1))
        self.ctl.record_trade("AAPL", "sell", 10, 100, 0, TODAY)
        self.main.open_detail("AAPL")
        info = self.main.details["AAPL"].position_info.text()
        self.assertIn("Keine Position", info)
        self.assertIn("+200.00", info)

    def test_detail_window_opens_once_per_symbol_and_leaves_dock_on_close(self):
        self.main.open_detail("AAPL")
        self.main.open_detail("AAPL")
        self.assertEqual(len(self.main.details), 1)
        detail = self.main.details["AAPL"]
        self.assertIn(detail, w.Dock.windows)
        detail.close()
        self.assertNotIn(detail, w.Dock.windows)
        self.assertNotIn("AAPL", self.main.details)

    def test_detail_loads_events_and_news_in_the_background(self):
        events = [(TODAY + dt.timedelta(days=7), "Quartalszahlen")]
        news = [("Schlagzeile", "Quelle", dt.datetime(2026, 10, 8, 9, 0), "https://x/1")]
        with mock.patch.object(sd, "fetch_events", lambda s: events), \
                mock.patch.object(sd, "fetch_news", lambda s, count=15: news):
            self.main.open_detail("AAPL")
            detail = self.main.details["AAPL"]
            self.assertTrue(wait_until(lambda: detail.event_title.text() == "Quartalszahlen"))
            self.assertIn("in 7 Tagen", detail.event_when.text())
            self.assertTrue(wait_until(lambda: any(
                "Schlagzeile" in t for t in texts(detail))))

    def test_detail_chart_defaults_to_six_months_and_switches_range(self):
        calls = []

        def history(symbol, key="6m"):
            calls.append(key)
            return [(dt.datetime(2026, 1, 1) + dt.timedelta(days=i), 100.0 + i) for i in range(5)]

        with mock.patch.object(sd, "fetch_history", history):
            self.main.open_detail("AAPL")
            detail = self.main.details["AAPL"]
            self.assertTrue(wait_until(lambda: detail.chart.points is not None))
            self.assertEqual(calls, ["6m"])
            self.assertTrue(detail.range_buttons["6m"].isChecked())
            detail.range_buttons["5y"].click()
            self.assertTrue(wait_until(lambda: calls == ["6m", "5y"] and detail.chart.points is not None))
            self.assertEqual([k for k, b in detail.range_buttons.items() if b.isChecked()], ["5y"])
            self.assertEqual(list(detail.range_buttons), ["1w", "1m", "6m", "1y", "5y"])
            self.assertEqual(detail.range_change.text(), "+4.00 %")
            detail.chart.hover = 2
            detail.chart.grab()  # Zeichnen darf nicht scheitern

    def test_detail_chart_reports_a_failed_load(self):
        def broken(symbol, key="6m"):
            raise ValueError("offline")

        with mock.patch.object(sd, "fetch_history", broken):
            self.main.open_detail("AAPL")
            detail = self.main.details["AAPL"]
            self.assertTrue(wait_until(lambda: "offline" in detail.chart.note))
            self.assertIsNone(detail.chart.points)

    def test_detail_scrolls_as_a_whole_with_news_inside_it(self):
        news = [("Schlagzeile", "Quelle", dt.datetime(2026, 10, 8, 9, 0), "https://x/1")]
        with mock.patch.object(sd, "fetch_news", lambda s, count=15: news):
            self.main.open_detail("AAPL")
            detail = self.main.details["AAPL"]
            self.assertTrue(wait_until(lambda: any("Schlagzeile" in t for t in texts(detail))))
            areas = detail.findChildren(w.QScrollArea)
            self.assertEqual(len(areas), 1)
            inside = areas[0].widget()
            for widget in (detail.chart, detail.position_card, detail.price):
                self.assertTrue(inside.isAncestorOf(widget))
            self.assertTrue(any(inside.isAncestorOf(card) for card in detail.findChildren(w.NewsCard)))

    def test_detail_without_events_says_so(self):
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.assertTrue(wait_until(lambda: detail.event_title.text() == "Kein bevorstehender Termin"))

    def test_transactions_window_lists_every_transaction_with_sale_gain(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 2, TODAY - dt.timedelta(days=2))
        self.ctl.record_trade("AAPL", "sell", 4, 100, 0, TODAY - dt.timedelta(days=1))
        w.open_transactions(self.ctl, "AAPL")
        window = w.TX_WINDOWS["AAPL"]
        rows = window.findChildren(w.TransactionRow)
        self.assertEqual(len(rows), 2)
        all_text = " ".join(texts(window))
        self.assertIn("Kauf · 10 Stk · 80.00 USD", all_text)
        self.assertIn("Verkauf · 4 Stk · 100.00 USD", all_text)
        self.assertIn("Gebühr 2.00", all_text)
        self.assertIn("+79.20 USD", all_text)  # 400 - 4 * 80.2

    def test_transactions_window_updates_live_and_opens_once(self):
        w.open_transactions(self.ctl, "AAPL")
        w.open_transactions(self.ctl, "AAPL")
        self.assertEqual(len(w.TX_WINDOWS), 1)
        window = w.TX_WINDOWS["AAPL"]
        self.assertIn("Noch keine Transaktionen", " ".join(texts(window)))
        self.ctl.record_trade("AAPL", "buy", 3, 100, 0, TODAY)
        self.assertEqual(len(window.findChildren(w.TransactionRow)), 1)
        self.assertIn("3 Stück", " ".join(texts(window)))

    def test_closing_transactions_window_frees_the_slot(self):
        w.open_transactions(self.ctl, "AAPL")
        window = w.TX_WINDOWS["AAPL"]
        window.close()
        self.assertNotIn("AAPL", w.TX_WINDOWS)
        self.assertNotIn(window, w.Dock.windows)

    def delete_button(self, window, index):
        row = window.findChildren(w.TransactionRow)[index]
        return row.findChildren(QPushButton)[-1]

    def test_deleting_from_the_transactions_window_asks_and_recalculates(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY)
        w.open_transactions(self.ctl, "AAPL")
        window = w.TX_WINDOWS["AAPL"]
        driver = DialogDriver(lambda d: d.submit())
        self.delete_button(window, 0).click()
        driver.check()
        self.assertEqual(self.ctl.transactions.get("AAPL", []), [])
        self.assertTrue(wait_until(lambda: window.findChildren(w.TransactionRow) == []))

    def test_cancelling_the_delete_dialog_keeps_the_transaction(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY)
        w.open_transactions(self.ctl, "AAPL")
        driver = DialogDriver(lambda d: d.reject())
        self.delete_button(w.TX_WINDOWS["AAPL"], 0).click()
        driver.check()
        self.assertEqual(len(self.ctl.transactions["AAPL"]), 1)

    def test_deleting_a_purchase_needed_by_a_sale_shows_the_reason(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY - dt.timedelta(days=1))
        self.ctl.record_trade("AAPL", "sell", 4, 100, 0, TODAY)
        w.open_transactions(self.ctl, "AAPL")
        window = w.TX_WINDOWS["AAPL"]
        # Zeilen sind neueste zuerst: Index 1 ist der Kauf
        driver = DialogDriver(lambda d: (d.submit(), self.assertIn("Löschen nicht möglich", d.error.text())))
        self.delete_button(window, 1).click()
        driver.check()
        self.assertEqual(len(self.ctl.transactions["AAPL"]), 2)


MATCHES = [{"symbol": "DRO.AX", "name": "DroneShield Limited", "exchange": "Australian", "type": "EQUITY"},
           {"symbol": "DRSHF", "name": "Droneshield Ltd", "exchange": "OTC Markets", "type": "EQUITY"}]


class ChoiceDialogTests(unittest.TestCase):
    OPTIONS = [("DRO.AX", "DroneShield Limited", "Australian"), ("DRSHF", "Droneshield Ltd", "")]

    def setUp(self):
        qapp()
        reset_windows()
        self.addCleanup(reset_windows)

    def test_lists_every_match_with_name_and_exchange(self):
        dialog = w.ChoiceDialog("Treffer", "Welche?", self.OPTIONS)

        def check(d):
            self.assertEqual(len(d.buttons), 2)
            self.assertIn("DRO.AX", d.buttons[0].text())
            self.assertIn("DroneShield Limited", d.buttons[0].text())
            self.assertIn("Australian", d.buttons[0].text())
            self.assertNotIn("·   ", d.buttons[1].text().split("Droneshield Ltd")[1])  # keine leere Börse

        driver = DialogDriver(check)
        dialog.run()
        driver.check()

    def test_clicking_a_match_returns_its_symbol(self):
        dialog = w.ChoiceDialog("Treffer", "Welche?", self.OPTIONS)
        driver = DialogDriver(lambda d: d.buttons[1].click())
        self.assertEqual(dialog.run(), "DRSHF")
        driver.check()

    def test_cancel_returns_nothing(self):
        dialog = w.ChoiceDialog("Treffer", "Welche?", self.OPTIONS)
        driver = DialogDriver(lambda d: d.reject())
        self.assertIsNone(dialog.run())
        driver.check()


class AddSymbolUiTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.main = w.MainWindow(self.ctl)

    def add(self, text):
        self.main.add_symbol(text)

    def test_plus_button_opens_a_small_dialog_that_adds_the_symbol(self):
        self.main.add_button.click()
        dialog = self.main.add_dialog
        dialog.entries[0].setText("NVDA")
        dialog.submit()
        self.assertFalse(dialog.isVisible())
        self.assertTrue(wait_until(lambda: self.main.status.text() == "NVDA gefunden"))

    def test_plus_dialog_is_not_modal_so_other_windows_stay_usable(self):
        self.main.add_button.click()
        dialog = self.main.add_dialog
        self.assertTrue(dialog.isVisible())
        self.assertFalse(dialog.isModal())
        self.assertIsNone(QApplication.activeModalWidget())
        self.main.add_button.click()  # kein zweiter Dialog
        self.assertIs(self.main.add_dialog, dialog)
        dialog.reject()
        self.assertNotIn(dialog, w.Dock.windows)

    def test_plus_dialog_rejects_empty_input_and_stays_open(self):
        self.main.add_button.click()
        dialog = self.main.add_dialog
        dialog.submit()
        self.assertFalse(dialog.error.isHidden())
        self.assertTrue(dialog.isVisible())
        dialog.reject()
        self.assertEqual(self.ctl.symbols, ["AAPL", "MSFT", "DELL"])

    def test_status_line_shows_found_after_adding(self):
        self.add("NVDA")
        self.assertTrue(wait_until(lambda: self.main.status.text() == "NVDA gefunden"))

    def test_foreign_suffix_adds_the_yahoo_symbol_with_a_card(self):
        self.add("ABBN.ZU")
        self.assertTrue(wait_until(lambda: "ABBN.SW" in self.main.cards))

    def test_name_opens_a_chooser_and_the_picked_listing_is_added(self):
        driver = DialogDriver(lambda d: d.buttons[0].click(), delay=400)
        with mock.patch.object(sd, "search_symbols", lambda q, count=8: MATCHES):
            self.add("droneshield")
            self.assertTrue(wait_until(lambda: "DRO.AX" in self.ctl.symbols, 6000))
        driver.check()
        self.assertIn("DRO.AX", self.main.cards)
        self.assertNotIn("DRSHF", self.ctl.symbols)

    def test_cancelling_the_chooser_adds_nothing(self):
        driver = DialogDriver(lambda d: d.reject(), delay=400)
        with mock.patch.object(sd, "search_symbols", lambda q, count=8: MATCHES):
            self.add("droneshield")
            wait_until(lambda: False, 1200)
        driver.check()
        self.assertEqual(self.ctl.symbols, ["AAPL", "MSFT", "DELL"])

    def test_unknown_name_shows_hint_with_exchange_suffix_examples(self):
        driver = DialogDriver(lambda d: self.assertTrue(
            any("DRO.AX" in t and "ABBN.SW" in t and "keine Aktie gefunden" in t for t in texts(d))), delay=400)
        self.add("xyz unbekannt")
        wait_until(lambda: False, 1200)
        driver.check()
        self.assertEqual(self.ctl.symbols, ["AAPL", "MSFT", "DELL"])


class SingleInstanceTests(unittest.TestCase):
    def test_second_claim_fails_until_the_first_is_released(self):
        first = w.claim_single_instance(48999)
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(w.claim_single_instance(48999))
        finally:
            first.close()
        again = w.claim_single_instance(48999)
        self.assertIsNotNone(again)
        again.close()


class ShutdownTests(unittest.TestCase):
    def test_quitting_with_open_windows_exits_cleanly(self):
        """Regression: Beenden mit offenem Detail- und Transaktionsfenster stürzte früher ab (Heap-Fehler)."""
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        result = subprocess.run([sys.executable, os.path.join(ROOT, "tests", "exit_probe.py")],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
