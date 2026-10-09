"""Oberfläche: Fensteranordnung, Dialoge und Fokus, Menü, Karten, Aufblinken, Fenster, Beenden."""
import datetime as dt
import os
import re
import socket
import subprocess
import sys
import unittest
from unittest import mock

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QWidget

import glossary
import history
import stock_data as sd
import stock_widget as w
from sources import ExternalTrade, SyncBatch, TransactionSource
from tests.support import AppTestCase, DialogDriver, plain, qapp, wait_until

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

    def test_no_day_change_shows_just_a_dash(self):
        for change in (0.0, 0.004, -0.004):  # auf zwei Stellen gerundet ebenfalls 0,00
            self.ctl.quotes["AAPL"]["change_pct"] = change
            self.ctl.changed.emit()
            self.assertEqual(self.main.cards["AAPL"].day.text(), "–", change)
        self.ctl.quotes["AAPL"]["change_pct"] = 0.006
        self.ctl.changed.emit()
        self.assertEqual(self.main.cards["AAPL"].day.text(), "▲ +0.01 %")

    def test_rows_with_a_dash_in_the_day_column_always_sort_last(self):
        self.ctl.quotes["AAPL"]["change_pct"] = 0.0
        self.ctl.quotes["MSFT"]["change_pct"] = 2.0
        self.ctl.quotes["DELL"]["change_pct"] = -1.0
        self.ctl.changed.emit()
        self.main.heads["day"].click()
        self.assertEqual(self.symbols_on_screen(), ["DELL", "MSFT", "AAPL"])
        self.main.heads["day"].click()
        self.assertEqual(self.symbols_on_screen(), ["MSFT", "DELL", "AAPL"])

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
        w.open_portfolio(self.ctl)
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
        self.assertEqual(plain(self.pw.total_sub), "investiert 1,170.00 €")

    def test_unrealized_tile_shows_amount_percentage_and_the_split(self):
        self.assertEqual(self.pw.unrealized.value.text(), "+180.00 €")
        self.assertIn("+15.38 %", plain(self.pw.unrealized.sub))
        self.assertIn("Kurs +180.00 €", plain(self.pw.unrealized.sub))
        self.assertIn("Währung +0.00 €", plain(self.pw.unrealized.sub))

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
        self.assertIn("Währung -150.00 €", plain(self.pw.unrealized.sub))

    def test_realized_tile_and_total_result_after_a_sale(self):
        self.ctl.record_trade("AAPL", "sell", 2, 110, 0, dt.date(2026, 2, 1))  # 60 USD Gewinn = 54 €
        self.pw.refresh()
        self.assertEqual(self.pw.realized.value.text(), "+54.00 €")
        self.assertIn("aus Verkäufen", plain(self.pw.realized.sub))
        # unrealisiert: 8 AAPL (Kosten 576, Wert 720) + MSFT (450/450) = +144; Summe +198
        self.assertEqual(self.pw.unrealized.value.text(), "+144.00 €")
        self.assertEqual(self.pw.result.value.text(), "+198.00 €")
        self.assertIn("Rendite +", plain(self.pw.result.sub))

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

    def test_portfolio_has_no_positions_section_those_are_in_the_watchlist(self):
        texts_in_window = " ".join(label.text() for label in self.pw.findChildren(QLabel))
        self.assertNotIn("POSITIONEN", texts_in_window.upper().replace("AUFTEILUNG", ""))
        self.assertFalse(hasattr(self.pw, "rows"))

    def test_empty_portfolio_shows_a_hint_and_zero_values(self):
        for symbol in ("AAPL", "MSFT"):
            self.ctl.record_trade(symbol, "sell", self.ctl.positions[symbol]["shares"], 100, 0, dt.date(2026, 2, 1))
        self.pw.refresh()
        self.assertEqual(self.pw.total.text(), "0.00 €")
        self.assertEqual(self.legend_labels(), [["Keine Daten"]])
        self.assertEqual(self.pw.donut.slices, [])
        self.assertFalse(self.pw.donut.grab().isNull())

    def test_window_opens_once_docks_and_frees_its_slot_on_close(self):
        w.open_portfolio(self.ctl)
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

    def test_opens_above_the_watchlist_right_aligned_and_not_beside_it(self):
        self.pw.close()
        self.main.setFixedHeight(200)  # genug Platz darüber, auch auf einem kleinen Testbildschirm
        self.main.show_docked()
        self.main.portfolio_button.click()
        pw = w.PORTFOLIO_WINDOWS["window"]
        self.assertIs(pw.dock_above, self.main)
        self.assertEqual(pw.x() + pw.width(), self.main.x() + self.main.width())
        self.assertLessEqual(pw.y() + pw.height(), self.main.y() - w.Dock.GAP)
        self.assertGreaterEqual(pw.y(), QApplication.primaryScreen().availableGeometry().top())

    def test_stacked_portfolio_is_exactly_as_wide_as_the_watchlist_and_follows_its_width(self):
        self.pw.close()
        self.main.setFixedHeight(200)
        self.main.show_docked()
        self.main.portfolio_button.click()
        pw = w.PORTFOLIO_WINDOWS["window"]
        self.assertEqual(pw.width(), self.main.width())
        self.assertEqual(pw.x(), self.main.x())
        self.ctl.events["AAPL"] = [(TODAY + dt.timedelta(days=5), "Ex-Dividende")]
        self.ctl.changed.emit()  # der Termin-Text macht die Watchlist breiter
        self.assertEqual(pw.width(), self.main.width())

    def test_summary_tiles_and_total_share_one_row_to_use_the_width(self):
        self.pw.show()
        QApplication.processEvents()
        tops = {self.pw.total.parentWidget().geometry().y(), self.pw.unrealized.geometry().y(),
                self.pw.realized.geometry().y(), self.pw.result.geometry().y()}
        self.assertEqual(len(tops), 1)

    def test_allocation_names_up_to_sixteen_entries_before_grouping_the_rest(self):
        asked = []

        def fake(holdings, dimension, max_items=8):
            asked.append(max_items)
            return [(f"S{n}", 10.0, 6.0) for n in range(max_items)]

        with mock.patch.object(w.portfolio, "allocation", fake):
            self.pw.show_allocation("position")
        self.assertEqual(asked[-1], 16)
        self.assertEqual(len(self.legend_labels()), 16)
        self.assertEqual(len(set(w.PALETTE)), 16)  # jede Scheibe hat ihre eigene Farbe

    def test_legend_uses_two_columns_when_there_are_many_entries(self):
        self.pw.segments["position"].click()
        self.assertEqual({self.pw.legend.getItemPosition(i)[1] for i in range(self.pw.legend.count())}, {0})
        slices = [(f"S{n}", 10.0, 10.0) for n in range(7)]
        self.pw.summary = type("S", (), {"holdings": []})()
        with mock.patch.object(w.portfolio, "allocation", lambda holdings, dimension, max_items=8: slices):
            self.pw.show_allocation("position")
        columns = {self.pw.legend.getItemPosition(i)[1] for i in range(self.pw.legend.count())}
        self.assertEqual(columns, {0, 1})

    def test_stacked_portfolio_does_not_take_a_slot_beside_the_watchlist(self):
        self.pw.close()
        self.main.setFixedHeight(200)
        self.main.show_docked()
        self.main.portfolio_button.click()
        w.open_transactions(self.ctl, "AAPL")
        beside = w.TX_WINDOWS["AAPL"]
        left = QApplication.primaryScreen().availableGeometry().left()
        self.assertTrue(w.Dock.fits_above(w.PORTFOLIO_WINDOWS["window"]))
        self.assertEqual(beside.x(), max(self.main.x() - w.Dock.GAP - beside.width(), left))  # direkt links vom Hauptfenster

    def test_portfolio_is_only_as_tall_as_the_space_above_the_watchlist(self):
        self.pw.close()
        self.main.setFixedHeight(300)
        self.main.show_docked()
        self.main.portfolio_button.click()
        pw = w.PORTFOLIO_WINDOWS["window"]
        space = self.main.y() - w.Dock.GAP - w.Dock.MARGIN - QApplication.primaryScreen().availableGeometry().top()
        self.assertLessEqual(pw.height(), max(space, w.Dock.MIN_STACKED))

    def test_portfolio_is_as_tall_as_its_content_so_nothing_scrolls_when_there_is_room(self):
        self.pw.screen_height = 1000
        self.pw.fit_height()
        self.pw.show()
        QApplication.processEvents()
        self.assertEqual(self.pw.area.verticalScrollBar().maximum(), 0)

    def test_when_there_is_no_room_above_the_portfolio_goes_beside_the_watchlist(self):
        self.pw.close()
        area = QApplication.primaryScreen().availableGeometry()
        self.main.setFixedHeight(area.height() - 40)
        self.main.show_docked()
        self.main.portfolio_button.click()
        pw = w.PORTFOLIO_WINDOWS["window"]
        self.assertEqual(pw.y() + pw.height(), self.main.y() + self.main.height())  # gleiche Unterkante, nicht darüber
        self.assertLessEqual(pw.x(), self.main.x())

    def test_without_the_watchlist_docked_the_portfolio_takes_the_normal_slot(self):
        self.assertFalse(self.main.isVisible())
        self.assertNotIn(self.main, w.Dock.windows)
        area = QApplication.primaryScreen().availableGeometry()
        self.assertEqual(self.pw.x() + self.pw.width(), area.right() + 1 - w.Dock.MARGIN)


