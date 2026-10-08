"""Aktien-Widget: Watchlist mit Kursen, Positionen, News und dem nächsten wichtigen Termin je Aktie.

Oberfläche mit Qt (PySide6): rahmenlose Fenster mit runden Ecken, alle Fenster und Dialoge
öffnen unten rechts im Bildschirm und reihen sich nebeneinander auf.
Start: python stock_widget.py   (mit --tray startet es nur als Icon im Infobereich)
"""
import datetime as dt
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, Qt, QTimer, QUrl, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGraphicsDropShadowEffect, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSystemTrayIcon, QVBoxLayout, QWidget)

import ledger
import stock_data as sd
from store import Store

REFRESH_SECONDS = 60

BG, SURFACE, SURFACE2 = "#14161c", "#1d2029", "#272b39"
TEXT, MUTED, ACCENT = "#eceef4", "#8a90a6", "#6c8cff"
GREEN, RED = "#34d399", "#f87171"

STYLE = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 13px; color: {TEXT}; }}
QLabel {{ background: transparent; }}
QFrame#panel {{ background: {BG}; border: 1px solid #2b2f3d; border-radius: 20px; }}
QFrame#card {{ background: {SURFACE}; border-radius: 10px; }}
QFrame#card:hover {{ background: {SURFACE2}; }}
QFrame#plain {{ background: {SURFACE}; border-radius: 16px; }}
QWidget#clear {{ background: transparent; }}
QLineEdit {{ background: {SURFACE}; border: 1px solid transparent; border-radius: 14px; padding: 9px 14px;
             selection-background-color: {ACCENT}; }}
