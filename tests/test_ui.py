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
        self.assertTrue(card.day.isHidden())
        self.assertTrue(card.pos.isHidden())

    def test_negative_change_uses_down_arrow(self):
        self.ctl.quotes["AAPL"]["change_pct"] = -2.0
        self.ctl.changed.emit()
        self.assertEqual(self.main.cards["AAPL"].day.text(), "▼ -2.00 %")

    def test_card_shows_position_and_profit_only_when_held(self):
        card = self.main.cards["AAPL"]
        self.assertTrue(card.pos.isHidden())
        self.ctl.record_trade("AAPL", "buy", 10, 80, 0, TODAY)
        self.assertFalse(card.pos.isHidden())
        self.assertEqual(card.pos.text(), "10 Stk · +25.00 %")
        self.assertTrue(self.main.cards["MSFT"].pos.isHidden())

    def test_card_shows_next_event(self):
        self.ctl.events["AAPL"] = [(TODAY + dt.timedelta(days=5), "Ex-Dividende")]
        self.ctl.changed.emit()
        self.assertTrue(self.main.cards["AAPL"].sub.text().startswith("Ex-Div."))
        self.ctl.events["MSFT"] = []
        self.ctl.changed.emit()
        self.assertEqual(self.main.cards["MSFT"].sub.text(), "kein Termin")

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
        self.main.entry.setText(text)
        self.main.add_symbol()

    def test_entry_is_cleared_and_asks_for_symbol_or_name(self):
        self.assertIn("Name", self.main.entry.placeholderText())
        self.add("ORA.US")
        self.assertEqual(self.main.entry.text(), "")

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