def count_color(widget, color, tolerance=14):
    """Zählt die Bildpunkte in der Farbe color (Hex) im Abbild des Widgets."""
    image = widget.grab().toImage()
    wanted = (int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16))
    count = 0
    for y in range(image.height()):
        for x in range(image.width()):
            pixel = image.pixelColor(x, y)
            if (abs(pixel.red() - wanted[0]) <= tolerance and abs(pixel.green() - wanted[1]) <= tolerance
                    and abs(pixel.blue() - wanted[2]) <= tolerance):
                count += 1
    return count


class ChartOverlayTests(unittest.TestCase):
    """Marken und Einstandslinie im Kursdiagramm."""

    def setUp(self):
        qapp()
        self.falling = [(dt.datetime(2026, 1, 1) + dt.timedelta(days=i), 200.0 - i) for i in range(10)]
        self.rising = [(dt.datetime(2026, 1, 1) + dt.timedelta(days=i), 100.0 + i) for i in range(10)]
        self.chart = w.PriceChart()
        self.chart.resize(420, 150)

    def marker(self, index=4, kind="buy", opening=False, price=195.0, text="Kauf 1 Stk"):
        return history.Marker(index, kind, opening, price, text)

    def test_without_overlay_there_is_no_amber_line_and_no_markers(self):
        self.chart.show_points(self.falling)
        self.assertEqual(count_color(self.chart, w.AMBER), 0)
        self.assertEqual(count_color(self.chart, w.GREEN), 0)  # fallender Kurs: die Linie ist rot

    def test_cost_line_is_drawn_in_amber_when_given(self):
        self.chart.show_points(self.falling)
        self.chart.set_overlay([], 195.0)
        self.assertGreater(count_color(self.chart, w.AMBER), 0)
        self.chart.set_overlay([], None)
        self.assertEqual(count_color(self.chart, w.AMBER), 0)

    def test_cost_far_from_the_prices_still_fits_into_the_chart(self):
        self.chart.show_points(self.falling)
        self.chart.set_overlay([], 20.0)  # weit unter allen Kursen
        self.assertGreater(count_color(self.chart, w.AMBER), 0)

    def test_buy_marker_is_green(self):
        self.chart.show_points(self.falling)
        self.chart.set_overlay([self.marker(kind="buy")])
        self.assertGreater(count_color(self.chart, w.GREEN), 0)

    def test_sell_marker_is_red(self):
        self.chart.show_points(self.rising)  # steigender Kurs: die Linie ist grün
        self.assertEqual(count_color(self.chart, w.RED), 0)
        self.chart.set_overlay([self.marker(kind="sell", price=104.0, text="Verkauf 1 Stk")])
        self.assertGreater(count_color(self.chart, w.RED), 0)

    def test_opening_balance_is_a_ring_in_the_accent_color(self):
        self.chart.show_points(self.falling)
        self.assertEqual(count_color(self.chart, w.ACCENT), 0)
        self.chart.set_overlay([self.marker(opening=True)])
        self.assertGreater(count_color(self.chart, w.ACCENT), 0)
        self.assertEqual(count_color(self.chart, w.GREEN), 0)  # kein Kauf-Dreieck für den Startbestand

    def test_new_points_clear_the_overlay(self):
        self.chart.show_points(self.falling)
        self.chart.set_overlay([self.marker()], 195.0)
        self.chart.show_points(self.rising)
        self.assertEqual((self.chart.markers, self.chart.cost), ([], None))

    def test_marker_prices_outside_the_chart_are_kept_inside_the_picture(self):
        self.chart.show_points(self.falling)
        self.chart.set_overlay([self.marker(price=5000.0), self.marker(index=0, price=-5.0)])
        self.assertFalse(self.chart.grab().isNull())

    def test_hovering_a_marker_selects_it_and_leaving_the_chart_resets_it(self):
        self.chart.show()
        self.chart.show_points(self.falling)
        marker = self.marker(index=4)
        self.chart.set_overlay([marker])
        x = int(self.chart._x(4))
        QTest.mouseMove(self.chart, QPoint(x, 40))
        self.assertIs(self.chart.hover_marker, marker)
        QTest.mouseMove(self.chart, QPoint(x + 60, 40))
        self.assertIsNone(self.chart.hover_marker)
        QTest.mouseMove(self.chart, QPoint(x, 40))
        QApplication.sendEvent(self.chart, QEvent(QEvent.Leave))
        self.assertIsNone(self.chart.hover_marker)

    def test_painting_with_a_hovered_marker_works(self):
        self.chart.show_points(self.falling)
        marker = self.marker(opening=True, text="Startbestand 7.36 Stk")
        self.chart.set_overlay([marker])
        self.chart.hover, self.chart.hover_marker = 4, marker
        self.assertFalse(self.chart.grab().isNull())


class DetailHistoryTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.main = w.MainWindow(self.ctl)
        days = [dt.datetime.combine(TODAY - dt.timedelta(days=9 - i), dt.time(12)) for i in range(10)]
        self.points = [(stamp, 100.0 + i) for i, stamp in enumerate(days)]
        patcher = mock.patch.object(sd, "fetch_history", lambda symbol, key="6m": self.points)
        patcher.start()
        self.addCleanup(patcher.stop)

    def detail(self):
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.assertTrue(wait_until(lambda: detail.chart.points is not None))
        return detail

    def lines(self, detail):
        return [detail.history_lines.itemAt(i).widget().text() for i in range(detail.history_lines.count())]

    def test_empty_history_says_so(self):
        detail = self.detail()
        self.assertEqual(self.lines(detail), [])
        self.assertEqual(detail.history_hint.text(), "Noch keine Einträge.")

    def test_opening_balance_is_explained(self):
        self.ctl.start_position("AAPL", 7.36, 0)
        detail = self.detail()
        [line] = self.lines(detail)
        self.assertIn("Startbestand 7.36 Stk", line)
        self.assertIn("Kaufdatum unbekannt", line)
        self.assertIn("Nur Startbestand", detail.history_hint.text())

    def test_real_entries_replace_the_opening_hint_and_newest_come_first(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 1.5, TODAY - dt.timedelta(days=8))
        self.ctl.record_trade("AAPL", "sell", 4, 110, 0, TODAY - dt.timedelta(days=2))
        detail = self.detail()
        first, second = self.lines(detail)
        self.assertTrue(first.startswith("Verkauf 4 Stk"))
        self.assertTrue(second.startswith("Kauf 10 Stk"))
        self.assertIn("2 Einträge seit", detail.history_hint.text())
        self.assertIn("Gebühren 1.50", detail.history_hint.text())

    def test_only_the_five_newest_are_listed(self):
        for i in range(7):
            self.ctl.record_trade("AAPL", "buy", 1, 100, 0, TODAY - dt.timedelta(days=9 - i))
        detail = self.detail()
        lines = self.lines(detail)
        self.assertEqual(len(lines), 6)
        self.assertEqual(lines[-1], "… und 2 weitere")

    def test_history_updates_when_a_trade_is_booked(self):
        detail = self.detail()
        self.ctl.record_trade("AAPL", "buy", 3, 100, 0, TODAY)
        self.assertEqual(len(self.lines(detail)), 1)
        self.assertEqual(detail.chart.markers[0].kind, "buy")

    def test_imported_entries_name_their_source(self):
        self.ctl.import_trades("etoro", [ExternalTrade("1", "AAPL", "buy", 5, 99.0, TODAY - dt.timedelta(days=3))])
        detail = self.detail()
        self.assertTrue(self.lines(detail)[0].endswith("eToro"))

    def test_all_entries_button_opens_the_transaction_list(self):
        detail = self.detail()
        detail.all_entries.click()
        self.assertIn("AAPL", w.TX_WINDOWS)

    def test_chart_shows_markers_for_entries_in_range_and_the_cost_line(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY - dt.timedelta(days=5))
        self.ctl.record_trade("AAPL", "sell", 4, 110, 0, TODAY - dt.timedelta(days=1))
        detail = self.detail()
        self.assertEqual([m.kind for m in detail.chart.markers], ["buy", "sell"])
        self.assertAlmostEqual(detail.chart.cost, 100.0)

    def test_entries_before_the_shown_range_get_no_marker(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY - dt.timedelta(days=200))
        self.assertEqual(self.detail().chart.markers, [])

    def test_opening_balance_marker_is_a_ring(self):
        self.ctl.start_position("AAPL", 10, 0)
        marker = self.detail().chart.markers[0]
        self.assertTrue(marker.opening)

    def test_cost_line_disappears_when_the_position_is_closed_but_markers_stay(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY - dt.timedelta(days=5))
        detail = self.detail()
        self.ctl.record_trade("AAPL", "sell", 10, 110, 0, TODAY - dt.timedelta(days=1))
        self.assertIsNone(detail.chart.cost)
        self.assertEqual(len(detail.chart.markers), 2)

    def test_no_cost_line_without_a_position(self):
        self.assertIsNone(self.detail().chart.cost)

    def test_switching_the_range_keeps_the_markers(self):
        self.ctl.record_trade("AAPL", "buy", 10, 100, 0, TODAY - dt.timedelta(days=5))
        detail = self.detail()
        detail.range_buttons["1m"].click()
        self.assertTrue(wait_until(lambda: detail.chart.markers))
        self.assertEqual(len(detail.chart.markers), 1)

    def test_closed_detail_window_ignores_later_changes(self):
        detail = self.detail()
        detail.close()
        self.ctl.record_trade("AAPL", "buy", 1, 100, 0, TODAY)  # darf keine Fehlermeldung auslösen
        self.assertNotIn("AAPL", self.main.details)


class TransactionSourceDisplayTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.ctl.record_trade("AAPL", "buy", 5, 90, 0, TODAY - dt.timedelta(days=9))
        self.ctl.import_trades("etoro", [ExternalTrade("E1", "AAPL", "buy", 2, 95.0, TODAY - dt.timedelta(days=5))])
        self.ctl.start_position("MSFT", 3, 0)

    def window(self, symbol):
        w.open_transactions(self.ctl, symbol)
        return w.TX_WINDOWS[symbol]

    def row_texts(self, window):
        return [" | ".join(texts(row)) for row in window.findChildren(w.TransactionRow)]

    def test_imported_row_shows_a_source_badge_and_manual_row_does_not(self):
        rows = self.row_texts(self.window("AAPL"))
        self.assertIn("eToro", rows[0])  # neueste zuerst: der Import
        self.assertNotIn("eToro", rows[1])
        self.assertNotIn("Manuell", rows[1])

    def test_opening_row_is_called_opening_balance_and_says_the_date_is_unknown(self):
        [row] = self.row_texts(self.window("MSFT"))
        self.assertIn("Startbestand · 3 Stk", row)
        self.assertIn("Kaufdatum unbekannt", row)
        self.assertIn("eingetragen", row)
        self.assertNotIn("Kauf ·", row)

    def test_opening_hint_is_shown_only_for_opening_only_histories(self):
        self.assertIn("Nur Startbestand", self.window("MSFT").hint.text())
        self.assertFalse(self.window("MSFT").hint.isHidden())
        self.assertTrue(self.window("AAPL").hint.isHidden())

    def test_deleting_an_imported_row_warns_that_the_next_sync_brings_it_back(self):
        window = self.window("AAPL")
        seen = []
        driver = DialogDriver(lambda d: seen.extend(texts(d)))
        window.findChildren(w.TransactionRow)[0].findChildren(QPushButton)[-1].click()
        driver.check()
        self.assertTrue(any("wird beim nächsten Abgleich wieder angelegt" in t for t in seen))

    def test_deleting_a_manual_row_has_no_such_warning(self):
        window = self.window("AAPL")
        seen = []
        driver = DialogDriver(lambda d: seen.extend(texts(d)))
        window.findChildren(w.TransactionRow)[1].findChildren(QPushButton)[-1].click()
        driver.check()
        self.assertFalse(any("Abgleich" in t for t in seen))

    def test_sync_state_of_registered_sources_is_shown(self):
        class Source(TransactionSource):
            name, label = "etoro", "eToro"

        self.ctl.sources["etoro"] = Source()
        window = self.window("AAPL")
        self.assertIn("eToro: zuletzt abgeglichen", window.hint.text())
        self.ctl.store.close()  # nur die Anzeige für eine nie abgeglichene Anbindung prüfen: neue Datenbank
        self.ctl.store = w.Store(os.path.join(os.path.dirname(sd.DB_FILE), "leer.db"))
        window.refresh()
        self.assertIn("eToro: noch nicht abgeglichen", window.hint.text())

    def test_imported_chart_marker_names_its_source(self):
        marker = history.chart_markers(self.ctl.transactions["AAPL"],
                                       [(dt.datetime.combine(TODAY - dt.timedelta(days=d), dt.time(12)), 1.0)
                                        for d in range(10, -1, -1)])
        self.assertTrue(any(m.text.endswith("eToro") for m in marker))