QLineEdit:focus {{ border: 1px solid {ACCENT}; }}
QPushButton {{ background: {SURFACE2}; border: none; border-radius: 14px; padding: 9px 16px; font-weight: 600; }}
QPushButton:hover {{ background: #323749; }}
QPushButton:disabled {{ color: #565b70; background: {SURFACE}; }}
QPushButton#primary {{ background: {ACCENT}; color: white; }}
QPushButton#primary:hover {{ background: #839eff; }}
QPushButton#icon {{ background: transparent; border-radius: 12px; padding: 0; min-width: 30px; max-width: 30px;
                    min-height: 30px; max-height: 30px; color: {MUTED}; font-size: 14px; }}
QPushButton#icon:hover {{ background: {SURFACE2}; color: {TEXT}; }}
QPushButton#icon:checked {{ color: {ACCENT}; }}
QPushButton#head {{ background: transparent; border-radius: 6px; padding: 2px 0; color: {MUTED}; font-size: 10px; font-weight: 700; }}
QPushButton#head:hover {{ color: {TEXT}; }}
QPushButton#option {{ text-align: left; padding: 11px 16px; background: {SURFACE}; font-weight: 600; }}
QPushButton#option:hover {{ background: {SURFACE2}; }}
QPushButton#option:focus {{ border: 1px solid {ACCENT}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 4px 0; }}
QScrollBar::handle:vertical {{ background: #333849; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QMenu {{ background: {SURFACE}; border: 1px solid #2b2f3d; border-radius: 12px; padding: 6px; }}
QMenu::item {{ padding: 8px 20px; border-radius: 8px; }}
QMenu::item:selected {{ background: {SURFACE2}; }}
QMenu::separator {{ height: 1px; background: #2b2f3d; margin: 4px 8px; }}
"""


# ---------- Anordnung: alles unten rechts, nebeneinander ----------

class Dock:
    """Verwaltet die offenen Fenster: das erste sitzt ganz rechts unten, jedes neue links daneben."""
    MARGIN, GAP = 6, 4
    windows = []
    frozen = False

    @classmethod
    def add(cls, window):
        if window not in cls.windows:
            cls.windows.append(window)
        cls.arrange()

    @classmethod
    def remove(cls, window):
        if window in cls.windows:
            cls.windows.remove(window)
        cls.arrange()

    @classmethod
    def arrange(cls):
        if cls.frozen:
            return
        area = QApplication.primaryScreen().availableGeometry()
        right = area.right() + 1 - cls.MARGIN
        bottom = area.bottom() + 1 - cls.MARGIN
        for window in cls.windows:
            window.move(max(right - window.width(), area.left()), bottom - window.height())
            right -= window.width() + cls.GAP


PIN = {"on": True}


def apply_pin():
    """Setzt 'im Vordergrund' für alle offenen Fenster; die Reihenfolge bleibt erhalten."""
    order = list(Dock.windows)
    Dock.frozen = True
    for window in order:
        window.setWindowFlag(Qt.WindowStaysOnTopHint, PIN["on"])
        window.show()
    Dock.windows[:] = order
    Dock.frozen = False
    Dock.arrange()


# ---------- Bausteine ----------

class DragHeader(QWidget):
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()


def make_panel(window, title, on_close, extras=(), always_on_top=False):
    """Macht aus window ein rahmenloses, rundes Fenster mit Schatten und Kopfzeile.
    Gibt das Layout für den Inhalt zurück."""
    flags = Qt.FramelessWindowHint | Qt.Tool
    if PIN["on"] or always_on_top:
        flags |= Qt.WindowStaysOnTopHint
    window.setWindowFlags(flags)
    window.setAttribute(Qt.WA_TranslucentBackground)

    outer = QVBoxLayout(window)
    outer.setContentsMargins(14, 14, 14, 14)
    panel = QFrame()
    panel.setObjectName("panel")
    shadow = QGraphicsDropShadowEffect(panel)
    shadow.setBlurRadius(30)
    shadow.setOffset(0, 6)
    shadow.setColor(QColor(0, 0, 0, 170))
    panel.setGraphicsEffect(shadow)
    outer.addWidget(panel)

    column = QVBoxLayout(panel)
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(0)

    header = DragHeader()
    row = QHBoxLayout(header)
    row.setContentsMargins(20, 14, 12, 6)
    caption = QLabel(title)
    caption.setStyleSheet("font-size: 16px; font-weight: 700;")
    row.addWidget(caption)
    row.addStretch()
    for widget in extras:
        row.addWidget(widget)
    close = QPushButton("✕")
    close.setObjectName("icon")
    close.setCursor(Qt.PointingHandCursor)
    close.clicked.connect(on_close)
    row.addWidget(close)
    column.addWidget(header)

    body = QVBoxLayout()
    body.setContentsMargins(16, 6, 16, 16)
    body.setSpacing(10)
    column.addLayout(body)
    return body


def make_menu():
    menu = QMenu()
    menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
    menu.setAttribute(Qt.WA_TranslucentBackground)
    return menu


def style_pill(label, color, strong=False):
    c = QColor(color)
    alpha = 70 if strong else 36
    label.setStyleSheet(f"background: rgba({c.red()},{c.green()},{c.blue()},{alpha}); color: {color}; "
                        "border-radius: 10px; padding: 2px 9px; font-size: 11px; font-weight: 700;")


FLASH_MS = 1100


def flash(widget, color, base=SURFACE):
    """Lässt einen Rahmen kurz in color aufleuchten und blendet ihn zur Grundfarbe zurück."""
    previous = getattr(widget, "_flash", None)
    if previous:
        previous.stop()
    start, end, name = QColor(color), QColor(base), widget.objectName()

    def apply(strength):
        mix = strength * 0.6
        mixed = QColor(*(round(b + (c - b) * mix) for c, b in
                         ((start.red(), end.red()), (start.green(), end.green()), (start.blue(), end.blue()))))
        widget.setStyleSheet(f"QFrame#{name} {{ background: {mixed.name()}; }}")

    animation = QVariantAnimation(widget)
    animation.setStartValue(1.0)
    animation.setEndValue(0.0)
    animation.setDuration(FLASH_MS)
    animation.setEasingCurve(QEasingCurve.OutCubic)
    animation.valueChanged.connect(apply)
    animation.finished.connect(lambda: widget.setStyleSheet(""))
    widget._flash = animation
    apply(1.0)
    animation.start()


def caption_label(text):
    label = QLabel(text.upper())
    label.setStyleSheet(f"color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
    return label


def sign_color(value):
    return GREEN if value >= 0 else RED


def arrow(value):
    return "▲" if value >= 0 else "▼"


def scroll_area():
    area = QScrollArea()
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    content = QWidget()
    content.setObjectName("clear")
    area.setWidget(content)
    area.viewport().setObjectName("clear")
    layout = QVBoxLayout(content)
    layout.setContentsMargins(0, 0, 4, 0)
    layout.setSpacing(8)
    return area, layout


def make_icon(size=64):
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(BG))
    painter.drawRoundedRect(0, 0, size, size, size * 0.24, size * 0.24)
    painter.setPen(QPen(QColor(GREEN), size * 0.11, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
    painter.drawPolyline([QPointF(size * x, size * y)
                          for x, y in ((0.16, 0.70), (0.36, 0.48), (0.52, 0.62), (0.84, 0.26))])
    painter.end()
    return QIcon(pixmap)


# ---------- Zustand und Netzwerk ----------

class Controller(QObject):
    """Hält Watchlist, Positionen und Kurse; Netzwerkarbeit läuft in Threads."""
    changed = Signal()
    ledger_changed = Signal()
    trade_recorded = Signal(str, str)  # Symbol, "buy" oder "sell"
    status = Signal(str)
    _result = Signal(object, object, object, object)

    def __init__(self):
        super().__init__()
        self.store = Store(sd.DB_FILE)
        self.store.import_legacy(sd.LEGACY_FILE)
        self.symbols = self.store.symbols()
        self.quotes, self.events = {}, {}
        self.transactions, self.positions, self.realized, self.sales = {}, {}, {}, {}
        self.reload_ledger()
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.closed = False
        self._result.connect(self._deliver)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    def start(self):
        self.refresh()
        self.timer.start(REFRESH_SECONDS * 1000)

    def run(self, work, done, fail=None):
        def task():
            try:
                self._result.emit(done, work(), None, fail)
            except Exception as exc:
                self._result.emit(done, None, exc, fail)
        self.pool.submit(task)

    def shutdown(self):
        """Beendet die Netzwerkarbeit; später eintreffende Ergebnisse werden verworfen."""
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)

    def _deliver(self, done, value, error, fail):
        if self.closed:
            return
        if error is None:
            done(value)
        elif fail:
            fail(error)

    def reload_ledger(self):
        """Berechnet Bestände und realisierte Gewinne neu aus den gespeicherten Transaktionen."""
        self.transactions = {}
        for tx in self.store.transactions():
            self.transactions.setdefault(tx.symbol, []).append(tx)
        opening = self.store.opening_realized()
        self.positions, self.realized, self.sales = {}, {}, {}
        for symbol in self.symbols:
            try:
                state = ledger.replay(self.transactions.get(symbol, []))
            except ValueError:
                self.status.emit(f"{symbol}: gespeicherte Transaktionen sind ungültig")
                continue
            realized = state.realized + opening.get(symbol, 0.0)
            self.realized[symbol] = realized
            self.sales[symbol] = {sale.transaction_id: sale for sale in state.sales}
            if state.shares > 0:
                self.positions[symbol] = {"shares": state.shares, "cost": state.avg_cost, "realized": realized}

    def refresh(self):
        for symbol in list(self.symbols):
            self.load_quote(symbol)
            if symbol not in self.events:
                self.load_events(symbol)

    def load_quote(self, symbol):
        def done(quote):
            if symbol in self.symbols:
                self.quotes[symbol] = quote
                self.status.emit("Aktualisiert " + dt.datetime.now().strftime("%H:%M:%S"))
                self.changed.emit()
        self.run(lambda: sd.fetch_quote(symbol), done,
                 lambda exc: self.status.emit(f"{symbol}: Kurs nicht abrufbar"))

    def load_events(self, symbol):
        def done(events):
            if symbol in self.symbols:
                self.events[symbol] = events
                self.changed.emit()
        self.run(lambda: sd.fetch_events(symbol), done)

    def add_symbol(self, text, on_error, on_choices=None):
        """Fügt eine Aktie hinzu. Fremde Börsenendungen werden übersetzt (ABBN.ZU -> ABBN.SW). Passt das
        Kürzel nicht direkt, wird nach dem Namen gesucht und on_choices(text, treffer) aufgerufen."""
        text = text.strip()
        if not text or text.upper() in self.symbols:
            return

        def work():
            for variant in sd.symbol_variants(text):
                try:
                    return variant, sd.fetch_quote(variant), None
                except Exception:
                    continue
            return None, None, sd.search_symbols(text)

        def done(result):
            symbol, quote, matches = result
            if symbol is None:
                if matches and on_choices:
                    self.status.emit(f"Mehrere Treffer für {text}")
                    on_choices(text, matches)
                else:
                    self.status.emit(f"{text} nicht gefunden")
                    on_error(text)
                return
            if symbol in self.symbols:
                self.status.emit(f"{symbol} ist schon in der Liste")
                return
            self.store.add_symbol(symbol)
            self.symbols = self.store.symbols()
            self.quotes[symbol] = quote
            self.reload_ledger()
            self.changed.emit()
            self.ledger_changed.emit()
            self.load_events(symbol)
            found = symbol if symbol == text.upper() else f"{text.upper()} als {symbol}"
            self.status.emit(f"{found} gefunden")

        def failed(exc):
            self.status.emit(f"{text}: Suche fehlgeschlagen")
            on_error(text)

        self.status.emit(f"Suche {text.upper()} …")
        self.run(work, done, failed)

    def remove(self, symbol):
        """Nimmt die Aktie aus der Watchlist. Ihre Transaktionen bleiben gespeichert."""
        self.store.remove_symbol(symbol)
        self.symbols = self.store.symbols()
        for cache in (self.quotes, self.events):
            cache.pop(symbol, None)
        self._ledger_changed()

    # Transaktionen: die Methoden werfen ValueError bei ungültigen Eingaben
    def record_trade(self, symbol, kind, shares, price, fee, day, note=""):
        candidate = ledger.Transaction(0, symbol, kind, shares, price, fee, day, note)
        ledger.replay(self.transactions.get(symbol, []) + [candidate])  # prüft den ganzen Verlauf
        self.store.add_transaction(symbol, kind, shares, price, fee, day, note)
        self._ledger_changed()
        self.trade_recorded.emit(symbol, kind)

    def start_position(self, symbol, shares, pl):
        """Startbestand aus Stückzahl und aktuellem Gewinn/Verlust in %, gespeichert als Kauf."""
        if symbol in self.positions:
            raise ValueError("Es gibt schon eine Position")
        if pl <= -100:
            raise ValueError("Der Gewinn/Verlust muss über -100 % liegen")
        price = self.quotes[symbol]["price"] / (1 + pl / 100)
        self.record_trade(symbol, "buy", shares, price, 0.0, dt.date.today(), "Startbestand")

    def delete_transaction(self, symbol, transaction_id):
        remaining = [t for t in self.transactions.get(symbol, []) if t.id != transaction_id]
        try:
            ledger.replay(remaining)
        except ValueError as exc:
            raise ValueError(f"Löschen nicht möglich: {exc}") from None
        self.store.delete_transaction(transaction_id)
        self._ledger_changed()

    def _ledger_changed(self):
        self.reload_ledger()
        self.changed.emit()
        self.ledger_changed.emit()


# ---------- Dialoge ----------

class FieldDialog(QDialog):
    """Dialog mit Zahlenfeldern. apply(werte) darf ValueError werfen; die Meldung erscheint im Dialog."""

    def __init__(self, title, intro, fields, apply=lambda values: None, ok_text="OK", cancel=True, modal=True):
        super().__init__()
        self.apply = apply
        self.setModal(modal)
        body = make_panel(self, title, self.reject, always_on_top=True)
        intro_label = QLabel(intro)
        intro_label.setWordWrap(True)
        intro_label.setStyleSheet(f"color: {MUTED};")
        body.addWidget(intro_label)

        self.entries, self.parsers = [], []
        for label, default, *parser in fields:
            self.parsers.append(parser[0] if parser else sd.parse_number)
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            entry = QLineEdit(default)
            entry.setFixedWidth(150)
            entry.returnPressed.connect(self.submit)
            row.addStretch()
            row.addWidget(entry)
            body.addLayout(row)
            self.entries.append(entry)

        self.error = QLabel()
        self.error.setWordWrap(True)
        self.error.setStyleSheet(f"color: {RED};")
        self.error.hide()
        body.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch()
        if cancel:
            cancel_button = QPushButton("Abbrechen")
            cancel_button.clicked.connect(self.reject)
            buttons.addWidget(cancel_button)
        ok = QPushButton(ok_text)
        ok.setObjectName("primary")
        ok.setDefault(True)
        ok.clicked.connect(self.submit)
        buttons.addWidget(ok)
        body.addLayout(buttons)

        self.setFixedWidth(400)
        self.setFixedHeight(self.layout().totalHeightForWidth(400))
        if self.entries:
            self.entries[0].setFocus()
            self.entries[0].selectAll()

    def showEvent(self, event):
        super().showEvent(event)
        # Sofort und noch einmal kurz danach: direkt nach einem Kontextmenü gibt Windows das Fenster
        # sonst manchmal nicht als aktiv frei, und man müsste erst ins Feld klicken.
        self.take_focus()
        QTimer.singleShot(0, self.take_focus)
        QTimer.singleShot(150, self.take_focus)

    def take_focus(self):
        if not self.isVisible() or not self.entries or self.focusWidget() in self.entries and self.isActiveWindow():
            return
        self.raise_()
        self.activateWindow()
        self.entries[0].setFocus(Qt.ActiveWindowFocusReason)
        self.entries[0].selectAll()

    def submit(self):
        try:
            values = [parse(entry.text()) for parse, entry in zip(self.parsers, self.entries)]
            self.apply(values)
        except ValueError as exc:
            return self.show_error(str(exc))
        self.accept()

    def show_error(self, text):
        self.error.setText(text)
        self.error.show()
        self.setFixedHeight(self.layout().totalHeightForWidth(400))
        Dock.arrange()

    def run(self):
        """Zeigt den Dialog und gibt zurück, ob er mit OK bestätigt wurde."""
        Dock.add(self)
        accepted = bool(self.exec())
        Dock.remove(self)
        return accepted

    def show_docked(self):
        """Zeigt den Dialog, ohne die übrigen Fenster zu sperren (für modal=False)."""
        Dock.add(self)
        self.finished.connect(lambda _: Dock.remove(self))
        self.show()


class ChoiceDialog(QDialog):
    """Dialog mit einer Liste von Treffern; run() gibt das gewählte Kürzel oder None zurück."""

    def __init__(self, title, intro, options):
        super().__init__()
        self.setModal(True)
        self.chosen = None
        self.buttons = []
        body = make_panel(self, title, self.reject, always_on_top=True)
        intro_label = QLabel(intro)
        intro_label.setWordWrap(True)
        intro_label.setStyleSheet(f"color: {MUTED};")
        body.addWidget(intro_label)
        for symbol, name, exchange in options:
            button = QPushButton(f"{symbol}   ·   {name}" + (f"   ·   {exchange}" if exchange else ""))
            button.setObjectName("option")
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, s=symbol: self.choose(s))
            body.addWidget(button)
            self.buttons.append(button)
        cancel = QPushButton("Abbrechen")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(cancel)
        body.addLayout(row)
        self.setFixedWidth(460)
        self.setFixedHeight(self.layout().totalHeightForWidth(460))

    def choose(self, symbol):
        self.chosen = symbol
        self.accept()

    def showEvent(self, event):
        super().showEvent(event)
        self.raise_()
        self.activateWindow()

    def run(self):
        Dock.add(self)
        self.exec()
        Dock.remove(self)
        return self.chosen


def show_message(title, text):
    FieldDialog(title, text, [], ok_text="OK", cancel=False).run()


def trade_dialog(ctl, symbol, mode):
    """Dialog für Startbestand ("start"), Kauf/Aufstocken ("buy") und Verkauf/Verkleinern ("sell")."""
    quote = ctl.quotes.get(symbol)
    position = ctl.positions.get(symbol)
    if not quote:
        return show_message("Kein Kurs", "Der Kurs ist noch nicht geladen. Bitte kurz warten.")
    price, cur = quote["price"], quote["currency"]
    num, today = sd.parse_number, dt.date.today().strftime("%d.%m.%Y")

    if mode == "start":
        if position:
            return show_message("Position vorhanden", "Es gibt schon eine Position. Erfasse Käufe oder Verkäufe.")
        FieldDialog(
            f"{symbol}: Startbestand",
            f"Aktueller Kurs: {price:.2f} {cur}. Aus Stückzahl und aktuellem Gewinn/Verlust wird der "
            "Einstandskurs berechnet und als Kauf gespeichert.",
            [("Stückzahl", "", num), ("Gewinn/Verlust in %", "0", num)],
            lambda values: ctl.start_position(symbol, *values)).run()
        return

    holding = f" Du hältst {position['shares']:g} Stück (Einstand {position['cost']:.2f})." if position else ""
    intro = f"Aktueller Kurs: {price:.2f} {cur}.{holding}"
    if mode == "buy":
        FieldDialog(f"{symbol}: Aufstocken", intro,
                    [("Stück", "", num), ("Kaufkurs", f"{price:.2f}", num), (f"Gebühr ({cur})", "0", num),
                     ("Datum", today, sd.parse_date)],
                    lambda values: ctl.record_trade(symbol, "buy", *values)).run()
        return

    if not position:
        return show_message("Keine Position", "Es gibt nichts zu verkaufen.")
    accepted = FieldDialog(f"{symbol}: Verkleinern", intro,
                           [("Stück", "", num), ("Verkaufskurs", f"{price:.2f}", num),
                            (f"Gebühr ({cur})", "0", num), ("Datum", today, sd.parse_date)],
                           lambda values: ctl.record_trade(symbol, "sell", *values)).run()
    if accepted and symbol not in ctl.positions:
        show_message("Position geschlossen",
                     f"Realisierter Gewinn/Verlust insgesamt: {ctl.realized[symbol]:+.2f} {cur}")


# ---------- Hauptfenster ----------

# Spalten der Watchlist: Schlüssel, Überschrift, Breite (None = nimmt den Rest) und Ausrichtung
COLUMNS = (
    ("symbol", "Symbol", 70, Qt.AlignLeft),
    ("price", "Kurs", 62, Qt.AlignRight),
    ("day", "Tag", 74, Qt.AlignRight),
    ("shares", "Stk", 44, Qt.AlignRight),
    ("pl", "G/V %", 74, Qt.AlignRight),
    ("amount", "G/V", 80, Qt.AlignRight),
    ("event", "Termin", None, Qt.AlignLeft),
)
ROW_MARGINS = (12, 0, 12, 0)
ROW_SPACING = 8


class StockCard(QFrame):
    """Eine Zeile der Watchlist; jede Spalte steht in einem eigenen Label mit fester Breite."""
    clicked = Signal(str)
    menu_requested = Signal(str)

    def __init__(self, symbol):
        super().__init__()
        self.symbol = symbol
        self.values = {"symbol": symbol}  # Sortierwerte je Spalte; None = leer, kommt beim Sortieren ans Ende
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(32)
        row = QHBoxLayout(self)
        row.setContentsMargins(*ROW_MARGINS)
        row.setSpacing(ROW_SPACING)
        self.labels = {}
        for key, _, width, align in COLUMNS:
            label = QLabel()
            label.setAlignment(align | Qt.AlignVCenter)
            if width:
                label.setFixedWidth(width)
            self.labels[key] = label
            row.addWidget(label, 0 if width else 1)
        self.price, self.day = self.labels["price"], self.labels["day"]
        self.shares, self.pl = self.labels["shares"], self.labels["pl"]
        self.amount, self.event = self.labels["amount"], self.labels["event"]
        self.labels["symbol"].setText(symbol)
        self.labels["symbol"].setStyleSheet("font-weight: 700;")
        self.price.setStyleSheet("font-weight: 700;")
        self.event.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

    def set_data(self, quote, position, events):
        if events:
            self.event.setText(sd.format_event(events[0], short=True))
            self.values["event"] = events[0][0]
        else:
            self.event.setText("kein Termin" if events is not None else "…")
            self.values["event"] = None
        for key in ("price", "day", "shares", "pl", "amount"):
            self.labels[key].setText("")
            self.values[key] = None
        if not quote:
            return
        self.price.setText(f"{quote['price']:.2f}")
        self.values["price"] = quote["price"]
        change = quote["change_pct"]
        self.day.setText(f"{arrow(change)} {change:+.2f} %")
        self.day.setStyleSheet(f"color: {sign_color(change)}; font-weight: 600;")
        self.values["day"] = change
        if position:
            pl = sd.pl_percent(position, quote["price"])
            amount = sd.pl_amount(position, quote["price"])
            self.shares.setText(f"{position['shares']:g}")
            self.pl.setText(f"{pl:+.2f} %")
            self.pl.setStyleSheet(f"color: {sign_color(pl)}; font-weight: 600;")
            self.amount.setText(f"{amount:+.2f}")
            self.amount.setStyleSheet(f"color: {sign_color(amount)};")
            self.values.update(shares=position["shares"], pl=pl, amount=amount)

    def flash(self, color):
        flash(self, color)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.symbol)

    def contextMenuEvent(self, event):
        self.menu_requested.emit(self.symbol)


class MainWindow(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.cards = {}
        self.details = {}
        self.add_dialog = None
        self.sort_key, self.sort_desc = None, False  # None = Reihenfolge der Watchlist

        self.pin =QPushButton("◉")
        self.pin.setObjectName("icon")
        self.pin.setCheckable(True)
        self.pin.setChecked(PIN["on"])
        self.pin.setToolTip("Im Vordergrund halten")
        self.pin.setCursor(Qt.PointingHandCursor)
        self.pin.clicked.connect(self.toggle_pin)

        self.add_button = QPushButton("+")
        self.add_button.setObjectName("icon")
        self.add_button.setToolTip("Aktie hinzufügen")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.clicked.connect(self.ask_symbol)

        body = make_panel(self, "Watchlist", self.hide_docked, extras=[self.add_button, self.pin])

        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(ROW_MARGINS[0], 0, ROW_MARGINS[2] + 12, 0)  # 12 = Rand und Scrollleiste rechts
        header.setSpacing(ROW_SPACING)
        self.heads = {}
        for key, title, width, align in COLUMNS:
            button = QPushButton(title)
            button.setObjectName("head")
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip("Nach dieser Spalte sortieren")
            if width:
                button.setFixedWidth(width)
            button.clicked.connect(lambda _=False, k=key: self.sort_by(k))
            self.heads[key] = button
            header.addWidget(button, 0 if width else 1)
        body.addWidget(self.header)

        self.area, self.list = scroll_area()
        self.empty = QLabel("Noch keine Aktien.\nMit + oben ein Kürzel oder einen Namen eingeben.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {MUTED};")
        self.list.addWidget(self.empty)
        self.list.addStretch()
        body.addWidget(self.area, 1)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        body.addWidget(self.status)

        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(600, min(640, screen.height() - 20))

        ctl.changed.connect(self.sync)
        ctl.status.connect(self.status.setText)
        ctl.trade_recorded.connect(self.flash_card)
        self.sync()

    def show_docked(self):
        Dock.add(self)
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_docked(self):
        Dock.remove(self)
        self.hide()

    def toggle_pin(self):
        PIN["on"] = self.pin.isChecked()
        apply_pin()

    def ask_symbol(self):
        """Kleiner Dialog mit Eingabefeld, nicht modal: die anderen Fenster bleiben bedienbar.
        Das Hinzufügen selbst läuft in add_symbol."""
        if self.add_dialog is not None and self.add_dialog.isVisible():
            self.add_dialog.raise_()
            self.add_dialog.activateWindow()
            return

        def parse(text):
            text = text.strip()
            if not text:
                raise ValueError("Bitte ein Kürzel oder einen Namen eingeben.")
            return text

        self.add_dialog = FieldDialog("Aktie hinzufügen", "Kürzel oder Name, z. B. AAPL oder Droneshield.",
                                      [("Kürzel / Name", "", parse)], lambda values: self.add_symbol(values[0]),
                                      ok_text="Hinzufügen", modal=False)
        self.add_dialog.show_docked()

    def add_symbol(self, text):
        self.ctl.add_symbol(text, self.symbol_not_found, self.choose_symbol)

    def symbol_not_found(self, text):
        show_message("Nichts gefunden",
                     f"Zu „{text}“ wurde keine Aktie gefunden. Probiere den Firmennamen oder das Kürzel mit "
                     "Börsenendung, z. B. DRO.AX (Australien), ABBN.SW (Schweiz) oder SAP.DE (Deutschland).")

    def choose_symbol(self, text, matches):
        options = [(m["symbol"], m["name"], m["exchange"]) for m in matches]
        chosen = ChoiceDialog(f"Treffer für „{text}“", "Welche Börse und welches Papier meinst du?", options).run()
        if chosen:
            self.ctl.add_symbol(chosen, self.symbol_not_found)

    def sync(self):
        for symbol in list(self.cards):
            if symbol not in self.ctl.symbols:
                self.cards.pop(symbol).deleteLater()
        for symbol in self.ctl.symbols:
            if symbol not in self.cards:
                card = StockCard(symbol)
                card.clicked.connect(self.open_detail)
                card.menu_requested.connect(self.show_menu)
                self.cards[symbol] = card
                self.list.insertWidget(self.list.count() - 1, card)
        for symbol, card in self.cards.items():
            card.set_data(self.ctl.quotes.get(symbol), self.ctl.positions.get(symbol),
                          self.ctl.events.get(symbol))
        self.empty.setVisible(not self.cards)
        self.header.setVisible(bool(self.cards))
        self.reorder()

    def sort_by(self, key):
        """Erster Klick sortiert aufsteigend, jeder weitere auf dieselbe Spalte kehrt die Richtung um."""
        self.sort_desc = (not self.sort_desc) if key == self.sort_key else False
        self.sort_key = key
        self.reorder()

    def ordered_symbols(self):
        symbols = [s for s in self.ctl.symbols if s in self.cards]
        key = self.sort_key
        if key is None:
            return symbols
        filled = [s for s in symbols if self.cards[s].values.get(key) is not None]
        empty = [s for s in symbols if self.cards[s].values.get(key) is None]  # leere Zellen immer zuletzt
        filled.sort(key=lambda s: self.cards[s].values[key], reverse=self.sort_desc)
        return filled + empty

    def reorder(self):
        for index, symbol in enumerate(self.ordered_symbols()):
            card = self.cards[symbol]
            if self.list.indexOf(card) != index + 1:  # Platz 0 hält den Leer-Hinweis
                self.list.removeWidget(card)
                self.list.insertWidget(index + 1, card)
        for key, title, _, align in COLUMNS:
            active = key == self.sort_key
            button = self.heads[key]
            button.setText(f"{title} {'▼' if self.sort_desc else '▲'}" if active else title)
            button.setStyleSheet(f"QPushButton#head {{ text-align: {'left' if align == Qt.AlignLeft else 'right'};"
                                 f"{f' color: {ACCENT};' if active else ''} }}")

    def flash_card(self, symbol, kind):
        """Kauf und Startbestand leuchten grün, Verkauf rot."""
        card = self.cards.get(symbol)
        if card:
            self.area.ensureWidgetVisible(card)
            card.flash(GREEN if kind == "buy" else RED)

    def show_menu(self, symbol):
        self.build_menu(symbol).exec(QCursor.pos())

    def build_menu(self, symbol):
        menu = make_menu()

        def add(text, action):
            # Erst nach dem Schließen des Menüs ausführen. Öffnet sich ein Dialog, während das Menü noch
            # den Fokus hält, bekommt er ihn von Windows nicht und man müsste erst ins Feld klicken.
            menu.addAction(text, lambda: QTimer.singleShot(0, action))

        add("Details", lambda: self.open_detail(symbol))
        menu.addSeparator()
        position = self.ctl.positions.get(symbol)
        if not position:
            add("Startbestand festlegen …", lambda: trade_dialog(self.ctl, symbol, "start"))
        add("Aufstocken …", lambda: trade_dialog(self.ctl, symbol, "buy"))
        if position:
            add("Verkleinern …", lambda: trade_dialog(self.ctl, symbol, "sell"))
        add("Transaktionen …", lambda: open_transactions(self.ctl, symbol))
        menu.addSeparator()
        add("Entfernen", lambda: self.ctl.remove(symbol))
        return menu

    def open_detail(self, symbol):
        existing = self.details.get(symbol)
        if existing:
            existing.raise_()
            existing.activateWindow()
            return
        window = DetailWindow(self.ctl, symbol)
        window.closed.connect(lambda s: self.details.pop(s, None))
        self.details[symbol] = window
        Dock.add(window)
        window.show()


# ---------- Transaktionen ----------

TX_WINDOWS = {}


def open_transactions(ctl, symbol):
    existing = TX_WINDOWS.get(symbol)
    if existing:
        existing.raise_()
        existing.activateWindow()
        return
    window = TransactionsWindow(ctl, symbol)
    window.closed.connect(lambda s: TX_WINDOWS.pop(s, None))
    TX_WINDOWS[symbol] = window
    Dock.add(window)
    window.show()


class TransactionRow(QFrame):
    def __init__(self, ctl, symbol, tx, cur):
        super().__init__()
        self.setObjectName("plain")
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 8, 10)
        left = QVBoxLayout()
        left.setSpacing(2)
        kind = "Kauf" if tx.kind == "buy" else "Verkauf"
        title = QLabel(f"{kind} · {tx.shares:g} Stk · {tx.price:.2f} {cur}")
        title.setStyleSheet("font-weight: 700;")
        details = [tx.day.strftime("%d.%m.%Y")]
        if tx.fee:
            details.append(f"Gebühr {tx.fee:.2f}")
        if tx.note:
            details.append(tx.note)
        sub = QLabel(" · ".join(details))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        left.addWidget(title)
        left.addWidget(sub)
        row.addLayout(left, 1)
        sale = ctl.sales.get(symbol, {}).get(tx.id)
        if sale:
            gain = QLabel(f"{sale.gain:+.2f} {cur}")
            style_pill(gain, sign_color(sale.gain), strong=True)
            row.addWidget(gain)
        delete = QPushButton("✕")
        delete.setObjectName("icon")
        delete.setToolTip("Transaktion löschen")
        delete.setCursor(Qt.PointingHandCursor)
        delete.clicked.connect(lambda: FieldDialog(
            "Transaktion löschen",
            f"{kind} von {tx.shares:g} Stück am {tx.day:%d.%m.%Y} wirklich löschen? "
            "Bestand und Gewinne werden neu berechnet.", [],
            lambda _values: ctl.delete_transaction(symbol, tx.id), ok_text="Löschen").run())
        row.addWidget(delete)


class TransactionsWindow(QWidget):
    closed = Signal(str)

    def __init__(self, ctl, symbol):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.ctl, self.symbol = ctl, symbol
        body = make_panel(self, f"{symbol}: Transaktionen", self.close)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"color: {MUTED};")
        body.addWidget(self.summary)
        area, self.rows = scroll_area()
        self.rows.addStretch()
        body.addWidget(area, 1)
        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(460, min(620, screen.height() - 20))
        ctl.ledger_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        while self.rows.count() > 1:
            widget = self.rows.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        quote = self.ctl.quotes.get(self.symbol)
        cur = quote["currency"] if quote else ""
        transactions = sorted(self.ctl.transactions.get(self.symbol, []), key=lambda t: (t.day, t.id), reverse=True)
        for tx in transactions:
            self.rows.insertWidget(self.rows.count() - 1, TransactionRow(self.ctl, self.symbol, tx, cur))
        position = self.ctl.positions.get(self.symbol)
        parts = [f"{position['shares']:g} Stück · Einstand {position['cost']:.2f}" if position
                 else "Keine Position"]
        if self.symbol in self.ctl.realized:
            parts.append(f"realisiert {self.ctl.realized[self.symbol]:+.2f} {cur}")
        self.summary.setText(" · ".join(parts) if transactions else "Noch keine Transaktionen.")

    def closeEvent(self, event):
        Dock.remove(self)
        try:
            self.ctl.ledger_changed.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        self.closed.emit(self.symbol)
        super().closeEvent(event)


# ---------- Detailfenster ----------

class DetailWindow(QWidget):
    closed = Signal(str)

    def __init__(self, ctl, symbol):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.ctl, self.symbol = ctl, symbol
        self.alive = True
        body = make_panel(self, symbol, self.close)

        top = QHBoxLayout()
        self.price = QLabel("…")
        self.price.setStyleSheet("font-size: 30px; font-weight: 700;")
        self.day = QLabel()
        top.addWidget(self.price)
        top.addSpacing(8)
        top.addWidget(self.day)
        top.addStretch()
        body.addLayout(top)

        # Position
        self.position_card = position_card = QFrame()
        position_card.setObjectName("plain")
        column = QVBoxLayout(position_card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(6)
        column.addWidget(caption_label("Position"))
        pl_row = QHBoxLayout()
        self.pl_percent = QLabel()
        self.pl_amount = QLabel()
        pl_row.addWidget(self.pl_percent)
        pl_row.addSpacing(8)
        pl_row.addWidget(self.pl_amount, 0, Qt.AlignBottom)
        pl_row.addStretch()
        column.addLayout(pl_row)
        self.position_info = QLabel()
        self.position_info.setWordWrap(True)
        self.position_info.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        column.addWidget(self.position_info)
        buttons = QGridLayout()
        buttons.setSpacing(6)
        self.buttons = {}
        for index, (text, mode) in enumerate((("Startbestand", "start"), ("Aufstocken", "buy"),
                                              ("Verkleinern", "sell"), ("Transaktionen", "history"))):
            button = QPushButton(text)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, m=mode: open_transactions(ctl, symbol)
                                   if m == "history" else trade_dialog(ctl, symbol, m))
            buttons.addWidget(button, index // 2, index % 2)
            self.buttons[mode] = button
        column.addLayout(buttons)
        body.addWidget(position_card)

        # Nächster Termin
        event_card = QFrame()
        event_card.setObjectName("plain")
        column = QVBoxLayout(event_card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(3)
        column.addWidget(caption_label("Nächster Termin"))
        self.event_title = QLabel("Wird geladen …")
        self.event_title.setStyleSheet(f"color: {ACCENT}; font-size: 17px; font-weight: 700;")
        self.event_when = QLabel()
        self.event_more = QLabel()
        self.event_more.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        column.addWidget(self.event_title)
        column.addWidget(self.event_when)
        column.addWidget(self.event_more)
        body.addWidget(event_card)

        body.addWidget(caption_label("News"))
        area, self.news = scroll_area()
        self.news.addStretch()
        body.addWidget(area, 1)

        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(460, min(760, screen.height() - 20))

        ctl.changed.connect(self.refresh_view)
        ctl.trade_recorded.connect(self.on_trade)
        self.refresh_view()
        ctl.run(self.load, self.show_loaded)

    def on_trade(self, symbol, kind):
        if symbol == self.symbol:
            flash(self.position_card, GREEN if kind == "buy" else RED)

    def refresh_view(self):
        quote = self.ctl.quotes.get(self.symbol)
        position = self.ctl.positions.get(self.symbol)
        if quote:
            cur = quote["currency"]
            self.price.setText(f"{quote['price']:.2f} {cur}")
            change = quote["change_pct"]
            self.day.setText(f"{arrow(change)} {change:+.2f} %")
            style_pill(self.day, sign_color(change))
        if position and quote:
            pl = sd.pl_percent(position, quote["price"])
            amount = sd.pl_amount(position, quote["price"])
            color = sign_color(pl)
            self.pl_percent.setText(f"{pl:+.2f} %")
            self.pl_percent.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {color};")
            self.pl_amount.setText(f"{amount:+.2f} {cur}")
            self.pl_amount.setStyleSheet(f"font-size: 14px; font-weight: 600; color: {color};")
            info = (f"{position['shares']:g} Stück · Einstand {position['cost']:.2f} · "
                    f"Wert {quote['price'] * position['shares']:.2f} {cur}")
            if position["realized"]:
                info += f" · realisiert {position['realized']:+.2f} {cur}"
            self.position_info.setText(info)
        else:
            self.pl_percent.setText("–")
            self.pl_percent.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {MUTED};")
            self.pl_amount.setText("")
            info = "Keine Position hinterlegt."
            realized = self.ctl.realized.get(self.symbol)
            if realized:
                info += f" Realisiert insgesamt {realized:+.2f} {quote['currency'] if quote else ''}"
            self.position_info.setText(info)
        self.buttons["start"].setEnabled(not position)
        self.buttons["sell"].setEnabled(bool(position))

    def load(self):
        events = news = error = None
        try:
            events = sd.fetch_events(self.symbol)
        except Exception as exc:
            error = exc
        try:
            news = sd.fetch_news(self.symbol)
        except Exception as exc:
            error = error or exc
        return events, news, error

    def show_loaded(self, result):
        if not self.alive:
            return
        events, news, error = result
        if events:
            title, when = sd.describe_event(events[0])
            self.event_title.setText(title)
            self.event_when.setText(when)
            self.event_more.setText("\n".join("Danach: " + sd.format_event(e) for e in events[1:4]))
        else:
            self.event_title.setText("Kein bevorstehender Termin" if events is not None
                                     else "Termine nicht ladbar")
            self.event_when.setText("")
            self.event_more.setText("")
        if not news:
            note = QLabel("Keine News gefunden." if news is not None else f"News nicht ladbar: {error}")
            note.setStyleSheet(f"color: {MUTED};")
            self.news.insertWidget(0, note)
        for title, source, published, link in news or []:
            self.news.insertWidget(self.news.count() - 1, NewsCard(title, source, published, link))

    def closeEvent(self, event):
        Dock.remove(self)
        self.alive = False
        for signal, slot in ((self.ctl.changed, self.refresh_view), (self.ctl.trade_recorded, self.on_trade)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):
                pass
        self.closed.emit(self.symbol)
        super().closeEvent(event)


class NewsCard(QFrame):
    def __init__(self, title, source, published, link):
        super().__init__()
        self.link = link
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 10, 14, 10)
        column.setSpacing(3)
        headline = QLabel(title)
        headline.setWordWrap(True)
        headline.setStyleSheet("font-weight: 600;")
        when = published.strftime("%d.%m. %H:%M") if published else ""
        meta = QLabel(" · ".join(part for part in (source, when) if part))
        meta.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        column.addWidget(headline)
        column.addWidget(meta)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            QDesktopServices.openUrl(QUrl(self.link))


# ---------- Start ----------

LOCK_PORT = 48653


def claim_single_instance(port=LOCK_PORT):
    """Hält einen lokalen Port als Sperre, damit das Widget nicht doppelt läuft."""
    lock = socket.socket()
    try:
        lock.bind(("127.0.0.1", port))
    except OSError:
        lock.close()
        return None
    return lock


def close_all_windows(ctl):
    """Schließt beim Beenden alle Fenster geordnet, sonst räumt Python sie in zufälliger Reihenfolge ab."""
    for window in list(Dock.windows):
        window.close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)  # löscht die geschlossenen Fenster jetzt
    ctl.shutdown()


def main():
    lock = claim_single_instance()
    if lock is None:
        return
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet(STYLE)
    icon = make_icon()
    app.setWindowIcon(icon)

    ctl = Controller()
    window = MainWindow(ctl)
    app.aboutToQuit.connect(lambda: close_all_windows(ctl))

    tray_menu = make_menu()
    tray_menu.addAction("Öffnen", window.show_docked)
    tray_menu.addAction("Beenden", app.quit)
    tray = QSystemTrayIcon(icon, app)
    tray.setToolTip("Aktien-Widget")
    tray.setContextMenu(tray_menu)
    tray.activated.connect(lambda reason: (window.hide_docked() if window.isVisible() else window.show_docked())
                           if reason == QSystemTrayIcon.Trigger else None)
    tray.show()

    if "--tray" not in sys.argv:
        window.show_docked()
    ctl.start()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
