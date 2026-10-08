"""Gemeinsame Hilfen für die Tests: temporäre Datenbank, kein Netzwerk, Dialoge fernsteuern."""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from PySide6.QtCore import QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import stock_data as sd
import stock_widget as w

QUOTE = {"price": 100.0, "change_pct": 1.5, "currency": "USD"}


def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(w.STYLE)
    return app


# Schreibweisen, die Yahoo nicht kennt (Namen und fremde Börsenendungen)
UNKNOWN = {"BAD", "DRONESHIELD", "ORA.US", "ABBN.ZU", "DRO.AU"}


def fake_quote(symbol):
    if symbol in UNKNOWN or " " in symbol or symbol.endswith(tuple(sd.SUFFIX_ALIASES)):
        raise ValueError("keine Kursdaten")
    return dict(QUOTE)


def wait_until(condition, timeout=3000):
    waited = 0
    while not condition() and waited < timeout:
        QTest.qWait(20)
        waited += 20
    return condition()


class DialogDriver:
    """Führt callback(dialog) aus, sobald ein modaler Dialog offen ist, und schließt ihn danach.
    Eine Notbremse schließt hängende Dialoge nach 5 Sekunden. Fehler im Callback meldet check()."""

    def __init__(self, callback, delay=150):
        self.errors = []
        self.callback = callback
        QTimer.singleShot(delay, self.step)
        QTimer.singleShot(delay + 5000, self.emergency)

    def step(self):
        dialog = QApplication.activeModalWidget()
        try:
            self.callback(dialog)
        except Exception as exc:  # wird in check() erneut geworfen
            self.errors.append(exc)
        finally:
            if dialog is not None and QApplication.activeModalWidget() is dialog:
                dialog.reject()

    def emergency(self):
        dialog = QApplication.activeModalWidget()
        if dialog is not None:
            self.errors.append(AssertionError("Dialog blieb offen"))
            dialog.reject()

    def check(self):
        if self.errors:
            raise self.errors[0]


class AppTestCase(unittest.TestCase):
    """Controller mit temporärer Datenbank. Ohne start(), also ohne Netzwerk; Kurse setzen die Tests selbst."""
    legacy = None  # Inhalt der alten watchlist.json (Liste oder dict), falls vorhanden

    def setUp(self):
        self.app = qapp()
        self.dir = tempfile.mkdtemp()
        for target, value in (("DATA_DIR", self.dir), ("DB_FILE", os.path.join(self.dir, "t.db")),
                              ("LEGACY_FILE", os.path.join(self.dir, "watchlist.json"))):
            patcher = mock.patch.object(sd, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, fake in (("fetch_quote", fake_quote), ("fetch_events", lambda s: []),
                           ("fetch_news", lambda s, count=15: []), ("search_symbols", lambda q, count=8: [])):
            patcher = mock.patch.object(sd, name, fake)
            patcher.start()
            self.addCleanup(patcher.stop)
        if self.legacy is not None:
            with open(sd.LEGACY_FILE, "w", encoding="utf-8") as f:
                json.dump(self.legacy, f)
        self.addCleanup(self.cleanup)
        self.ctl = w.Controller()
        self.ctl.quotes = {s: dict(QUOTE) for s in self.ctl.symbols}

    def cleanup(self):
        for window in list(w.Dock.windows):
            window.close()
        w.Dock.windows.clear()
        w.TX_WINDOWS.clear()
        w.PIN["on"] = True
        self.app.processEvents()
        self.ctl.shutdown()
        self.ctl.store.close()
        shutil.rmtree(self.dir, ignore_errors=True)