class PerformanceChartTests(unittest.TestCase):
    def setUp(self):
        qapp()
        self.chart = w.PerformanceChart()
        self.chart.resize(420, 150)

    def points(self, values, invested=None, results=None):
        invested = invested or [100.0] * len(values)
        results = results or [v - i for v, i in zip(values, invested)]
        return [history.PerformancePoint(dt.date(2026, 1, 5) + dt.timedelta(days=i), v, inv, 0.0, r, 0.0)
                for i, (v, inv, r) in enumerate(zip(values, invested, results))]

    def test_few_points_show_a_note_instead_of_a_line(self):
        self.chart.show_series([])
        self.assertFalse(self.chart.grab().isNull())
        self.chart.show_series(self.points([100.0]))
        self.assertFalse(self.chart.grab().isNull())
        self.assertEqual(count_color(self.chart, w.GREEN), 0)

    def test_gain_is_drawn_green_and_loss_red(self):
        self.chart.show_series(self.points([100, 110, 120]))
        self.assertGreater(count_color(self.chart, w.GREEN), 0)
        self.assertEqual(count_color(self.chart, w.RED), 0)
        self.chart.show_series(self.points([100, 90, 80]))
        self.assertGreater(count_color(self.chart, w.RED), 0)
        self.assertEqual(count_color(self.chart, w.GREEN), 0)

    def test_a_flat_series_does_not_divide_by_zero(self):
        self.chart.show_series(self.points([100.0, 100.0, 100.0]))
        self.assertFalse(self.chart.grab().isNull())

    def test_hover_shows_value_invested_and_result_of_that_day(self):
        self.chart.show()
        self.chart.show_series(self.points([100, 110, 120]))
        QTest.mouseMove(self.chart, QPoint(int(self.chart._x(1)), 60))
        self.assertEqual(self.chart.hover, 1)
        self.assertFalse(self.chart.grab().isNull())
        QApplication.sendEvent(self.chart, QEvent(QEvent.Leave))
        self.assertIsNone(self.chart.hover)

    def test_new_series_resets_the_hover(self):
        self.chart.show_series(self.points([100, 110]))
        self.chart.hover = 1
        self.chart.show_series(self.points([100, 110, 120]))
        self.assertIsNone(self.chart.hover)


