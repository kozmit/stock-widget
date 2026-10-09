"""Startet das Widget mit offenem Detail- und Transaktionsfenster und beendet es. Exit-Code 0 heißt sauber.

Wird von tests/test_ui.py als eigener Prozess gestartet, weil ein Absturz beim Beenden sonst den
ganzen Testlauf mitreißt.
"""
import datetime as dt
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stock_data as sd

tmp = tempfile.mkdtemp()
sd.DATA_DIR = tmp
sd.DB_FILE = os.path.join(tmp, "probe.db")
sd.LEGACY_FILE = os.path.join(tmp, "watchlist.json")
sd.fetch_quote = lambda symbol: {"price": 100.0, "change_pct": 1.0, "currency": "USD"}
sd.fetch_events = lambda symbol: []
sd.fetch_news = lambda symbol, count=15: []
sd.fetch_daily_closes = lambda symbol, start: {start: 100.0}

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import stock_widget as w

app = QApplication([])
app.setQuitOnLastWindowClosed(False)
app.setStyleSheet(w.STYLE)
ctl = w.Controller()
window = w.MainWindow(ctl)
app.aboutToQuit.connect(lambda: w.close_all_windows(ctl))
window.show_docked()
ctl.start()


def open_windows():
    ctl.record_trade("AAPL", "buy", 10, 100, 0, dt.date(2026, 1, 1))
    ctl.record_trade("AAPL", "sell", 2, 110, 0, dt.date(2026, 2, 1))
    window.open_detail("AAPL")
    w.open_transactions(ctl, "AAPL")


QTimer.singleShot(400, open_windows)
QTimer.singleShot(1500, app.quit)
code = app.exec()
ctl.store.close()
sys.exit(code)