class PortfolioHistoryTests(AppTestCase):
    D1 = dt.date(2026, 1, 5)

    def setUp(self):
        super().setUp()
        self.ctl.fx.add_history("USD", {self.D1: 0.90})
        self.ctl.fx.set_latest("USD", 0.90, dt.datetime.now(), "Test")

    def open(self):
        w.open_portfolio(self.ctl)
        return w.PORTFOLIO_WINDOWS["window"]

    def test_chart_follows_the_ledger_and_the_daily_closes(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        self.assertTrue(wait_until(lambda: self.ctl.closes.get("AAPL")))
        window = self.open()
        expected = self.ctl.performance().points
        self.assertGreaterEqual(len(expected), 2)
        self.assertEqual(window.performance_chart.points, expected)

    def test_result_line_shows_amount_and_percentage_of_the_last_day(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        wait_until(lambda: self.ctl.closes.get("AAPL"))
        window = self.open()
        last = self.ctl.performance().points[-1]
        self.assertEqual(window.performance_result.text(), f"{w.signed_money(last.result)} · {w.signed_percent(last.return_pct)}")

    def test_only_opening_balances_explain_why_the_history_is_short(self):
        self.ctl.start_position("AAPL", 10, 0)
        window = self.open()
        self.assertFalse(window.performance_note.isHidden())
        self.assertIn(f"Der Verlauf beginnt am {TODAY:%d.%m.%Y}", window.performance_note.text())
        self.assertIn("eToro", window.performance_note.text())

    def test_real_entries_name_the_first_day_instead(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        window = self.open()
        self.assertIn("Verlauf ab 05.01.2026, dem ersten Eintrag.", window.performance_note.text())
        self.assertNotIn("Eintragung", window.performance_note.text())

    def test_stocks_without_prices_are_listed(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        with mock.patch.object(sd, "fetch_daily_closes", side_effect=RuntimeError("offline")):
            self.ctl.closes.clear()
            window = self.open()
        self.assertIn("Ohne Kurse oder Wechselkurse im Verlauf: AAPL", window.performance_note.text())

    def test_empty_portfolio_has_no_series_and_no_note(self):
        window = self.open()
        self.assertEqual(window.performance_chart.points, [])
        self.assertEqual(window.performance_result.text(), "")
        self.assertTrue(window.performance_note.isHidden())

    def test_new_daily_closes_update_the_open_window(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, self.D1)
        self.ctl.closes.clear()
        window = self.open()
        self.ctl.changed.disconnect(window.update_timer.start)  # nur das Signal für neue Tageskurse soll wirken
        self.assertEqual(window.performance_chart.points[0].value, 0.0)
        self.ctl.closes["AAPL"] = {self.D1: 100.0, TODAY: 120.0}
        self.ctl.history_changed.emit()
        self.assertTrue(wait_until(lambda: window.performance_chart.points[-1].value > 0))

    def test_closing_the_window_disconnects_it_from_the_history_signal(self):
        window = self.open()
        window.close()
        self.ctl.history_changed.emit()  # darf nichts mehr aufrufen und nicht scheitern
        self.assertNotIn("window", w.PORTFOLIO_WINDOWS)


def enter(widget):
    QApplication.sendEvent(widget, QEnterEvent(QPointF(2, 2), QPointF(2, 2), QPointF(2, 2)))


def leave(widget):
    QApplication.sendEvent(widget, QEvent(QEvent.Leave))


def press(widget, key):
    QApplication.sendEvent(widget, QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier))


def popup_texts(popup):
    return [label.text() for label in popup.findChildren(QLabel) if label.text() and not label.isHidden()]


class TermPopupTests(unittest.TestCase):
    """Die Erklärung eines Fachbegriffs: Hover, Klick, Tastatur, Schließen."""

    def setUp(self):
        qapp()
        self.popup = w.TermPopup.instance()
        self.popup.close_term()
        self.addCleanup(self.popup.close_term)
        self.page = QWidget()
        self.page.resize(700, 500)
        self.page.show()
        self.addCleanup(self.page.close)

    def label(self, key="kgv", text="KGV", note="", x=20, y=20):
        label = QLabel(text, self.page)
        label.move(x, y)
        label.show()
        return w.explain(label, key, note)

    def test_the_explanation_appears_after_the_mouse_rests_and_names_the_term(self):
        label = self.label()
        enter(label)
        self.assertFalse(self.popup.isVisible())  # nicht sofort
        self.assertTrue(wait_until(self.popup.isVisible, 2000))
        self.assertIs(self.popup.owner, label)
        self.assertFalse(self.popup.pinned)
        self.assertEqual(self.popup.title.text(), "KGV")
        self.assertEqual(self.popup.full.text(), glossary.term("kgv").full)
        self.assertEqual(self.popup.text.text(), glossary.term("kgv").text)

    def test_formula_unit_interpretation_and_example_are_shown(self):
        label = self.label()
        enter(label)
        wait_until(self.popup.isVisible, 2000)
        texts_shown = popup_texts(self.popup)
        for heading, value in glossary.term("kgv").sections():
            self.assertIn(heading.upper(), texts_shown)
            self.assertIn(value, texts_shown)

    def test_a_term_without_a_long_form_hides_that_line(self):
        label = self.label("kurs", "Kurs")
        enter(label)
        wait_until(self.popup.isVisible, 2000)
        self.assertTrue(self.popup.full.isHidden())

    def test_leaving_before_the_pause_shows_nothing(self):
        label = self.label()
        enter(label)
        leave(label)
        wait_until(lambda: False, w.HOVER_MS + 250)
        self.assertFalse(self.popup.isVisible())

    def test_leaving_closes_a_hover_explanation(self):
        label = self.label()
        enter(label)
        wait_until(self.popup.isVisible, 2000)
        leave(label)
        self.assertFalse(self.popup.isVisible())

    def test_a_click_keeps_the_explanation_open_even_when_the_mouse_leaves(self):
        label = self.label()
        QTest.mouseClick(label, Qt.LeftButton)
        self.assertTrue(self.popup.isVisible())
        self.assertTrue(self.popup.pinned)
        leave(label)
        self.assertTrue(self.popup.isVisible())

    def test_a_second_click_closes_it(self):
        label = self.label()
        QTest.mouseClick(label, Qt.LeftButton)
        QTest.mouseClick(label, Qt.LeftButton)
        self.assertFalse(self.popup.isVisible())

    def test_a_click_somewhere_else_closes_a_pinned_explanation(self):
        label, other = self.label(), self.label("peg", "PEG", x=200)
        QTest.mouseClick(label, Qt.LeftButton)
        QTest.mouseClick(self.page, Qt.LeftButton, pos=QPoint(400, 300))
        self.assertFalse(self.popup.isVisible())
        self.assertIsNone(other and self.popup.owner)

    def test_a_click_inside_the_explanation_keeps_it(self):
        label = self.label()
        QTest.mouseClick(label, Qt.LeftButton)
        QTest.mouseClick(self.popup.text, Qt.LeftButton)
        self.assertTrue(self.popup.isVisible())

    def test_escape_closes_it(self):
        label = self.label()
        QTest.mouseClick(label, Qt.LeftButton)
        press(label, Qt.Key_Escape)
        self.assertFalse(self.popup.isVisible())

    def test_escape_closes_it_even_when_another_widget_has_the_focus(self):
        label = self.label()
        QTest.mouseClick(label, Qt.LeftButton)  # festgehalten per Maus: der Fokus liegt woanders
        press(self.page, Qt.Key_Escape)
        self.assertFalse(self.popup.isVisible())

    def test_without_a_mouse_tab_reaches_the_term_and_enter_or_space_opens_it(self):
        label = self.label()
        self.assertEqual(label.focusPolicy(), Qt.TabFocus)
        press(label, Qt.Key_Return)
        self.assertTrue(self.popup.isVisible() and self.popup.pinned)
        press(label, Qt.Key_Space)  # zweites Drücken schließt
        self.assertFalse(self.popup.isVisible())
        press(label, Qt.Key_Enter)
        self.assertTrue(self.popup.isVisible())

    def test_f1_opens_the_explanation_for_any_term(self):
        label = self.label()
        press(label, Qt.Key_F1)
        self.assertTrue(self.popup.isVisible())
        self.assertEqual(self.popup.key, "kgv")

    def test_other_keys_do_nothing(self):
        label = self.label()
        press(label, Qt.Key_A)
        self.assertFalse(self.popup.isVisible())

    def test_buttons_keep_their_own_click_and_open_the_explanation_with_f1(self):
        button = QPushButton("Kurs", self.page)
        button.show()
        clicks = []
        button.clicked.connect(lambda: clicks.append(1))
        w.explain(button, "kurs", note="Ein Klick sortiert.")
        QTest.mouseClick(button, Qt.LeftButton)
        self.assertEqual(clicks, [1])
        self.assertFalse(self.popup.isVisible())  # der Klick löst die Schaltfläche aus, keine Erklärung
        press(button, Qt.Key_Return)
        self.assertFalse(self.popup.isVisible())
        press(button, Qt.Key_F1)
        self.assertTrue(self.popup.isVisible())
        self.assertEqual(self.popup.footer.text(), "Ein Klick sortiert.")
        self.assertFalse(self.popup.footer.isHidden())

    def test_buttons_hover_also_opens_the_explanation(self):
        button = QPushButton("Kurs", self.page)
        button.show()
        w.explain(button, "kurs")
        enter(button)
        self.assertTrue(wait_until(self.popup.isVisible, 2000))
        self.assertTrue(self.popup.footer.isHidden())  # ohne Hinweis keine Fußzeile

    def test_the_term_is_underlined_while_hovered_or_focused_but_buttons_are_not(self):
        label = self.label()
        self.assertFalse(label.font().underline())
        enter(label)
        self.assertTrue(label.font().underline())
        leave(label)
        self.assertFalse(label.font().underline())
        QApplication.sendEvent(label, __import__("PySide6.QtGui", fromlist=["QFocusEvent"]).QFocusEvent(QEvent.FocusIn))
        self.assertTrue(label.font().underline())
        button = QPushButton("Kurs", self.page)
        w.explain(button, "kurs")
        enter(button)
        self.assertFalse(button.font().underline())

    def test_the_same_term_reads_the_same_everywhere(self):
        first, second = self.label(), self.label("kgv", "P/E", x=300)
        press(first, Qt.Key_F1)
        one = popup_texts(self.popup)
        self.popup.close_term()
        press(second, Qt.Key_F1)
        self.assertEqual(popup_texts(self.popup), one)

    def test_only_one_explanation_at_a_time(self):
        first, second = self.label(), self.label("peg", "PEG", x=200)
        press(first, Qt.Key_F1)
        press(second, Qt.Key_F1)
        self.assertIs(self.popup.owner, second)
        self.assertEqual(self.popup.title.text(), "PEG-Ratio")

    def test_hiding_the_term_closes_its_explanation(self):
        label = self.label()
        press(label, Qt.Key_F1)
        label.hide()
        self.assertFalse(self.popup.isVisible())

    def test_losing_the_focus_closes_a_pinned_explanation(self):
        from PySide6.QtGui import QFocusEvent
        label = self.label()
        press(label, Qt.Key_F1)
        QApplication.sendEvent(label, QFocusEvent(QEvent.FocusOut))
        self.assertFalse(self.popup.isVisible())

    def test_the_box_stays_completely_on_the_screen(self):
        area = QApplication.primaryScreen().availableGeometry()
        corner = QLabel("KGV")
        corner.show()
        corner.move(area.right() - 20, area.bottom() - 20)
        w.explain(corner, "kgv")
        self.addCleanup(corner.close)
        press(corner, Qt.Key_F1)
        geometry = self.popup.geometry()
        self.assertGreaterEqual(geometry.left(), area.left())
        self.assertGreaterEqual(geometry.top(), area.top())
        self.assertLessEqual(geometry.right(), area.right())
        self.assertLessEqual(geometry.bottom(), area.bottom())

    def test_the_box_is_tall_enough_for_all_its_text(self):
        label = self.label()
        press(label, Qt.Key_F1)
        self.assertGreaterEqual(self.popup.height(), self.popup.layout().totalHeightForWidth(self.popup.width()))

    def test_no_text_is_cut_off_at_the_bottom_for_any_term(self):
        """Regression: Die Höhe wurde berechnet, bevor die neuen Zeilen ihre Schrift hatten; der letzte Satz fehlte."""
        label = self.label()
        for key in glossary.GLOSSARY:
            w.explain(label, key)
            self.popup.close_term()
            press(label, Qt.Key_F1)
            for child in self.popup.findChildren(QLabel):
                child.ensurePolished()
            self.popup.layout().activate()
            needed = self.popup.layout().totalHeightForWidth(self.popup.width())
            self.assertGreaterEqual(self.popup.height(), needed, key)
            for child in self.popup.findChildren(QLabel):
                if child.isVisible() and child.wordWrap():
                    self.assertGreaterEqual(child.height(), child.heightForWidth(child.width()), f"{key}: {child.text()[:30]}")

    def test_explaining_again_rebinds_instead_of_adding_a_second_handler(self):
        label = self.label("kgv")
        w.explain(label, "kuv")
        press(label, Qt.Key_F1)
        self.assertEqual(self.popup.key, "kuv")
        self.assertEqual(len(label.findChildren(w.TermAnchor)), 1)

    def test_an_unknown_key_fails_right_away(self):
        with self.assertRaises(KeyError):
            w.explain(QLabel("x"), "gibt-es-nicht")

    def test_the_explanation_text_is_also_available_for_screen_readers(self):
        label = self.label()
        self.assertEqual(label.accessibleDescription(), glossary.term("kgv").text)

    def test_terms_show_the_help_cursor(self):
        self.assertEqual(self.label().cursor().shape(), Qt.WhatsThisCursor)


class TermLinkTests(unittest.TestCase):
    """Einzelne Wörter in einem Text, zum Beispiel „Einstand“ in der Positionszeile."""

    def setUp(self):
        qapp()
        self.popup = w.TermPopup.instance()
        self.popup.close_term()
        self.addCleanup(self.popup.close_term)
        self.label = w.enable_term_links(QLabel(f"10 Stück · {w.term_link('Einstand', 'einstand')} 80.00"))
        self.label.show()
        self.addCleanup(self.label.close)

    def test_the_label_is_set_up_for_mouse_and_keyboard(self):
        self.assertEqual(self.label.textFormat(), Qt.RichText)
        flags = self.label.textInteractionFlags()
        self.assertTrue(flags & Qt.LinksAccessibleByMouse and flags & Qt.LinksAccessibleByKeyboard)
        self.assertNotEqual(self.label.focusPolicy(), Qt.NoFocus)

    def test_links_are_bright_not_the_default_blue(self):
        self.assertEqual(self.label.palette().color(w.QPalette.Link).name(), w.QColor(w.TEXT).name())

    def test_hovering_the_word_shows_its_explanation_after_the_pause(self):
        self.label.linkHovered.emit("term:einstand")
        self.assertFalse(self.popup.isVisible())
        self.assertTrue(wait_until(self.popup.isVisible, 2000))
        self.assertEqual(self.popup.title.text(), "Einstandskurs")
        self.assertIs(self.popup.owner, self.label)
        self.assertFalse(self.popup.pinned)

    def test_moving_away_from_the_word_closes_it(self):
        self.label.linkHovered.emit("term:einstand")
        wait_until(self.popup.isVisible, 2000)
        self.label.linkHovered.emit("")
        self.assertFalse(self.popup.isVisible())

    def test_moving_away_before_the_pause_shows_nothing(self):
        self.label.linkHovered.emit("term:einstand")
        self.label.linkHovered.emit("")
        wait_until(lambda: False, w.HOVER_MS + 250)
        self.assertFalse(self.popup.isVisible())

    def test_activating_the_word_pins_the_explanation_and_a_second_time_closes_it(self):
        self.label.linkActivated.emit("term:einstand")
        self.assertTrue(self.popup.isVisible() and self.popup.pinned)
        self.label.linkActivated.emit("term:einstand")
        self.assertFalse(self.popup.isVisible())

    def test_a_pinned_explanation_survives_the_mouse_leaving_the_word(self):
        self.label.linkActivated.emit("term:einstand")
        self.label.linkHovered.emit("")
        self.assertTrue(self.popup.isVisible())

    def test_other_links_and_unknown_terms_are_ignored(self):
        for url in ("https://example.com", "term:gibt-es-nicht", ""):
            self.label.linkActivated.emit(url)
            self.label.linkHovered.emit(url)
        wait_until(lambda: False, w.HOVER_MS + 250)
        self.assertFalse(self.popup.isVisible())

    def test_enabling_twice_does_not_connect_twice(self):
        w.enable_term_links(self.label)
        self.assertEqual(len(self.label.findChildren(w.TermLinks)), 1)

    def test_escape_closes_a_pinned_word_explanation(self):
        self.label.linkActivated.emit("term:einstand")
        QApplication.sendEvent(self.label, QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
        self.assertFalse(self.popup.isVisible())

    def test_the_text_without_markup_reads_normally(self):
        self.assertEqual(plain(self.label), "10 Stück · Einstand 80.00")


class TermWiringTests(AppTestCase):
    """Die Begriffe sind an den richtigen Stellen der echten Fenster angebunden."""

    def setUp(self):
        super().setUp()
        self.main = w.MainWindow(self.ctl)
        self.addCleanup(w.TermPopup.instance().close_term)

    @staticmethod
    def anchors(widget):
        """Alle erklärbaren Widgets eines Fensters: Schlüssel -> Widgets."""
        found = {}
        for child in widget.findChildren(QWidget):
            anchor = getattr(child, "_term_anchor", None)
            if anchor is not None:
                found.setdefault(anchor.key, []).append(child)
        return found

    def links(self, label):
        return [glossary.key_of_link(url) for url in __import__("re").findall(r'href="([^"]+)"', label.text())]

    def test_every_sortable_column_header_explains_itself_and_still_sorts(self):
        for key, term in w.HEAD_TERMS.items():
            button = self.main.heads[key]
            self.assertEqual(button._term_anchor.key, term)
            self.assertIn("sortiert", button._term_anchor.note)
        self.assertFalse(hasattr(self.main.heads["symbol"], "_term_anchor"))
        self.main.heads["price"].click()
        self.assertEqual(self.main.sort_key, "price")

    def test_detail_window_captions_and_figures_explain_themselves(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY)
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        keys = set(self.anchors(detail))
        self.assertTrue({"position", "gv_prozent", "gv", "historie", "termin"} <= keys)
        self.assertEqual(detail.pl_percent._term_anchor.key, "gv_prozent")
        self.assertEqual(detail.pl_amount._term_anchor.key, "gv")

    def test_the_position_line_explains_einstand_and_realized(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY - dt.timedelta(days=1))
        self.ctl.record_trade("AAPL", "sell", 2, 100, 0, TODAY)
        self.main.open_detail("AAPL")
        line = self.main.details["AAPL"].position_info
        self.assertEqual(self.links(line), ["einstand", "realisiert"])
        self.assertIn("Einstand 80.00", plain(line))

    def test_the_position_line_without_position_explains_realized_profit(self):
        self.ctl.record_trade("AAPL", "buy", 2, 80, 0, TODAY - dt.timedelta(days=1))
        self.ctl.record_trade("AAPL", "sell", 2, 100, 0, TODAY)
        self.main.open_detail("AAPL")
        self.assertEqual(self.links(self.main.details["AAPL"].position_info), ["realisiert"])

    def test_the_event_title_explains_the_kind_of_event(self):
        events = [(TODAY + dt.timedelta(days=7), "Ex-Dividende")]
        with mock.patch.object(sd, "fetch_events", lambda s: events):
            self.main.open_detail("AAPL")
            detail = self.main.details["AAPL"]
            self.assertTrue(wait_until(lambda: detail.event_title.text() == "Ex-Dividende"))
        self.assertEqual(detail.event_title._term_anchor.key, "ex_dividende")

    def test_each_event_kind_has_its_own_explanation(self):
        for kind, key in w.EVENT_TERMS.items():
            events = [(TODAY + dt.timedelta(days=3), kind)]
            with mock.patch.object(sd, "fetch_events", lambda s, events=events: events):
                self.main.open_detail("MSFT")
                detail = self.main.details["MSFT"]
                self.assertTrue(wait_until(lambda: detail.event_title.text() == kind))
                self.assertEqual(detail.event_title._term_anchor.key, key)
                detail.close()

    def test_portfolio_window_explains_its_figures(self):
        self.ctl.fx.add_history("USD", {TODAY: 0.9})
        self.ctl.fx.set_latest("USD", 0.9, dt.datetime.now(), "Test")
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY)
        w.open_portfolio(self.ctl)
        window = w.PORTFOLIO_WINDOWS["window"]
        self.assertTrue({"gesamtwert", "unrealisiert", "realisiert", "gesamtergebnis", "verlauf", "aufteilung"}
                        <= set(self.anchors(window)))
        self.assertEqual(self.links(window.total_sub), ["investiert"])
        self.assertEqual(self.links(window.unrealized.sub), ["kursgewinn", "waehrungseffekt"])
        self.assertEqual(self.links(window.realized.sub), ["kursgewinn", "waehrungseffekt"])
        self.assertEqual(self.links(window.result.sub), ["gesamtrendite"])

    def test_transactions_window_explains_opening_balance_and_realized_profit(self):
        self.ctl.start_position("AAPL", 3, 0)
        self.ctl.record_trade("MSFT", "buy", 5, 90, 0, TODAY - dt.timedelta(days=2))
        self.ctl.record_trade("MSFT", "sell", 2, 100, 0, TODAY - dt.timedelta(days=1))
        w.open_transactions(self.ctl, "AAPL")
        w.open_transactions(self.ctl, "MSFT")
        self.assertIn("startbestand", self.anchors(w.TX_WINDOWS["AAPL"]))
        self.assertIn("realisiert", self.anchors(w.TX_WINDOWS["MSFT"]))
        self.assertNotIn("startbestand", self.anchors(w.TX_WINDOWS["MSFT"]))

    def test_the_old_plain_tooltip_on_explained_headers_is_gone_so_only_one_box_appears(self):
        for key in w.HEAD_TERMS:
            self.assertEqual(self.main.heads[key].toolTip(), "")


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

    def test_position_tile_has_no_buttons_the_position_changes_only_via_the_watchlist_menu(self):
        self.main.open_detail("AAPL")
        tile = self.main.details["AAPL"].position_card
        self.assertEqual(tile.findChildren(QPushButton), [])

    def test_detail_shows_profit_cost_and_value(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY)
        self.main.open_detail("AAPL")
        detail = self.main.details["AAPL"]
        self.assertEqual(detail.pl_percent.text(), "+25.00 %")
        self.assertIn("10 Stück", plain(detail.position_info))
        self.assertIn("Einstand 80.00", plain(detail.position_info))
        self.assertIn("Wert 1000.00 USD", plain(detail.position_info))

    def test_detail_without_position_mentions_realized_profit_of_closed_trades(self):
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY - dt.timedelta(days=1))
        self.ctl.record_trade("AAPL", "sell", 10, 100, 0, TODAY)
        self.main.open_detail("AAPL")
        info = plain(self.main.details["AAPL"].position_info)
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

    def test_market_lamp_shows_open_extended_closed_and_hides_without_state(self):
        card = self.main.cards["AAPL"]
        for state, tip in (("open", "Börse geöffnet"), ("extended", "Vor- oder Nachbörse"),
                           ("closed", "Börse geschlossen")):
            self.ctl.quotes["AAPL"]["market_state"] = state
            self.ctl.changed.emit()
            self.assertEqual((card.market_state, card.lamp.toolTip()), (state, tip))
        self.ctl.quotes["AAPL"].pop("market_state")
        self.ctl.changed.emit()
        self.assertIsNone(card.market_state)
        self.assertEqual(card.lamp.toolTip(), "")

    def test_market_lamp_is_off_for_a_stale_quote(self):
        self.ctl.quotes["AAPL"]["market_state"] = "open"
        self.ctl.quote_times["AAPL"] = dt.datetime.now() - dt.timedelta(days=3)
        self.ctl.changed.emit()
        self.assertIsNone(self.main.cards["AAPL"].market_state)

    def test_clicking_a_selected_row_deselects_it_and_closes_its_detail_window(self):
        card = self.main.cards["AAPL"]
        card.clicked.emit("AAPL")
        detail = self.main.details["AAPL"]
        self.assertTrue(card.property("open"))
        card.clicked.emit("AAPL")
        self.assertNotIn("AAPL", self.main.details)
        self.assertFalse(card.property("open"))
        self.assertFalse(detail.isVisible())
        self.assertNotIn(detail, w.Dock.windows)
        card.clicked.emit("AAPL")  # und noch einmal öffnen geht wieder
        self.assertIn("AAPL", self.main.details)

    def test_card_is_highlighted_while_its_detail_window_is_open(self):
        card, other = self.main.cards["AAPL"], self.main.cards["MSFT"]
        self.assertFalse(card.property("open"))
        self.main.open_detail("AAPL")
        self.assertTrue(card.property("open"))
        self.assertFalse(other.property("open"))
        self.main.open_detail("MSFT")
        self.assertTrue(card.property("open") and other.property("open"))
        self.main.details["AAPL"].close()
        self.assertFalse(card.property("open"))
        self.assertTrue(other.property("open"))
        self.ctl.changed.emit()  # eine Aktualisierung darf die Markierung nicht verlieren
        self.assertTrue(other.property("open"))

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


class PinnedBoxTests(AppTestCase):
    """Angeheftete Boxen: Reißzwecke oben rechts, Zustand in der Datenbank, Öffnen beim Start."""

    def setUp(self):
        super().setUp()
        self.main = w.MainWindow(self.ctl)

    def open_everything(self):
        self.main.show_docked()
        self.main.open_detail("AAPL")
        w.open_transactions(self.ctl, "AAPL")
        w.open_portfolio(self.ctl)
        return {"detail:AAPL": self.main.details["AAPL"], "tx:AAPL": w.TX_WINDOWS["AAPL"],
                "portfolio": w.PORTFOLIO_WINDOWS["window"]}

    def test_every_box_has_a_pin_button_that_starts_unpinned(self):
        for key, window in self.open_everything().items():
            self.assertFalse(window.pin_button.isChecked(), key)
            self.assertIn("Anheften", window.pin_button.toolTip())

    def test_the_watchlist_has_no_pin_button(self):
        self.assertFalse(hasattr(self.main, "pin_button"))

    def test_pin_button_sits_right_before_the_close_button(self):
        self.main.open_detail("AAPL")
        window = self.main.details["AAPL"]
        header = [b for b in window.findChildren(QPushButton) if b.objectName() == "icon"][:2]
        self.assertIs(header[0], window.pin_button)
        self.assertEqual(header[1].text(), "✕")

    def test_clicking_the_pin_saves_it_and_clicking_again_removes_it(self):
        windows = self.open_everything()
        for window in windows.values():
            window.pin_button.click()
        self.assertEqual(sorted(self.ctl.pinned), sorted(windows))
        again = w.Controller()  # ein Neustart liest dieselbe Datenbank
        self.addCleanup(again.shutdown)
        self.addCleanup(again.store.close)
        self.assertEqual(sorted(again.pinned), sorted(windows))
        windows["detail:AAPL"].pin_button.click()
        self.assertNotIn("detail:AAPL", self.ctl.store.meta(self.ctl.PINS_KEY))

    def test_pinned_state_is_shown_when_the_box_opens_again(self):
        self.ctl.set_pinned("detail:AAPL", True)
        self.main.open_detail("AAPL")
        self.assertTrue(self.main.details["AAPL"].pin_button.isChecked())

    def test_restore_opens_all_pinned_boxes_and_nothing_else(self):
        for key in ("detail:AAPL", "tx:MSFT", "portfolio"):
            self.ctl.set_pinned(key, True)
        w.restore_pinned(self.ctl, self.main)
        self.assertEqual(set(self.main.details), {"AAPL"})
        self.assertEqual(set(w.TX_WINDOWS), {"MSFT"})
        self.assertIn("window", w.PORTFOLIO_WINDOWS)

    def test_restored_pinned_portfolio_sits_above_the_watchlist_like_a_manually_opened_one(self):
        self.ctl.set_pinned("portfolio", True)
        self.main.setFixedHeight(200)
        self.main.show_docked()
        w.restore_pinned(self.ctl, self.main)
        pw = w.PORTFOLIO_WINDOWS["window"]
        self.assertIs(pw.dock_above, self.main)
        self.assertEqual((pw.x(), pw.width()), (self.main.x(), self.main.width()))
        self.assertLessEqual(pw.y() + pw.height(), self.main.y() - w.Dock.GAP)

    def test_opening_an_already_open_portfolio_from_the_watchlist_moves_it_above(self):
        w.open_portfolio(self.ctl)  # ohne Bezug, also links daneben
        self.main.setFixedHeight(200)
        self.main.show_docked()
        w.open_portfolio(self.ctl, above=self.main)
        self.assertIs(w.PORTFOLIO_WINDOWS["window"].dock_above, self.main)
        self.assertEqual(w.PORTFOLIO_WINDOWS["window"].width(), self.main.width())

    def test_restore_is_idempotent_and_skips_unknown_symbols(self):
        for key in ("detail:GONE", "tx:GONE", "detail:AAPL"):
            self.ctl.set_pinned(key, True)
        w.restore_pinned(self.ctl, self.main)
        w.restore_pinned(self.ctl, self.main)
        self.assertEqual(set(self.main.details), {"AAPL"})
        self.assertEqual(len([x for x in w.Dock.windows if isinstance(x, w.DetailWindow)]), 1)
        self.assertEqual(w.TX_WINDOWS, {})

    def test_tray_click_closes_everything_and_the_next_click_opens_the_watchlist_and_pinned_boxes_only(self):
        windows = self.open_everything()
        windows["detail:AAPL"].pin_button.click()
        self.assertTrue(w.widget_is_open(self.main))
        w.toggle_widget(self.ctl, self.main)  # alles zu, angeheftet oder nicht, auch die Watchlist
        self.assertFalse(w.widget_is_open(self.main))
        self.assertEqual((self.main.details, w.TX_WINDOWS, w.PORTFOLIO_WINDOWS), ({}, {}, {}))
        self.assertFalse(self.main.isVisible())
        self.assertEqual(self.ctl.pinned, ["detail:AAPL"])
        w.toggle_widget(self.ctl, self.main)  # Watchlist und die angeheftete Box, sonst nichts
        self.assertTrue(self.main.isVisible())
        self.assertEqual(set(self.main.details), {"AAPL"})
        self.assertEqual((w.TX_WINDOWS, w.PORTFOLIO_WINDOWS), ({}, {}))
        w.toggle_widget(self.ctl, self.main)
        self.assertFalse(w.widget_is_open(self.main))

    def test_the_watchlist_always_opens_even_without_any_pinned_box(self):
        self.assertEqual(self.ctl.pinned, [])
        w.open_widget(self.ctl, self.main)
        self.assertTrue(self.main.isVisible())
        w.close_widget(self.main)
        self.assertFalse(self.main.isVisible())
        w.open_widget(self.ctl, self.main)
        self.assertTrue(self.main.isVisible())

    def test_a_stale_main_pin_from_an_earlier_version_is_ignored(self):
        self.ctl.set_pinned("main", True)
        w.close_widget(self.main)
        w.open_widget(self.ctl, self.main)
        self.assertTrue(self.main.isVisible())
        self.assertEqual((self.main.details, w.TX_WINDOWS), ({}, {}))

    def test_closing_a_pinned_box_keeps_it_pinned(self):
        self.ctl.set_pinned("detail:AAPL", True)
        self.main.open_detail("AAPL")
        self.main.details["AAPL"].close()
        self.assertIn("detail:AAPL", self.ctl.pinned)

    def test_removing_a_stock_unpins_its_boxes(self):
        for key in ("detail:AAPL", "tx:AAPL", "detail:MSFT"):
            self.ctl.set_pinned(key, True)
        self.ctl.remove("AAPL")
        self.assertEqual(self.ctl.pinned, ["detail:MSFT"])

    def test_broken_stored_value_means_nothing_is_pinned(self):
        self.ctl.store.set_meta(self.ctl.PINS_KEY, "kaputt{")
        again = w.Controller()
        self.addCleanup(again.shutdown)
        self.addCleanup(again.store.close)
        self.assertEqual(again.pinned, [])


class SingleInstanceTests(unittest.TestCase):
    @staticmethod
    def free_port():
        """Ein freier Port statt eines festen, damit gleichzeitige Testläufe sich nicht stören."""
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            return probe.getsockname()[1]

    def test_second_claim_fails_until_the_first_is_released(self):
        port = self.free_port()
        first = w.claim_single_instance(port)
        self.assertIsNotNone(first)
        try:
            self.assertIsNone(w.claim_single_instance(port))
        finally:
            first.close()
        again = w.claim_single_instance(port)
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
