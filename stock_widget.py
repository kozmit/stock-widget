"""Aktien-Widget: Watchlist mit Kursen, Positionen, News und dem nächsten wichtigen Termin je Aktie.

Oberfläche mit Qt (PySide6): rahmenlose Fenster mit runden Ecken, alle Fenster und Dialoge
öffnen unten rechts im Bildschirm und reihen sich nebeneinander auf.
Start: python stock_widget.py   (mit --tray startet es nur als Icon im Infobereich)
"""
import datetime as dt
import json
import socket
import sys
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import (QEasingCurve, QEvent, QObject, QPoint, QPointF, QRectF, QSize, Qt, QTimer, QUrl,
                            QVariantAnimation, Signal)
from PySide6.QtGui import (QColor, QCursor, QDesktopServices, QFont, QFontMetrics, QGuiApplication, QIcon,
                           QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF)
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QGraphicsDropShadowEffect,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QSystemTrayIcon, QVBoxLayout,
                               QWidget)

import glossary
import history
import ledger
import portfolio
import etoro
import consensus as cons
import events as evt
import fundamentals as fund
import keywords as kw
import newsfeed
import sources
import stock_data as sd
import fx as fx_module
from fx import BASES, FxTable, currency_code
from PySide6.QtWidgets import QAbstractButton, QButtonGroup, QComboBox
from store import Store

REFRESH_SECONDS = 60
STALE_SECONDS = 3 * REFRESH_SECONDS          # so lange gilt ein Kurs ohne neuen Abruf als aktuell
INSTRUMENT_MAX_AGE = dt.timedelta(days=7)    # danach werden die Stammdaten neu geladen
NEWS_MAX_AGE = dt.timedelta(minutes=10)      # so lange gelten geladene News für den Feed als frisch
NEWS_FETCH_COUNT = 40                        # so viele News werden geholt; der Filter lässt davon nur einen Teil übrig
NOT_LOADED = object()                        # Marke: die News einer Aktie sind noch nicht angekommen

BG, SURFACE, SURFACE2 = "#14161c", "#1d2029", "#272b39"
TEXT, MUTED, ACCENT = "#eceef4", "#8a90a6", "#6c8cff"
GREEN, RED, AMBER = "#34d399", "#f87171", "#fbbf24"

STYLE = f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI"; font-size: 13px; color: {TEXT}; }}
QLabel {{ background: transparent; }}
QFrame#panel {{ background: {BG}; border: 1px solid #2b2f3d; border-radius: 20px; }}
QFrame#card {{ background: {SURFACE}; border-radius: 10px; }}
QFrame#card:hover {{ background: {SURFACE2}; }}
QFrame#card[open="true"] {{ background: #2c3a78; }}
QFrame#card[open="true"]:hover {{ background: #34448c; }}
QFrame#termpanel {{ background: {SURFACE}; border: 1px solid #3a4157; border-radius: 12px; }}
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
QPushButton#segment {{ background: transparent; color: {MUTED}; padding: 6px 12px; border-radius: 10px; font-size: 12px; }}
QPushButton#segment:hover {{ color: {TEXT}; }}
QPushButton#segment:checked {{ background: {SURFACE2}; color: {TEXT}; }}
QPushButton#option {{ text-align: left; padding: 11px 16px; background: {SURFACE}; font-weight: 600; }}
QPushButton#option:hover {{ background: {SURFACE2}; }}
QPushButton#option:focus {{ border: 1px solid {ACCENT}; }}
QComboBox {{ background: {SURFACE}; border: 1px solid transparent; border-radius: 14px; padding: 8px 14px; }}
QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid #2b2f3d; border-radius: 10px; padding: 4px;
                               selection-background-color: {SURFACE2}; outline: 0; }}
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
    """Verwaltet die offenen Fenster: das erste sitzt ganz rechts unten, jedes neue links daneben.
    Ein Fenster mit dock_above (und desired_height) sitzt stattdessen über diesem Fenster, so breit wie
    dieses, und wird nur so hoch, wie darüber Platz ist."""
    MARGIN, GAP, MIN_STACKED = 6, 4, 280
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
    def fits_above(cls, window):
        """Ob window über seinem Bezugsfenster sitzt: nur wenn das offen ist und darüber genug Platz bleibt;
        sonst nimmt es wie jedes andere Fenster den nächsten Platz links."""
        base = getattr(window, "dock_above", None)
        if base not in cls.windows:
            return False
        area = QApplication.primaryScreen().availableGeometry()
        base_top = area.bottom() + 1 - cls.MARGIN - base.height()
        return base_top - cls.GAP - cls.MARGIN - area.top() >= min(cls.MIN_STACKED, window.desired_height)

    @classmethod
    def arrange(cls):
        if cls.frozen:
            return
        area = QApplication.primaryScreen().availableGeometry()
        right = area.right() + 1 - cls.MARGIN
        bottom = area.bottom() + 1 - cls.MARGIN
        stacked = [w for w in cls.windows if cls.fits_above(w)]
        for window in (w for w in cls.windows if w not in stacked):
            window.move(max(right - window.width(), area.left()), bottom - window.height())
            right -= window.width() + cls.GAP
        for window in stacked:
            base = window.dock_above
            space = base.y() - cls.GAP - cls.MARGIN - area.top()
            height = min(window.desired_height, space)
            if window.width() != base.width():
                window.setFixedWidth(base.width())
                window.update_desired_height()  # die Höhe hängt von der Breite ab (umbrechende Texte)
                height = min(window.desired_height, space)
            if window.height() != height:
                window.setFixedHeight(height)
            window.move(max(base.x() + base.width() - window.width(), area.left()),
                        max(base.y() - cls.GAP - window.height(), area.top()))


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


def pin_icon():
    """Reißzwecke: grau, wenn die Box nicht angeheftet ist, blau, wenn sie es ist."""
    icon = QIcon()
    for color, state in ((MUTED, QIcon.Off), (ACCENT, QIcon.On)):
        pixmap = QPixmap(32, 32)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.translate(16, 15)
        painter.rotate(40)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(color))
        painter.drawRoundedRect(QRectF(-4.5, -13, 9, 11), 2, 2)
        painter.drawRoundedRect(QRectF(-8, -3, 16, 3.5), 1.5, 1.5)
        painter.setPen(QPen(QColor(color), 2.4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(0, 1), QPointF(0, 12))
        painter.end()
        icon.addPixmap(pixmap, QIcon.Normal, state)
    return icon


def make_panel(window, title, on_close, extras=(), always_on_top=False, pin=None):
    """Macht aus window ein rahmenloses, rundes Fenster mit Schatten und Kopfzeile.
    pin = (Controller, Schlüssel) fügt oben rechts die Reißzwecke hinzu: angeheftete Boxen öffnen sich
    beim Start des Widgets von selbst. Gibt das Layout für den Inhalt zurück."""
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
    if pin:
        ctl, key = pin
        window.pin_button = pin_button = QPushButton()
        pin_button.setObjectName("icon")
        pin_button.setIcon(pin_icon())
        pin_button.setIconSize(QSize(16, 16))
        pin_button.setCheckable(True)
        pin_button.setChecked(ctl.is_pinned(key))
        pin_button.setToolTip("Anheften: öffnet sich beim Start des Widgets automatisch")
        pin_button.setCursor(Qt.PointingHandCursor)
        pin_button.clicked.connect(lambda checked=False: ctl.set_pinned(key, pin_button.isChecked()))
        row.addWidget(pin_button)
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


def freshness_text(freshness):
    """Text zu einem Kurs: Stand, Quelle und ob er veraltet ist."""
    if not freshness:
        return ""
    parts = []
    fetched = freshness["fetched_at"]
    if fetched:
        same_day = fetched.date() == dt.date.today()
        parts.append("Stand " + fetched.strftime("%H:%M:%S" if same_day else "%d.%m. %H:%M"))
    if freshness["source"]:
        parts.append(freshness["source"])
    text = " · ".join(parts)
    if freshness["error"]:
        text += f"\nVeraltet: letzter Abruf fehlgeschlagen ({freshness['error']})"
    elif freshness["stale"]:
        text += "\nVeraltet: länger nicht aktualisiert"
    return text


def caption_label(text):
    label = QLabel(text.upper())
    label.setStyleSheet(f"color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
    return label


def sign_color(value):
    return GREEN if value >= 0 else RED


def arrow(value):
    return "▲" if value >= 0 else "▼"


def clear_layout(layout):
    """Entfernt alle Einträge eines Layouts samt der Widgets darin (auch aus verschachtelten Layouts)."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().hide()
            item.widget().setParent(None)
            item.widget().deleteLater()
        elif item.layout():
            clear_layout(item.layout())


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


# ---------- Erklärungen zu Fachbegriffen ----------
# Der Text zu jedem Begriff steht zentral in glossary.py und ist deshalb überall gleich. explain() macht ein ganzes
# Widget zum Begriff, term_link() + enable_term_links() einzelne Wörter in einem Text. Die Erklärung erscheint, wenn die
# Maus kurz auf dem Begriff ruht, bleibt nach einem Klick stehen und lässt sich ohne Maus öffnen: mit Tab zum Begriff,
# dann Eingabe- oder Leertaste (bei Schaltflächen mit F1). Esc oder ein Klick daneben schließt sie.

HOVER_MS = 350  # so lange ruht die Maus, bevor die Erklärung erscheint
HEAD_TERMS = {"price": "kurs", "day": "tag", "value": "positionswert", "pl": "gv_prozent", "amount": "gv",
              "event": "termin"}
EVENT_TERMS = {"Quartalszahlen": "quartalszahlen", "Ex-Dividende": "ex_dividende",
               "Dividendenzahlung": "dividendenzahlung"}


class _PopupWatcher(QObject):
    """Hört, solange eine Erklärung festgehalten ist, auf Klicks daneben und auf Esc."""

    def __init__(self, popup):
        super().__init__(popup)
        self.popup = popup

    def eventFilter(self, obj, event):
        popup = self.popup
        if event.type() == QEvent.MouseButtonPress and hasattr(event, "globalPosition"):
            # Maßgeblich ist das Widget unter dem Mauszeiger, nicht der Empfänger: Qt reicht einen Klick, den ein Label
            # nicht verarbeitet, an das übergeordnete Widget weiter.
            target = QApplication.widgetAt(event.globalPosition().toPoint())
            owner = popup.owner
            inside = target is not None and (target is popup or popup.isAncestorOf(target))
            on_owner = target is not None and owner is not None and (target is owner or owner.isAncestorOf(target))
            if not inside and not on_owner:  # ein Klick auf den Auslöser selbst schaltet dort um
                popup.close_term()
        elif event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            popup.close_term()
            return True
        return False


class TermPopup(QWidget):
    """Die gemeinsame Erklärungsbox. Es gibt nur eine: ein neuer Begriff ersetzt den vorigen."""
    WIDTH = 380
    _instance = None

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        super().__init__(None, Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFixedWidth(self.WIDTH)
        self.owner, self.key, self.pinned = None, None, False
        self.watcher = _PopupWatcher(self)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 14)
        self.panel = None
        self._fill(glossary.term("kurs"), "")  # nur damit die Beschriftungen immer existieren

    def _fill(self, term, note):
        """Baut den Inhalt für term ganz neu auf. Ein vorhandenes Layout umzubauen ergab falsche Höhen."""
        outer = self.layout()
        if self.panel is not None:
            outer.removeWidget(self.panel)
            self.panel.hide()
            self.panel.setParent(None)
            self.panel.deleteLater()
        panel = self.panel = QFrame()
        panel.setObjectName("termpanel")
        shadow = QGraphicsDropShadowEffect(panel)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(QColor(0, 0, 0, 170))
        panel.setGraphicsEffect(shadow)
        column = QVBoxLayout(panel)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(5)

        def label(text, style=""):
            item = QLabel(text)
            item.setWordWrap(True)
            if style:
                item.setStyleSheet(style)
            column.addWidget(item)
            return item

        self.title = label(term.title, "font-size: 14px; font-weight: 700;")
        self.full = label(term.full, f"color: {MUTED}; font-size: 11px;")
        self.full.setVisible(bool(term.full))
        self.text = label(term.text)
        for heading, value in term.sections():
            label(heading.upper(), f"color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
            style = (f'color: {ACCENT}; font-family: "Cascadia Mono", Consolas, monospace; font-size: 12px;'
                     if heading == "Formel" else "")
            label(value, style)
        self.footer = label(note, f"color: {MUTED}; font-size: 11px; font-style: italic;")
        self.footer.setVisible(bool(note))
        outer.addWidget(panel)
        for child in [panel, *panel.findChildren(QWidget)]:  # erst mit der echten Schrift und dem Rahmen messen
            child.ensurePolished()
        margins = outer.contentsMargins()
        inner = panel.layout().totalHeightForWidth(self.WIDTH - margins.left() - margins.right())
        self.setFixedHeight(inner + margins.top() + margins.bottom())

    def show_term(self, key, owner, pinned=False, point=None, note=""):
        """Zeigt die Erklärung zu key neben owner (oder an der Bildschirmstelle point)."""
        self.owner, self.key, self.pinned = owner, key, pinned
        self._fill(glossary.term(key), note)
        self.move(self._position(owner, point))
        self.show()
        self.raise_()
        app = QApplication.instance()
        if pinned:
            app.installEventFilter(self.watcher)
        else:
            app.removeEventFilter(self.watcher)

    def _position(self, owner, point):
        """Unter dem Begriff (oder neben dem Mauszeiger), und immer ganz auf dem Bildschirm."""
        anchor = point + QPoint(12, 18) if point is not None else owner.mapToGlobal(QPoint(0, owner.height() + 4))
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        x = min(max(anchor.x(), area.left() + 4), area.right() - self.width() - 4)
        y = anchor.y()
        if y + self.height() > area.bottom() - 4:  # unten kein Platz: über den Begriff setzen
            above = (point.y() - self.height() - 8) if point is not None else owner.mapToGlobal(QPoint(0, 0)).y() - self.height() - 4
            y = max(above, area.top() + 4)
        return QPoint(x, y)

    def close_term(self, owner=None, only_unpinned=False):
        """Schließt die Erklärung; mit owner nur, wenn sie zu diesem Widget gehört."""
        if owner is not None and owner is not self.owner:
            return
        if only_unpinned and self.pinned:
            return
        self.hide()
        self.owner, self.key, self.pinned = None, None, False
        QApplication.instance().removeEventFilter(self.watcher)


class TermAnchor(QObject):
    """Macht ein ganzes Widget zum Auslöser einer Erklärung (siehe explain)."""

    def __init__(self, widget, key, note=""):
        super().__init__(widget)
        self.widget, self.key, self.note = widget, key, note
        self.is_button = isinstance(widget, QAbstractButton)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(HOVER_MS)
        self.timer.timeout.connect(lambda: self.open(pinned=False))
        widget.installEventFilter(self)

    def is_open(self):
        popup = TermPopup.instance()
        return popup.isVisible() and popup.owner is self.widget and popup.key == self.key

    def open(self, pinned):
        TermPopup.instance().show_term(self.key, self.widget, pinned, note=self.note)

    def toggle(self):
        if self.is_open() and TermPopup.instance().pinned:
            TermPopup.instance().close_term()
        else:
            self.open(pinned=True)

    def underline(self, on):
        if not self.is_button:  # Schaltflächen haben ihre eigene Rückmeldung
            font = self.widget.font()
            font.setUnderline(on)
            self.widget.setFont(font)

    def eventFilter(self, obj, event):
        kind = event.type()
        if kind == QEvent.Enter:
            self.timer.start()
            self.underline(True)
        elif kind == QEvent.Leave:
            self.timer.stop()
            if not self.widget.hasFocus():
                self.underline(False)
            TermPopup.instance().close_term(self.widget, only_unpinned=True)
        elif kind == QEvent.MouseButtonRelease and not self.is_button and event.button() == Qt.LeftButton:
            self.timer.stop()
            self.toggle()
        elif kind == QEvent.KeyPress:
            key = event.key()
            if key == Qt.Key_F1 or (not self.is_button and key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space)):
                self.timer.stop()
                self.toggle()
                return True
            if key == Qt.Key_Escape and self.is_open():
                TermPopup.instance().close_term()
                return True
        elif kind == QEvent.FocusIn:
            self.underline(True)
        elif kind == QEvent.FocusOut:
            if not self.widget.underMouse():
                self.underline(False)
            if self.is_open() and TermPopup.instance().pinned:
                TermPopup.instance().close_term()
        elif kind in (QEvent.Hide, QEvent.Close):
            self.timer.stop()
            TermPopup.instance().close_term(self.widget)
        return False


def explain(widget, key, note=""):
    """Macht widget zum Begriff key aus dem Glossar. Gibt widget zurück, damit es sich einreihen lässt.
    note erscheint unten in der Erklärung (zum Beispiel was ein Klick auf eine Schaltfläche tut)."""
    term = glossary.term(key)  # ein unbekannter Schlüssel soll sofort auffallen
    anchor = getattr(widget, "_term_anchor", None)
    if anchor is not None:
        anchor.key, anchor.note = key, note
        return widget
    widget._term_anchor = TermAnchor(widget, key, note)
    if not isinstance(widget, QAbstractButton):
        widget.setCursor(Qt.WhatsThisCursor)
        if widget.focusPolicy() == Qt.NoFocus:
            widget.setFocusPolicy(Qt.TabFocus)  # mit Tab erreichbar
    widget.setAccessibleDescription(term.text)
    return widget


def term_link(text, key):
    """Ein Wort in einem Rich-Text-Label, das beim Überfahren oder Anklicken erklärt wird."""
    return glossary.link(text, key)


class TermLinks(QObject):
    """Bedient die term_link-Verweise einer Beschriftung (siehe enable_term_links)."""

    def __init__(self, label):
        super().__init__(label)
        self.label, self.pending = label, ""
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(HOVER_MS)
        self.timer.timeout.connect(self.open_pending)
        label.linkHovered.connect(self.hovered)
        label.linkActivated.connect(self.activated)

    def hovered(self, url):
        key = glossary.key_of_link(url)
        self.timer.stop()
        if key:
            self.pending = key
            self.timer.start()
        else:
            self.pending = ""
            TermPopup.instance().close_term(self.label, only_unpinned=True)

    def open_pending(self):
        if self.pending:
            TermPopup.instance().show_term(self.pending, self.label, False, QCursor.pos())

    def activated(self, url):
        key = glossary.key_of_link(url)
        if not key:
            return
        self.timer.stop()
        popup = TermPopup.instance()
        if popup.isVisible() and popup.owner is self.label and popup.key == key and popup.pinned:
            popup.close_term()
        else:  # mit der Maus neben dem Zeiger, mit der Tastatur unter der Beschriftung
            popup.show_term(key, self.label, True, QCursor.pos() if self.label.underMouse() else None)


def enable_term_links(label):
    """Macht die term_link-Verweise in der Beschriftung bedienbar (auch mit Tab und Eingabetaste)."""
    if getattr(label, "_term_links", None) is None:
        label.setTextFormat(Qt.RichText)
        label.setTextInteractionFlags(Qt.LinksAccessibleByMouse | Qt.LinksAccessibleByKeyboard)
        palette = label.palette()
        for role in (QPalette.Link, QPalette.LinkVisited):
            palette.setColor(role, QColor(TEXT))
        label.setPalette(palette)
        label._term_links = TermLinks(label)
    return label


# ---------- Zustand und Netzwerk ----------

class Controller(QObject):
    """Hält Watchlist, Positionen und Kurse; Netzwerkarbeit läuft in Threads."""
    changed = Signal()
    ledger_changed = Signal()
    history_changed = Signal()  # neue Tageskurse oder Importe: Verlaufsdiagramme neu zeichnen
    base_changed = Signal(str)  # die Basiswährung wurde umgestellt (EUR oder USD)
    calendar_changed = Signal()  # Termine wurden angelegt, geändert, gelöscht oder von Yahoo aktualisiert
    fundamentals_changed = Signal(str)  # Kennzahlen einer Aktie wurden geladen (oder der Abruf schlug fehl)
    news_changed = Signal(str)  # die News einer Aktie wurden geladen (oder der Abruf schlug fehl)
    terms_changed = Signal(str)  # die Suchbegriffe einer Aktie (News-Filter) wurden gesammelt
    trade_recorded = Signal(str, str)  # Symbol, "buy" oder "sell"
    status = Signal(str)
    _result = Signal(object, object, object, object)

    def __init__(self):
        super().__init__()
        self.store = Store(sd.DB_FILE)
        self.store.import_legacy(sd.LEGACY_FILE)
        self.symbols = self.store.symbols()
        self.pinned = self._load_pins()
        self._apply_saved_base()
        self.quotes, self.events = {}, {}
        self.transactions, self.positions, self.realized, self.sales = {}, {}, {}, {}
        self.states, self.opening, self.removed_holdings = {}, {}, []
        self.cash = self._load_cash()
        self.calendar = self.store.events()  # alle Termine; angezeigt wird, was zur Watchlist gehört
        self.reload_ledger()
        self.quote_times, self.quote_errors, self.instruments = {}, {}, {}
        self.instrument_tried = set()  # Stammdaten werden je Start höchstens einmal je Aktie versucht
        self.fundamentals = self.store.fundamentals()  # Kennzahlen je Aktie: {"data", "source", "fetched_at"}
        self.fundamentals_errors, self.fundamentals_loading = {}, set()
        self.news_cache, self.news_loading = {}, set()  # News je Aktie: {"news", "error", "fetched_at"}
        self.terms = self.store.search_terms()  # Suchbegriffe je Aktie: {"terms", "source", "fetched_at"}
        self.terms_loading = set()
        self.load_persisted()
        self.fx = FxTable(self.store.fx_rates(), self.store.fx_latest())
        self.fx_history_tried = set()  # Historie wird je Start und Währung einmal nachgeladen
        self.closes = self.store.closes()  # Tageskurse je Aktie für den Verlauf des Portfolios
        self.closes_tried = set()
        self.sources = {}  # Anbindungen für Transaktionen von außen: Name -> sources.TransactionSource
        self.load_etoro()
        self.pool = ThreadPoolExecutor(max_workers=6)
        self.slow_pool = ThreadPoolExecutor(max_workers=1)  # das Sprachmodell braucht Sekunden: eine Aktie nach der anderen
        self.closed = False
        self._result.connect(self._deliver)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)

    def start(self):
        self.refresh()
        self.timer.start(REFRESH_SECONDS * 1000)

    # Angeheftete Boxen: Schlüssel "main", "detail:SYMBOL", "tx:SYMBOL" und "portfolio", in der Datenbank gemerkt
    PINS_KEY = "pinned_windows"

    BASE_KEY, FX_BASE_KEY = "base_currency", "fx_base"

    def _apply_saved_base(self):
        """Stellt die gemerkte Basiswährung ein (ohne Merkzettel Euro). Gespeicherte Wechselkurse gelten nur für die
        Basis, mit der sie geholt wurden; passen sie nicht, werden sie verworfen und neu geladen."""
        saved = self.store.meta(self.BASE_KEY)
        fx_module.set_base(saved if saved in BASES else "EUR")
        if (self.store.meta(self.FX_BASE_KEY) or "EUR") != fx_module.BASE:  # ältere Kurse stammen von der Euro-Basis
            self.store.clear_fx()
        self.store.set_meta(self.FX_BASE_KEY, fx_module.BASE)

    def set_base(self, code):
        """Rechnet das Portfolio ab jetzt in code (EUR oder USD). Die Wechselkurse werden für die neue Basis neu geholt."""
        if code not in BASES or code == fx_module.BASE:
            return
        fx_module.set_base(code)
        self.store.set_meta(self.BASE_KEY, code)
        self.store.clear_fx()
        self.store.set_meta(self.FX_BASE_KEY, code)
        self.fx = FxTable()
        self.fx_history_tried = set()
        self.base_changed.emit(code)
        self.changed.emit()
        self.history_changed.emit()
        self.refresh_fx()

    CASH_KEY = "cash:"  # je Anbindung: {"amount": ..., "currency": ..., "at": ...}

    def _load_cash(self):
        """Verfügbares Guthaben je Anbindung, zuletzt beim Abgleich gemerkt: {Name: (Betrag, Währung, Zeitpunkt)}."""
        result = {}
        for name in ("etoro",):
            try:
                data = json.loads(self.store.meta(self.CASH_KEY + name) or "null")
                if data:
                    result[name] = (float(data["amount"]), data["currency"], dt.datetime.fromisoformat(data["at"]))
            except (ValueError, KeyError, TypeError):
                continue
        return result

    def set_cash(self, name, cash):
        """Merkt das Guthaben einer Anbindung; cash = (Betrag, Währung) oder None (dann bleibt der alte Stand)."""
        if not cash:
            return
        now = dt.datetime.now().replace(microsecond=0)
        self.store.set_meta(self.CASH_KEY + name, json.dumps({"amount": cash[0], "currency": cash[1],
                                                              "at": now.isoformat()}))
        self.cash[name] = (cash[0], cash[1], now)

    SORT_KEY = "sort:"  # je Box ("positions", "watchlist"): {"key": Spalte, "desc": bool}

    def saved_sort(self, box):
        """Gemerkte Sortierung einer Liste als (Spalte, absteigend); ohne gültigen Merkzettel (None, False)."""
        try:
            data = json.loads(self.store.meta(self.SORT_KEY + box) or "null")
            if data and data["key"] in [column[0] for column in COLUMNS]:
                return data["key"], bool(data["desc"])
        except (ValueError, KeyError, TypeError):
            pass
        return None, False

    def set_sort(self, box, key, desc):
        self.store.set_meta(self.SORT_KEY + box, json.dumps({"key": key, "desc": desc}))

    def _load_pins(self):
        try:
            data = json.loads(self.store.meta(self.PINS_KEY) or "[]")
        except ValueError:
            return []
        return [key for key in data if isinstance(key, str)] if isinstance(data, list) else []

    def is_pinned(self, key):
        return key in self.pinned

    def set_pinned(self, key, on):
        if on == (key in self.pinned):
            return
        if on:
            self.pinned.append(key)
        else:
            self.pinned.remove(key)
        self.store.set_meta(self.PINS_KEY, json.dumps(self.pinned))

    def run(self, work, done, fail=None, pool=None):
        def task():
            try:
                self._result.emit(done, work(), None, fail)
            except Exception as exc:
                self._result.emit(done, None, exc, fail)
        (pool or self.pool).submit(task)

    def shutdown(self):
        """Beendet die Netzwerkarbeit; später eintreffende Ergebnisse werden verworfen."""
        self.closed = True
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.slow_pool.shutdown(wait=False, cancel_futures=True)

    def _deliver(self, done, value, error, fail):
        if self.closed:
            return
        if error is None:
            done(value)
        elif fail:
            fail(error)

    def load_persisted(self):
        """Zeigt sofort die zuletzt gespeicherten Kurse und Stammdaten. Ob ein Kurs noch gilt, entscheidet sein Alter."""
        for symbol, snapshot in self.store.snapshots().items():
            if symbol in self.symbols:
                self.quote_times[symbol] = snapshot.pop("fetched_at")
                self.quotes[symbol] = snapshot
        for symbol, info in self.store.instruments().items():
            if symbol in self.symbols:
                self.instruments[symbol] = info

    def reload_ledger(self):
        """Berechnet Bestände und realisierte Gewinne neu aus den gespeicherten Transaktionen."""
        self.transactions = {}
        for tx in self.store.transactions():
            self.transactions.setdefault(tx.symbol, []).append(tx)
        opening = self.store.opening_realized()
        self.opening = opening
        self.positions, self.realized, self.sales, self.states = {}, {}, {}, {}
        for symbol in self.symbols:
            try:
                state = ledger.replay(self.transactions.get(symbol, []))
            except ValueError:
                self.status.emit(f"{symbol}: gespeicherte Transaktionen sind ungültig")
                continue
            realized = state.realized + opening.get(symbol, 0.0)
            self.realized[symbol] = realized
            self.sales[symbol] = {sale.transaction_id: sale for sale in state.sales}
            self.states[symbol] = state
            if state.shares > 0:
                self.positions[symbol] = {"shares": state.shares, "cost": state.avg_cost, "realized": realized}
        # Aktien, die aus der Watchlist entfernt wurden, aber noch Bestand haben, fehlen im Portfolio.
        self.removed_holdings = []
        for symbol, transactions in self.transactions.items():
            if symbol not in self.symbols:
                try:
                    if ledger.replay(transactions).shares > ledger.EPS:
                        self.removed_holdings.append(symbol)
                except ValueError:
                    pass

    def currencies_in_use(self):
        """Fremdwährungen, für die Wechselkurse gebraucht werden, mit dem Tag der ersten Transaktion."""
        needed = {}
        for symbol in self.symbols:
            transactions = self.transactions.get(symbol)
            currency = (self.quotes.get(symbol) or {}).get("currency") or self.instruments.get(symbol, {}).get("currency")
            if not transactions or not currency or currency_code(currency) == fx_module.BASE:
                continue
            code, first = currency_code(currency), min(t.day for t in transactions)
            needed[code] = min(needed.get(code, first), first)
        return needed

    def refresh_fx(self):
        for code, first_day in self.currencies_in_use().items():
            self.load_fx_latest(code)
            if code not in self.fx_history_tried:
                self.load_fx_history(code, first_day)

    def load_fx_latest(self, code):
        def done(rate):
            now = dt.datetime.now().replace(microsecond=0)
            self.fx.set_latest(code, rate, now, sd.SOURCE)
            self.store.save_fx_latest(code, rate, now, sd.SOURCE)
            self.changed.emit()
        base = fx_module.BASE
        self.run(lambda: sd.fetch_fx_rate(code, base), done,
                 lambda exc: self.status.emit(f"Wechselkurs {code} → {base} nicht abrufbar"))

    def load_fx_history(self, code, first_day):
        self.fx_history_tried.add(code)
        start = first_day - dt.timedelta(days=7)  # Puffer für Wochenenden und Feiertage
        known = self.fx.coverage(code)
        if known and known[0] <= start:  # ältere Tage sind schon gespeichert: nur das Neue holen
            start = known[1] - dt.timedelta(days=7)

        def done(rates):
            self.fx.add_history(code, rates)
            self.store.save_fx_rates(code, rates, sd.SOURCE)
            self.changed.emit()
        base = fx_module.BASE
        self.run(lambda: sd.fetch_fx_history(code, base, start), done,
                 lambda exc: self.status.emit(f"Wechselkurs-Historie {code} nicht abrufbar"))

    def refresh_closes(self):
        """Lädt die Tageskurse, die der Verlauf braucht: je Aktie ab dem ersten Eintrag."""
        for symbol in self.symbols:
            transactions = self.transactions.get(symbol)
            if transactions and symbol not in self.closes_tried:
                self.load_closes(symbol, min(t.day for t in transactions))

    def load_closes(self, symbol, first_day):
        self.closes_tried.add(symbol)
        start = first_day - dt.timedelta(days=7)  # Puffer für Wochenenden und Feiertage
        known = sorted(self.closes.get(symbol, {}))
        if known and known[0] <= start:  # ältere Tage sind schon gespeichert: nur das Neue holen
            start = known[-1] - dt.timedelta(days=7)

        def done(closes):
            self.closes.setdefault(symbol, {}).update(closes)
            self.store.save_closes(symbol, closes, sd.SOURCE)
            self.history_changed.emit()
        self.run(lambda: sd.fetch_daily_closes(symbol, start), done,
                 lambda exc: self.status.emit(f"{symbol}: Kursverlauf nicht abrufbar"))

    def performance(self):
        """Verlauf des Portfolios Tag für Tag (siehe history.py); nur Aktien der Watchlist."""
        transactions = {s: self.transactions[s] for s in self.symbols if self.transactions.get(s)}
        currencies = {}
        for symbol in transactions:
            currency = (self.quotes.get(symbol) or {}).get("currency") or self.instruments.get(symbol, {}).get("currency")
            if currency:
                currencies[symbol] = currency
        return history.performance_series(transactions, self.closes, currencies, self.fx)

    def portfolio_summary(self):
        """Kennzahlen des ganzen Portfolios in der Basiswährung (siehe portfolio.py)."""
        summary = portfolio.summarize(self.states, self.quotes, self.instruments, self.fx, self.opening, self.symbols,
                                      [(amount, currency) for amount, currency, _ in self.cash.values()])
        if self.removed_holdings:
            summary.warnings.append("Entfernte Aktien mit Bestand sind nicht enthalten: "
                                    + ", ".join(self.removed_holdings))
        stale = self.stale_symbols()
        if stale:
            summary.warnings.append(f"{len(stale)} Kurs{'e' if len(stale) != 1 else ''} veraltet: " + ", ".join(stale))
        return summary

    def refresh(self):
        self.refresh_fx()
        self.refresh_closes()
        for symbol in list(self.symbols):
            self.load_quote(symbol)
            if symbol not in self.events:
                self.load_events(symbol)
            if self.needs_instrument(symbol):
                self.load_instrument(symbol)
            self.load_terms(symbol)

    def load_terms(self, symbol):
        """Sammelt die Suchbegriffe, wenn sie fehlen oder veraltet sind. Ein Fehlschlag wird je Start nicht
        wiederholt; die gespeicherten Begriffe bleiben dann stehen."""
        if symbol in self.terms_loading or not kw.needs_refresh(self.terms.get(symbol)):
            return False
        self.terms_loading.add(symbol)  # bleibt auch nach dem Abruf drin: je Start höchstens ein Versuch je Aktie

        def done(found):
            if symbol in self.symbols:
                now = dt.datetime.now().replace(microsecond=0)
                self.store.save_search_terms(symbol, found["terms"], found["source"], now)
                self.terms[symbol] = {**found, "fetched_at": now}
                self.terms_changed.emit(symbol)
        self.run(lambda: kw.collect(symbol), done, pool=self.slow_pool)
        return True

    def news_terms(self, symbol):
        """Begriffe, nach denen die News einer Aktie gefiltert werden: die gesammelten, sonst der Firmenname."""
        entry = self.terms.get(symbol)
        if entry and entry["terms"]:
            return entry["terms"]
        return kw.name_terms((self.instruments.get(symbol) or {}).get("name"))

    def load_quote(self, symbol):
        def done(quote):
            if symbol in self.symbols:
                self.store_quote(symbol, quote)
                self.status.emit("Aktualisiert " + dt.datetime.now().strftime("%H:%M:%S"))
                self.changed.emit()

        def failed(exc):
            # Der letzte Kurs bleibt sichtbar, gilt aber ab jetzt als veraltet.
            if symbol in self.symbols:
                self.quote_errors[symbol] = str(exc) or "Abruf fehlgeschlagen"
            self.status.emit(f"{symbol}: Kurs nicht abrufbar")
            self.changed.emit()
        self.run(lambda: sd.fetch_quote(symbol), done, failed)

    def store_quote(self, symbol, quote):
        now = dt.datetime.now().replace(microsecond=0)  # so genau wie in der Datenbank
        self.quotes[symbol] = quote
        self.quote_times[symbol] = now
        self.quote_errors.pop(symbol, None)
        self.store.save_snapshot(symbol, quote, now)

    def freshness(self, symbol, now=None):
        """Wie aktuell der Kurs ist: {stale, fetched_at, source, error}, oder None ohne Kurs."""
        quote = self.quotes.get(symbol)
        if not quote:
            return None
        fetched_at = self.quote_times.get(symbol)
        error = self.quote_errors.get(symbol)
        too_old = fetched_at is not None and ((now or dt.datetime.now()) - fetched_at).total_seconds() > STALE_SECONDS
        return {"stale": bool(error) or too_old, "fetched_at": fetched_at,
                "source": quote.get("source", ""), "error": error}

    def stale_symbols(self, now=None):
        return [s for s in self.symbols if (f := self.freshness(s, now)) and f["stale"]]

    def needs_instrument(self, symbol, now=None):
        if symbol in self.instrument_tried:
            return False
        info = self.instruments.get(symbol)
        return info is None or (now or dt.datetime.now()) - info["fetched_at"] > INSTRUMENT_MAX_AGE

    def load_instrument(self, symbol):
        self.instrument_tried.add(symbol)

        def done(info):
            if symbol in self.symbols:
                now = dt.datetime.now().replace(microsecond=0)
                self.store.save_instrument(symbol, info, now)
                self.instruments[symbol] = {**info, "fetched_at": now}
                self.changed.emit()
        self.run(lambda: sd.fetch_instrument(symbol), done)

    def store_news(self, symbol, news, error=None, now=None):
        """Merkt sich die News einer Aktie (auch die, die das Detailfenster selbst geladen hat). Schlug der Abruf
        fehl, bleiben frühere News stehen und der Fehler wird mitgemerkt."""
        old = self.news_cache.get(symbol)
        keep = news if news is not None else (old or {}).get("news")
        self.news_cache[symbol] = {"news": keep, "error": None if news is not None else str(error),
                                   "fetched_at": now or dt.datetime.now()}
        self.news_changed.emit(symbol)

    def load_news(self, symbol, force=False, now=None):
        """Lädt die News einer Aktie für den Feed, wenn sie fehlen oder älter als NEWS_MAX_AGE sind."""
        entry = self.news_cache.get(symbol)
        current = now or dt.datetime.now()
        fresh = entry is not None and entry["error"] is None and current - entry["fetched_at"] < NEWS_MAX_AGE
        if symbol in self.news_loading or (fresh and not force):
            return False
        self.news_loading.add(symbol)

        def done(news):
            self.news_loading.discard(symbol)
            if symbol in self.symbols:
                self.store_news(symbol, news)

        def failed(exc):
            self.news_loading.discard(symbol)
            if symbol in self.symbols:
                self.store_news(symbol, None, exc)
        self.run(lambda: sd.fetch_news(symbol), done, failed)
        return True

    def feed_symbols(self, positions_only=True):
        """Die Aktien, deren News der Feed zeigt: nur die mit Position oder die ganze Watchlist."""
        return [s for s in self.symbols if not positions_only or s in self.positions]

    def build_feed(self, positions_only=True, hours=newsfeed.DEFAULT_HOURS, limit=newsfeed.DEFAULT_LIMIT,
                   show_minor=False, now=None):
        """Der gemeinsame News-Feed aus den geladenen News (siehe newsfeed.build)."""
        scope = self.feed_symbols(positions_only)
        news = {s: self.news_cache[s]["news"] for s in scope if (self.news_cache.get(s) or {}).get("news") is not None}
        items = newsfeed.merge(news, self.news_terms)
        today = dt.date.today()
        newsfeed.attach_events(items, {s: [m.event for m in self.calendar_events(s, today)] for s in scope}, today)
        return newsfeed.build(items, now, hours, limit, show_minor)

    def load_fundamentals(self, symbol, force=False):
        """Lädt die Kennzahlen, wenn sie fehlen oder älter als fundamentals.MAX_AGE sind (oder mit force).
        Schlägt der Abruf fehl, bleiben die gespeicherten Kennzahlen mit ihrem alten Stand stehen."""
        entry = self.fundamentals.get(symbol)
        if symbol in self.fundamentals_loading or not (
                force or entry is None or fund.needs_refresh(entry["fetched_at"])):
            return False
        self.fundamentals_loading.add(symbol)

        def done(data):
            self.fundamentals_loading.discard(symbol)
            if symbol in self.symbols:
                now = dt.datetime.now().replace(microsecond=0)
                self.store.save_fundamentals(symbol, data, sd.SOURCE, now)
                self.fundamentals[symbol] = {"data": data, "source": sd.SOURCE, "fetched_at": now}
                self.fundamentals_errors.pop(symbol, None)
                self.fundamentals_changed.emit(symbol)

        def failed(exc):
            self.fundamentals_loading.discard(symbol)
            self.fundamentals_errors[symbol] = str(exc)
            if symbol in self.symbols:
                self.fundamentals_changed.emit(symbol)
        self.run(lambda: sd.fetch_fundamentals(symbol), done, failed)
        return True

    def load_events(self, symbol):
        def done(events):
            if symbol in self.symbols:
                self.apply_fetched_events(symbol, events)
                self.changed.emit()
        self.run(lambda: sd.fetch_events(symbol), done)

    def apply_fetched_events(self, symbol, fetched):
        """Bucht die von Yahoo gemeldeten Termine und aktualisiert die Spalte „Termin“ dieser Aktie."""
        self.record_yahoo_events(symbol, fetched)
        self.events[symbol] = self.upcoming_pairs(symbol)

    # Termine (Ereigniskalender), siehe events.py
    def reload_calendar(self):
        """Lädt die Termine neu; die Spalte „Termin“ der schon geladenen Aktien folgt dem Kalender."""
        self.calendar = self.store.events()
        for symbol in list(self.events):
            self.events[symbol] = self.upcoming_pairs(symbol)
        self.calendar_changed.emit()
        self.changed.emit()

    def calendar_events(self, symbol=None, today=None):
        """Die Termine der Watchlist (oder einer Aktie), Doppelte zusammengeführt (events.MergedEvent)."""
        scope = [e for e in self.calendar if e.symbol in self.symbols and (symbol is None or e.symbol == symbol)]
        return evt.merge_duplicates(scope, today or dt.date.today())

    def upcoming_pairs(self, symbol, today=None):
        """Kommende Termine mit genauem Tag als [(Tag, Art)], wie sie die Spalte „Termin“ braucht."""
        today = today or dt.date.today()
        return [(max(m.event.day, today), evt.KINDS[m.event.kind])
                for m in evt.upcoming(self.calendar_events(symbol, today), today) if m.event.precision == "day"]

    def calendar_window(self, days, positions_only=False, today=None, symbol=None):
        """Kommende Termine der nächsten days Tage: (mit genauem Tag, ohne genauen Tag)."""
        today = today or dt.date.today()
        merged = [m for m in self.calendar_events(symbol, today)
                  if not positions_only or m.event.symbol in self.positions]
        return evt.calendar_window(merged, today, days)

    def record_yahoo_events(self, symbol, fetched, today=None):
        """Bucht die von Yahoo gemeldeten künftigen Termine [(Tag, Bezeichnung)] als „erwartet“: Yahoo sagt nicht, ob
        das Unternehmen den Termin bestätigt hat. Nennt Yahoo für eine Art mehrere Tage, ist das ein Zeitraum.
        Verschiebt sich ein Termin, ersetzt der neue den alten; vergangene bleiben als „eingetreten“ stehen."""
        today = today or dt.date.today()
        by_kind = {}
        for day, label in fetched or []:
            kind = evt.YAHOO_KINDS.get(label)
            if kind:
                by_kind.setdefault(kind, []).append(day)
        changed = False
        for kind, days in by_kind.items():
            start, end = min(days), max(days)
            external_id = f"{kind}:{start.isoformat()}"
            fields = {"symbol": symbol, "kind": kind, "title": evt.KINDS[kind], "day": start, "end": end,
                      "precision": "day", "status": "expected", "relevance": evt.default_relevance(kind),
                      "note": "", "source_url": ""}
            changed |= self.store.upsert_event(sd.SOURCE, external_id, fields)[1]
            changed |= bool(self.store.delete_stale_events(sd.SOURCE, symbol, kind, {external_id}, today))
        if changed:
            self.calendar = self.store.events()
            self.calendar_changed.emit()
        return changed

    def _manual_event(self, event_id):
        event = next((e for e in self.calendar if e.id == event_id), None)
        if event is None:
            raise ValueError("Der Termin existiert nicht mehr")
        if not event.is_manual:
            raise ValueError(f"Dieser Termin stammt von {event.source} und lässt sich nicht ändern. "
                             "Lege bei Bedarf einen eigenen Termin an.")
        return event

    def add_event(self, symbol, kind, title, when, status, relevance=None, note="", source_url=""):
        """Legt einen eigenen Termin an (ValueError bei ungültigen Eingaben); gibt seine Nummer zurück."""
        if symbol.strip().upper() not in self.symbols:
            raise ValueError("Die Aktie steht nicht auf deinen Listen")
        fields = evt.make_event(symbol, kind, title, when, status, relevance, note, source_url)
        event_id = self.store.add_event(fields)
        self.reload_calendar()
        return event_id

    def update_event(self, event_id, kind, title, when, status, relevance=None, note="", source_url=""):
        event = self._manual_event(event_id)
        self.store.update_event(event_id, evt.make_event(event.symbol, kind, title, when, status, relevance, note,
                                                         source_url))
        self.reload_calendar()

    def delete_event(self, event_id):
        self._manual_event(event_id)
        self.store.delete_event(event_id)
        self.reload_calendar()

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
            self.store_quote(symbol, quote)
            self.reload_ledger()
            self.changed.emit()
            self.ledger_changed.emit()
            self.load_events(symbol)
            self.load_instrument(symbol)
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
        for cache in (self.quotes, self.events, self.quote_times, self.quote_errors, self.instruments,
                      self.fundamentals, self.fundamentals_errors, self.terms, self.news_cache):
            cache.pop(symbol, None)
        self.terms_loading.discard(symbol)
        for key in (f"detail:{symbol}", f"tx:{symbol}"):
            self.set_pinned(key, False)
        self._ledger_changed()

    # Transaktionen: die Methoden werfen ValueError bei ungültigen Eingaben
    def record_trade(self, symbol, kind, shares, price, fee, day, note="", source="manual"):
        candidate = ledger.Transaction(0, symbol, kind, shares, price, fee, day, note, source)
        ledger.replay(self.transactions.get(symbol, []) + [candidate])  # prüft den ganzen Verlauf
        self.store.add_transaction(symbol, kind, shares, price, fee, day, note, source)
        self._ledger_changed()
        self.trade_recorded.emit(symbol, kind)

    def start_position(self, symbol, shares, pl):
        """Startbestand aus Stückzahl und aktuellem Gewinn/Verlust in %, gespeichert als Kauf."""
        if symbol in self.positions:
            raise ValueError("Es gibt schon eine Position")
        if pl <= -100:
            raise ValueError("Der Gewinn/Verlust muss über -100 % liegen")
        price = self.quotes[symbol]["price"] / (1 + pl / 100)
        self.record_trade(symbol, "buy", shares, price, 0.0, dt.date.today(), ledger.OPENING_NOTE,
                          ledger.OPENING_SOURCE)

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
        self.fx_history_tried.clear()  # ein neuer, womöglich älterer Kauf braucht evtl. mehr Historie
        self.closes_tried.clear()
        self.refresh_fx()
        self.refresh_closes()
        self.changed.emit()
        self.ledger_changed.emit()

    # Anbindungen für Transaktionen von außen (zum Beispiel eToro), siehe sources.py
    def known_symbols(self):
        return set(self.symbols) | set(self.transactions)

    def import_trades(self, source, trades, replace_openings=False, cursor="", notes=(), adopt_unknown=False,
                      cash=None):
        """Bucht die Einträge einer Anbindung und gibt den sources.ImportPlan zurück: was neu ist, was schon
        da war, was ein unbekanntes Kürzel hat und was den Verlauf ungültig machen würde (wird nicht gebucht).
        Mit replace_openings ersetzen echte Käufe den Startbestand der betroffenen Aktien."""
        known = self.known_symbols()
        if adopt_unknown:  # die Anbindung liefert Yahoo-Kürzel: unbekannte Aktien kommen auf die Watchlist
            known |= {trade.symbol.upper() for trade in trades}
        plan = sources.plan_import(source, trades, self.store.transactions(), self.store.aliases(source),
                                   known, replace_openings)
        before = set(self.symbols)
        for symbol, trade in plan.new:
            self.store.add_symbol(symbol)  # nimmt auch eine zuvor entfernte Aktie wieder auf
            self.store.add_transaction(symbol, trade.kind, trade.shares, trade.price, trade.fee, trade.day,
                                       trade.note, source, trade.external_id)
        self.store.delete_transactions(plan.remove_ids)
        self.set_cash(source, cash)
        self.symbols = self.store.symbols()
        self.store.save_sync_state(source, cursor, dt.datetime.now(),
                                   f"{len(plan.new)} neu, {plan.duplicates} schon vorhanden, "
                                   f"{len(plan.rejected)} abgelehnt, {sum(plan.unmapped.values())} mit unbekanntem Kürzel"
                                   + "".join(f"; {note}" for note in notes))
        self._ledger_changed()
        for symbol in self.symbols:
            if symbol not in before:
                self.load_quote(symbol)
                self.load_events(symbol)
                self.load_instrument(symbol)
        self.history_changed.emit()
        return plan

    def sync_source(self, name, on_done, on_error, replace_openings=False, adopt_unknown=False):
        """Holt neue Einträge von der Anbindung name (im Hintergrund) und bucht sie."""
        source = self.sources[name]
        state = self.store.sync_state(name)
        cursor = state["cursor"] if state else ""

        def done(batch):
            on_done(self.import_trades(name, batch.trades, replace_openings, batch.cursor, batch.notes, adopt_unknown,
                                       getattr(batch, "cash", None)))
        self.run(lambda: source.fetch(cursor), done, on_error)

    # eToro (nur lesend), siehe etoro.py
    def load_etoro(self):
        """Meldet die eToro-Anbindung an, wenn Schlüssel gespeichert sind, sonst ab."""
        keys = etoro.load_credentials(sd.DATA_DIR)
        if keys:
            self.sources["etoro"] = etoro.EtoroSource(etoro.EtoroClient(*keys))
        else:
            self.sources.pop("etoro", None)

    def connect_etoro(self, api_key, user_key):
        etoro.save_credentials(sd.DATA_DIR, api_key, user_key)
        self.load_etoro()

    def disconnect_etoro(self):
        """Löscht nur die Schlüssel; die bereits übernommenen Transaktionen bleiben."""
        etoro.delete_credentials(sd.DATA_DIR)
        self.load_etoro()

    def sync_etoro(self, on_done, on_error):
        """Gleicht mit eToro ab; echte Käufe ersetzen dabei den Startbestand."""
        self.sync_source("etoro", on_done, on_error, replace_openings=True, adopt_unknown=True)

    def set_alias(self, source, source_symbol, symbol):
        """Ordnet das Kürzel einer Anbindung einem Yahoo-Kürzel zu (gilt für spätere Importe)."""
        self.store.set_alias(source, source_symbol.strip(), symbol.strip().upper())


# ---------- Dialoge ----------

class Choice:
    """Auswahlfeld für FieldDialog: options = [(Beschriftung, Wert)], current = der vorgewählte Wert."""

    def __init__(self, options, current=None):
        self.options, self.current = list(options), current


class FieldDialog(QDialog):
    """Dialog mit Eingabefeldern (Zahlen, Text, Datum) und Auswahlfeldern (Choice). apply(werte) darf ValueError
    werfen; die Meldung erscheint im Dialog."""

    def __init__(self, title, intro, fields, apply=lambda values: None, ok_text="OK", cancel=True, modal=True,
                 field_width=150):
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
            row = QHBoxLayout()
            row.addWidget(QLabel(label))
            if isinstance(default, Choice):
                self.parsers.append(lambda value: value)
                entry = QComboBox()
                for text, value in default.options:
                    entry.addItem(text, value)
                entry.setCurrentIndex(max(entry.findData(default.current), 0))
            else:
                self.parsers.append(parser[0] if parser else sd.parse_number)
                entry = QLineEdit(default)
                entry.returnPressed.connect(self.submit)
            entry.setFixedWidth(field_width)
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
            if hasattr(self.entries[0], "selectAll"):
                self.entries[0].selectAll()

    @staticmethod
    def value_of(entry):
        return entry.currentData() if isinstance(entry, QComboBox) else entry.text()

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
        if hasattr(self.entries[0], "selectAll"):
            self.entries[0].selectAll()

    def submit(self):
        try:
            values = [parse(self.value_of(entry)) for parse, entry in zip(self.parsers, self.entries)]
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
    ("day", "Tag", 92, Qt.AlignRight),  # 18 davon für die Statuslampe
    ("value", "Position", 76, Qt.AlignRight),
    ("pl", "G/V %", 74, Qt.AlignRight),
    ("amount", "G/V", 80, Qt.AlignRight),
    ("event", "Termin", None, Qt.AlignLeft),
)
WATCHLIST_HIDDEN = ("value", "pl", "amount")  # ohne Position gibt es dort nichts anzuzeigen
ROW_MARGINS = (12, 0, 12, 0)
ROW_SPACING = 8
MARKET_LAMPS = {"open": (GREEN, "Börse geöffnet"), "extended": (AMBER, "Vor- oder Nachbörse"),
                "closed": (MUTED, "Börse geschlossen")}


class StockCard(QFrame):
    """Eine Zeile der Watchlist; jede Spalte steht in einem eigenen Label mit fester Breite."""
    clicked = Signal(str)
    menu_requested = Signal(str)

    def __init__(self, symbol, hidden=()):
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
            label.setVisible(key not in hidden)  # bleibt als Label da, belegt aber keinen Platz
            if key == "day":
                # Statuslampe der Börse links neben der Tagesveränderung
                self.lamp = QLabel()
                self.lamp.setFixedSize(8, 8)
                cell = QWidget()
                cell.setObjectName("clear")
                cell.setFixedWidth(width)
                inner = QHBoxLayout(cell)
                inner.setContentsMargins(0, 0, 0, 0)
                inner.setSpacing(10)
                inner.addWidget(self.lamp, 0, Qt.AlignVCenter)
                inner.addWidget(label, 1)
                self.labels[key] = label
                row.addWidget(cell, 0)
                continue
            if width:
                label.setFixedWidth(width)
            self.labels[key] = label
            row.addWidget(label, 0 if width else 1)
        self.price, self.day = self.labels["price"], self.labels["day"]
        self.value, self.pl = self.labels["value"], self.labels["pl"]
        self.amount, self.event = self.labels["amount"], self.labels["event"]
        self.labels["symbol"].setText(symbol)
        self.labels["symbol"].setStyleSheet("font-weight: 700;")
        self.price.setStyleSheet("font-weight: 700;")
        self.event.setStyleSheet(f"color: {MUTED}; font-size: 11px;")

    def set_data(self, quote, position, events, freshness=None):
        stale = bool(freshness and freshness["stale"])
        if events:
            self.event.setText(sd.format_event(events[0], short=True))
            self.values["event"] = events[0][0]
        else:
            self.event.setText("kein Termin" if events is not None else "…")
            self.values["event"] = None
        for key in ("price", "day", "value", "pl", "amount"):
            self.labels[key].setText("")
            self.values[key] = None
        self.price.setToolTip("")
        self.day.setToolTip("")
        self.set_market(None, stale=False)
        self.value.setToolTip("")
        if not quote:
            return
        self.price.setText(f"{quote['price']:.2f}")
        # Ein veralteter Kurs ist gelb und kursiv; der Tooltip nennt Stand und Quelle.
        self.price.setStyleSheet("font-weight: 700;" + (f" color: {AMBER}; font-style: italic;" if stale else ""))
        self.price.setToolTip(freshness_text(freshness))
        self.day.setToolTip(freshness_text(freshness))
        self.values["price"] = quote["price"]
        change = quote["change_pct"]
        if round(change, 2) == 0:  # keine Veränderung, etwa weil die Börse noch nicht geöffnet hat
            self.day.setText("–")
            self.day.setStyleSheet(f"color: {MUTED}; font-weight: 600;")
            change = None  # wie eine leere Zelle: beim Sortieren immer ganz unten
        else:
            self.day.setText(f"{arrow(change)} {change:+.2f} %")
            self.day.setStyleSheet(f"color: {sign_color(change)}; font-weight: 600;")
        self.values["day"] = change
        self.set_market(quote.get("market_state"), stale)
        if position:
            pl = sd.pl_percent(position, quote["price"])
            amount = sd.pl_amount(position, quote["price"])
            value = quote["price"] * position["shares"]
            self.value.setText(f"{value:,.2f}")
            self.value.setToolTip(f"{position['shares']:g} Stück")
            self.pl.setText(f"{pl:+.2f} %")
            self.pl.setStyleSheet(f"color: {sign_color(pl)}; font-weight: 600;")
            self.amount.setText(f"{amount:+.2f}")
            self.amount.setStyleSheet(f"color: {sign_color(amount)};")
            self.values.update(value=value, pl=pl, amount=amount)

    def set_market(self, state, stale):
        """Statuslampe: grün = Börse offen, gelb = Vor-/Nachbörse, grau = geschlossen; ohne Status oder bei
        veraltetem Kurs aus, denn dann wüsste man nicht, ob sie noch stimmt."""
        shown = None if stale else MARKET_LAMPS.get(state)
        self.market_state = None if shown is None else state
        if shown is None:
            self.lamp.setStyleSheet("background: transparent;")
            self.lamp.setToolTip("")
            return
        color, text = shown
        self.lamp.setStyleSheet(f"background: {color}; border-radius: 4px;")
        self.lamp.setToolTip(text)

    def flash(self, color):
        flash(self, color)

    def set_open(self, is_open):
        """Hebt die Zeile hervor, solange das Detailfenster dieser Aktie offen ist."""
        if bool(self.property("open")) != is_open:
            self.setProperty("open", is_open)
            self.style().unpolish(self)
            self.style().polish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.symbol)

    def contextMenuEvent(self, event):
        self.menu_requested.emit(self.symbol)


class MainWindow(QWidget):
    """Die Box „Positionen“: Aktien, von denen Stücke gehalten werden. Mit watchlist_of=<Positionen-Fenster> ist es
    stattdessen die Box „Watchlist“ mit den übrigen Aktien; sie teilen sich die offenen Detailfenster."""

    def __init__(self, ctl, watchlist_of=None):
        super().__init__()
        self.ctl = ctl
        self.cards = {}
        self.is_watchlist = watchlist_of is not None
        self.details = watchlist_of.details if self.is_watchlist else {}
        self.peers = [watchlist_of] if self.is_watchlist else []
        if self.is_watchlist:
            watchlist_of.peers.append(self)
        self.hidden = WATCHLIST_HIDDEN if self.is_watchlist else ()  # Spalten, die diese Box nicht zeigt
        self.watchlist = None   # nur in der Box „Positionen“: die Box „Watchlist“, sobald sie einmal geöffnet wurde
        self.add_dialog = None
        self.box = "watchlist" if self.is_watchlist else "positions"
        self.sort_key, self.sort_desc = ctl.saved_sort(self.box)  # None = Reihenfolge der Aktien

        self.pin =QPushButton("◉")
        self.pin.setObjectName("icon")
        self.pin.setCheckable(True)
        self.pin.setChecked(PIN["on"])
        self.pin.setToolTip("Im Vordergrund halten")
        self.pin.setCursor(Qt.PointingHandCursor)
        self.pin.clicked.connect(self.toggle_pin)

        self.add_button = QPushButton("+")
        self.add_button.setObjectName("icon")
        self.add_button.setToolTip("Aktie zur Watchlist hinzufügen")
        self.add_button.setCursor(Qt.PointingHandCursor)
        self.add_button.clicked.connect(self.ask_symbol)

        self.portfolio_button = QPushButton("◔")
        self.portfolio_button.setObjectName("icon")
        self.portfolio_button.setToolTip("Portfolio-Übersicht")
        self.portfolio_button.setCursor(Qt.PointingHandCursor)
        self.portfolio_button.clicked.connect(lambda: open_portfolio(self.ctl, above=self))

        self.calendar_button = QPushButton("▦")
        self.calendar_button.setObjectName("icon")
        self.calendar_button.setToolTip("Termine der nächsten Tage")
        self.calendar_button.setCursor(Qt.PointingHandCursor)
        self.calendar_button.clicked.connect(lambda: open_calendar(self.ctl, open_symbol=self.open_detail))

        self.news_button = QPushButton("≡")
        self.news_button.setObjectName("icon")
        self.news_button.setToolTip("News-Feed")
        self.news_button.setCursor(Qt.PointingHandCursor)
        self.news_button.clicked.connect(lambda: open_news(self.ctl, open_symbol=self.open_detail))

        self.watchlist_button = QPushButton("☆")
        self.watchlist_button.setObjectName("icon")
        self.watchlist_button.setToolTip("Watchlist öffnen oder schließen")
        self.watchlist_button.setCursor(Qt.PointingHandCursor)
        self.watchlist_button.clicked.connect(self.toggle_watchlist)

        if self.is_watchlist:
            body = make_panel(self, "Watchlist", self.hide_docked, extras=[self.add_button],
                              pin=(ctl, "watchlist"))
        else:
            body = make_panel(self, "Positionen", self.hide_docked,
                              extras=[self.news_button, self.calendar_button, self.portfolio_button, self.watchlist_button,
                                    self.pin])

        self.header = QWidget()
        header = QHBoxLayout(self.header)
        header.setContentsMargins(ROW_MARGINS[0], 0, ROW_MARGINS[2] + 12, 0)  # 12 = Rand und Scrollleiste rechts
        header.setSpacing(ROW_SPACING)
        self.heads = {}
        for key, title, width, align in COLUMNS:
            button = QPushButton(title)
            button.setObjectName("head")
            button.setVisible(key not in self.hidden)
            button.setCursor(Qt.PointingHandCursor)
            if key in HEAD_TERMS:
                explain(button, HEAD_TERMS[key], note="Ein Klick sortiert die Liste nach dieser Spalte.")
            else:
                button.setToolTip("Nach dieser Spalte sortieren")
            if width:
                button.setFixedWidth(width)
            button.clicked.connect(lambda _=False, k=key: self.sort_by(k))
            self.heads[key] = button
            header.addWidget(button, 0 if width else 1)
        body.addWidget(self.header)

        self.area, self.list = scroll_area()
        self.empty = QLabel("Keine Aktie auf der Watchlist.\nMit + oben ein Kürzel oder einen Namen eingeben."
                            if self.is_watchlist else
                            "Noch keine Positionen.\nMit ☆ oben die Watchlist öffnen, dort eine Aktie hinzufügen "
                            "und einen Kauf eintragen.")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setStyleSheet(f"color: {MUTED};")
        self.list.addWidget(self.empty)
        self.list.addStretch()
        body.addWidget(self.area, 1)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
        self.stale_note = QLabel()
        self.stale_note.setStyleSheet(f"color: {AMBER}; font-size: 10px; font-weight: 600;")
        self.stale_note.hide()
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        footer.addWidget(self.stale_note)
        body.addLayout(footer)

        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedHeight(min(640, screen.height() - 20))

        ctl.changed.connect(self.sync)
        ctl.status.connect(self.status.setText)
        ctl.trade_recorded.connect(self.flash_card)
        # Ein Kurs veraltet auch, ohne dass neue Daten kommen; deshalb prüft ein Timer das Alter regelmäßig.
        self.fresh_timer = QTimer(self)
        self.fresh_timer.timeout.connect(self.refresh_cards)
        self.fresh_timer.start(15_000)
        self.sync()

    def show_docked(self):
        Dock.add(self)
        self.show()
        self.raise_()
        self.activateWindow()

    def hide_docked(self):
        Dock.remove(self)
        self.hide()

    def closeEvent(self, event):
        Dock.remove(self)
        super().closeEvent(event)

    def toggle_watchlist(self):
        """Öffnet die Box „Watchlist“ (beim ersten Mal wird sie angelegt) oder schließt sie wieder."""
        if self.watchlist is not None and self.watchlist.isVisible():
            self.watchlist.hide_docked()
        else:
            self.open_watchlist()

    def open_watchlist(self):
        if self.watchlist is None:
            self.watchlist = MainWindow(self.ctl, watchlist_of=self)
        self.watchlist.show_docked()

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

    def shown_symbols(self):
        """Positionen: Aktien mit gehaltenen Stücken. Watchlist: alle übrigen. Eine Aktie wechselt von selbst die Box."""
        return [s for s in self.ctl.symbols if (s in self.ctl.positions) != self.is_watchlist]

    def sync(self):
        shown = self.shown_symbols()
        for symbol in list(self.cards):
            if symbol not in shown:
                self.cards.pop(symbol).deleteLater()
        for symbol in shown:
            if symbol not in self.cards:
                card = StockCard(symbol, self.hidden)
                card.clicked.connect(self.toggle_detail)
                card.menu_requested.connect(self.show_menu)
                self.cards[symbol] = card
                self.list.insertWidget(self.list.count() - 1, card)
        self.refresh_cards()
        self.empty.setVisible(not self.cards)
        self.header.setVisible(bool(self.cards))
        self.reorder()
        self.fit_width()

    def refresh_cards(self):
        for symbol, card in self.cards.items():
            card.set_open(symbol in self.details)
            card.set_data(self.ctl.quotes.get(symbol), self.ctl.positions.get(symbol),
                          self.ctl.events.get(symbol), self.ctl.freshness(symbol))
        stale = self.ctl.stale_symbols()
        if stale:
            self.stale_note.setText(f"⚠ {len(stale)} Kurs{'e' if len(stale) != 1 else ''} veraltet")
            self.stale_note.setToolTip("\n".join(
                f"{s}: " + freshness_text(self.ctl.freshness(s)).replace("\n", " · ") for s in stale))
            self.stale_note.show()
        else:
            self.stale_note.hide()

    def fit_width(self):
        """Das Fenster ist genau so breit, wie die Spalten brauchen; der Termin-Text bestimmt die letzte Spalte."""
        event = max([c.event.sizeHint().width() for c in self.cards.values()]
                    + [self.heads["event"].sizeHint().width()])
        for card in self.cards.values():
            card.event.setMinimumWidth(event)
        self.heads["event"].setMinimumWidth(event)
        fixed = [width for key, _, width, _ in COLUMNS if width and key not in self.hidden]
        row = ROW_MARGINS[0] + ROW_MARGINS[2] + sum(fixed) + event + ROW_SPACING * len(fixed)
        # + Scrollleiste samt Rand (12), Innenrand des Panels (32), Rahmen (2), Schattenrand des Fensters (28)
        width = row + 12 + 32 + 2 + 28
        if width != self.width():
            self.setFixedWidth(width)
            Dock.arrange()

    def sort_by(self, key):
        """Erster Klick sortiert aufsteigend, jeder weitere auf dieselbe Spalte kehrt die Richtung um."""
        self.sort_desc = (not self.sort_desc) if key == self.sort_key else False
        self.sort_key = key
        self.ctl.set_sort(self.box, key, self.sort_desc)
        self.reorder()

    def ordered_symbols(self):
        symbols = [s for s in self.shown_symbols() if s in self.cards]
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

    def mark_open(self, symbol):
        for window in [self, *self.peers]:
            card = window.cards.get(symbol)
            if card:
                card.set_open(symbol in self.details)

    def on_detail_closed(self, symbol):
        self.details.pop(symbol, None)
        self.mark_open(symbol)

    def toggle_detail(self, symbol):
        """Klick auf eine Zeile: öffnet das Detailfenster, ein zweiter Klick schließt es wieder."""
        existing = self.details.get(symbol)
        if existing:
            existing.close()  # das Schließen meldet sich über on_detail_closed ab und löscht die Markierung
        else:
            self.open_detail(symbol)

    def open_detail(self, symbol):
        existing = self.details.get(symbol)
        if existing:
            existing.raise_()
            existing.activateWindow()
            return
        window = DetailWindow(self.ctl, symbol)
        window.closed.connect(self.on_detail_closed)
        self.details[symbol] = window
        self.mark_open(symbol)
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
        opening = ledger.is_opening(tx)
        kind = "Startbestand" if opening else "Kauf" if tx.kind == "buy" else "Verkauf"
        title = QLabel(f"{kind} · {tx.shares:g} Stk · {tx.price:.2f} {cur}")
        title.setStyleSheet("font-weight: 700;")
        if opening:
            explain(title, "startbestand")
            details = [f"eingetragen {tx.day:%d.%m.%Y}", "Kaufdatum unbekannt"]
        else:
            details = [tx.day.strftime("%d.%m.%Y")]
        if tx.fee:
            details.append(f"Gebühr {tx.fee:.2f}")
        if tx.note and not opening:
            details.append(tx.note)
        sub = QLabel(" · ".join(details))
        sub.setWordWrap(True)
        sub.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        left.addWidget(title)
        left.addWidget(sub)
        row.addLayout(left, 1)
        imported = tx.source not in ("", "manual", ledger.OPENING_SOURCE)
        if imported:  # Herkunft sichtbar machen: dieser Eintrag stammt von einer Anbindung, nicht von Hand
            badge = QLabel(history.source_label(tx.source))
            style_pill(badge, ACCENT)
            row.addWidget(badge)
        sale = ctl.sales.get(symbol, {}).get(tx.id)
        if sale:
            gain = QLabel(f"{sale.gain:+.2f} {cur}")
            style_pill(gain, sign_color(sale.gain), strong=True)
            explain(gain, "realisiert")
            row.addWidget(gain)
        delete = QPushButton("✕")
        delete.setObjectName("icon")
        delete.setToolTip("Transaktion löschen")
        delete.setCursor(Qt.PointingHandCursor)
        delete.clicked.connect(lambda: FieldDialog(
            "Transaktion löschen",
            f"{kind} von {tx.shares:g} Stück am {tx.day:%d.%m.%Y} wirklich löschen? "
            "Bestand und Gewinne werden neu berechnet."
            + (f" Der Eintrag stammt von {history.source_label(tx.source)} und wird beim nächsten Abgleich "
               "wieder angelegt." if imported else ""), [],
            lambda _values: ctl.delete_transaction(symbol, tx.id), ok_text="Löschen").run())
        row.addWidget(delete)


class TransactionsWindow(QWidget):
    closed = Signal(str)

    def __init__(self, ctl, symbol):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.ctl, self.symbol = ctl, symbol
        body = make_panel(self, f"{symbol}: Transaktionen", self.close, pin=(ctl, f"tx:{symbol}"))
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        self.summary.setStyleSheet(f"color: {MUTED};")
        body.addWidget(self.summary)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color: {AMBER}; font-size: 11px;")
        body.addWidget(self.hint)
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
        self.hint.setText(self.history_hint(history.summarize_history(transactions)))
        self.hint.setVisible(bool(self.hint.text()))

    def history_hint(self, summary):
        """Erklärt, was die Liste (noch) nicht enthält, und nennt den Stand der Anbindungen."""
        lines = []
        if summary and summary.only_opening:
            lines.append("Nur Startbestand: Das Kaufdatum ist unbekannt, deshalb gibt es noch keine echte Kaufhistorie. "
                         "Sie entsteht mit neuen Käufen und Verkäufen und mit einer Anbindung wie eToro.")
        for name, source in self.ctl.sources.items():
            state = self.ctl.store.sync_state(name)
            lines.append(f"{source.label}: " + (f"zuletzt abgeglichen {state['last_sync_at']:%d.%m. %H:%M} "
                                                f"({state['message']})" if state else "noch nicht abgeglichen"))
        return "\n".join(lines)

    def closeEvent(self, event):
        Dock.remove(self)
        try:
            self.ctl.ledger_changed.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        self.closed.emit(self.symbol)
        super().closeEvent(event)


# ---------- Detailfenster ----------

class PriceChart(QWidget):
    # Marken: Kauf grün (Dreieck nach oben), Verkauf rot (nach unten), Startbestand als Ring;
    # dazu der Einstandskurs als gestrichelte Linie.
    """Liniendiagramm eines Kursverlaufs mit Hover-Anzeige; points = [(datetime, Kurs)] oder None."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(150)
        self.setMouseTracking(True)
        self.points, self.note, self.hover = None, "Wird geladen …", None
        self.markers, self.cost, self.hover_marker = [], None, None

    def show_points(self, points, note=""):
        self.points, self.note, self.hover = points, note, None
        self.markers, self.cost, self.hover_marker = [], None, None
        self.update()

    def set_overlay(self, markers, cost=None):
        """Käufe und Verkäufe als Marken (history.Marker) und der Einstandskurs als gestrichelte Linie."""
        self.markers, self.cost, self.hover_marker = list(markers), cost, None
        self.update()

    def _x(self, index):
        rect = self._plot_rect()
        return rect.left() + rect.width() * index / max(len(self.points) - 1, 1)

    def _plot_rect(self):
        return self.rect().adjusted(4, 6, -44, -18)  # rechts Platz für die Kurswerte, unten für die Daten

    def mouseMoveEvent(self, event):
        if self.points:
            rect = self._plot_rect()
            share = (event.position().x() - rect.left()) / max(rect.width(), 1)
            self.hover = min(max(round(share * (len(self.points) - 1)), 0), len(self.points) - 1)
            self.hover_marker = next((m for m in self.markers if abs(self._x(m.index) - event.position().x()) <= 8),
                                     None)
            self.update()

    def leaveEvent(self, event):
        self.hover = self.hover_marker = None
        self.update()

    def _draw_guides(self, painter, rect, low, high):
        """Waagerechte Linien mit Kurswerten (rechts) und senkrechte mit Daten (unten)."""
        guide = QColor(MUTED)
        guide.setAlpha(60)
        small = painter.font()
        small.setPixelSize(10)
        painter.setFont(small)
        count = len(self.points)
        days = (self.points[-1][0] - self.points[0][0]).days
        form = "%d.%m. %H:%M" if days <= 8 else "%d.%m.%y" if days <= 400 else "%m/%Y"
        for step in range(4):
            y = rect.bottom() - rect.height() * step / 3
            painter.setPen(QPen(guide, 1, Qt.DotLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(rect.right() + 4, y - 7, 40, 14), Qt.AlignLeft | Qt.AlignVCenter,
                             f"{low + (high - low) * step / 3:.2f}")
        for step in range(4):
            x = rect.left() + rect.width() * step / 3
            painter.setPen(QPen(guide, 1, Qt.DotLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            stamp = self.points[round((count - 1) * step / 3)][0]
            align = (Qt.AlignLeft, Qt.AlignHCenter, Qt.AlignHCenter, Qt.AlignRight)[step]
            box = QRectF(x - 40 if step else x, rect.bottom() + 3, 80, 12)
            if step == 3:
                box.moveRight(x)
            elif step == 0:
                box.moveLeft(x)
            painter.setPen(QColor(MUTED))
            painter.drawText(box, align | Qt.AlignVCenter, stamp.strftime(form))

    def _draw_overlay(self, painter, rect, low, span, pos):
        """Einstandslinie und Marken für Käufe und Verkäufe."""
        if self.cost:
            y = rect.bottom() - rect.height() * (self.cost - low) / span
            painter.setPen(QPen(QColor(AMBER), 1, Qt.DashLine))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            painter.setPen(QColor(AMBER))
            painter.drawText(QRectF(rect.left() + 2, y - 13, 120, 12), Qt.AlignLeft | Qt.AlignVCenter,
                             f"Einstand {self.cost:.2f}")
        for marker in self.markers:
            x = self._x(marker.index)
            y = min(max(rect.bottom() - rect.height() * (marker.price - low) / span, rect.top() + 6), rect.bottom() - 6)
            color = QColor(GREEN if marker.kind == "buy" else RED)
            size = 5.5 if marker is not self.hover_marker else 7.5
            if marker.opening:
                painter.setBrush(QColor(BG))
                painter.setPen(QPen(QColor(ACCENT), 2))
                painter.drawEllipse(QPointF(x, y), size - 1, size - 1)
                continue
            up = marker.kind == "buy"
            tip = -size if up else size
            painter.setBrush(color)
            painter.setPen(QPen(QColor(BG), 1))
            painter.drawPolygon(QPolygonF([QPointF(x, y + tip), QPointF(x - size, y - tip), QPointF(x + size, y - tip)]))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self._plot_rect()
        if not self.points:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter, self.note)
            return
        prices = [p for _, p in self.points]
        low, high = min(prices), max(prices)
        if self.cost:  # der Einstandskurs soll immer im Bild liegen
            low, high = min(low, self.cost), max(high, self.cost)
        span = (high - low) or 1.0
        color = QColor(sign_color(prices[-1] - prices[0]))

        def pos(i):
            x = rect.left() + rect.width() * i / (len(prices) - 1)
            return QPointF(x, rect.bottom() - rect.height() * (prices[i] - low) / span)

        self._draw_guides(painter, rect, low, high)
        path = QPainterPath(pos(0))
        for i in range(1, len(prices)):
            path.lineTo(pos(i))
        fill = QPainterPath(path)
        fill.lineTo(rect.right(), rect.bottom())
        fill.lineTo(rect.left(), rect.bottom())
        fill.closeSubpath()
        shade = QColor(color)
        shade.setAlpha(40)
        painter.fillPath(fill, shade)
        painter.setPen(QPen(color, 1.8))
        painter.drawPath(path)
        self._draw_overlay(painter, rect, low, span, pos)
        if self.hover is not None:
            stamp, price = self.points[self.hover]
            point = pos(self.hover)
            painter.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
            painter.drawLine(QPointF(point.x(), rect.top()), QPointF(point.x(), rect.bottom()))
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(point, 3.5, 3.5)
            text = self.hover_marker.text if self.hover_marker else f"{stamp:%d.%m.%Y} · {price:.2f}"
            box = QRectF(rect.left() + 2, rect.top() + 1, painter.fontMetrics().horizontalAdvance(text) + 8, 14)
            painter.setBrush(QColor(0, 0, 0, 150))
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(box, Qt.AlignCenter, text)


class DetailWindow(QWidget):
    closed = Signal(str)

    def __init__(self, ctl, symbol):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.ctl, self.symbol = ctl, symbol
        self.alive = True
        frame = make_panel(self, symbol, self.close, pin=(ctl, f"detail:{symbol}"))
        # Die ganze Box scrollt, nicht nur die News: aller Inhalt liegt in einer gemeinsamen Scrollfläche.
        area, body = scroll_area()
        body.setContentsMargins(0, 0, 4, 0)
        body.setSpacing(10)
        frame.addWidget(area)

        names = QVBoxLayout()
        names.setSpacing(2)
        self.company = QLabel()
        self.company.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.company_meta = QLabel()
        self.company_meta.setWordWrap(True)
        self.company_meta.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        names.addWidget(self.company)
        names.addWidget(self.company_meta)
        body.addLayout(names)

        top = QHBoxLayout()
        self.price = QLabel("…")
        self.price.setStyleSheet("font-size: 30px; font-weight: 700;")
        self.day = QLabel()
        top.addWidget(self.price)
        top.addSpacing(8)
        top.addWidget(self.day)
        top.addStretch()
        self.price_note = QLabel()
        price_box = QVBoxLayout()
        price_box.setSpacing(2)
        price_box.addLayout(top)
        price_box.addWidget(self.price_note)
        body.addLayout(price_box)

        # Kursdiagramm mit Zeitraumwahl
        self.chart = PriceChart()
        self.range_change = QLabel()
        body.addWidget(self.range_change)
        body.addWidget(self.chart)
        self.range_key = sd.DEFAULT_RANGE
        self.history_token = 0
        range_row = QHBoxLayout()
        range_row.setSpacing(14)
        range_row.addStretch()
        self.range_buttons = {}
        for key, title, _, _ in sd.HISTORY_RANGES:
            button = QPushButton(title)
            button.setCheckable(True)
            button.setFlat(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setStyleSheet(f"QPushButton {{ background: transparent; border: none; padding: 0; "
                                 f"color: {MUTED}; font-size: 11px; min-height: 0; }} "
                                 f"QPushButton:hover {{ color: {TEXT}; }} "
                                 f"QPushButton:checked {{ color: {ACCENT}; font-weight: 700; }}")
            button.clicked.connect(lambda _checked=False, k=key: self.load_history(k))
            range_row.addWidget(button)
            self.range_buttons[key] = button
        range_row.addStretch()
        body.addLayout(range_row)

        # Position
        self.position_card = position_card = QFrame()
        position_card.setObjectName("plain")
        column = QVBoxLayout(position_card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(6)
        column.addWidget(explain(caption_label("Position"), "position"))
        pl_row = QHBoxLayout()
        self.pl_percent = explain(QLabel(), "gv_prozent")
        self.pl_amount = explain(QLabel(), "gv")
        pl_row.addWidget(self.pl_percent)
        pl_row.addSpacing(8)
        pl_row.addWidget(self.pl_amount, 0, Qt.AlignBottom)
        pl_row.addStretch()
        column.addLayout(pl_row)
        self.position_info = enable_term_links(QLabel())
        self.position_info.setWordWrap(True)
        self.position_info.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        column.addWidget(self.position_info)
        body.addWidget(position_card)

        # Kursziele der Analysten
        self.targets_card = targets_card = QFrame()
        targets_card.setObjectName("plain")
        column = QVBoxLayout(targets_card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(4)
        column.addWidget(caption_label("Kursziel der Analysten"))
        self.target_mean = QLabel("Wird geladen …")
        self.target_mean.setStyleSheet(f"color: {MUTED}; font-size: 17px; font-weight: 700;")
        self.target_upside = QLabel()
        self.target_range = QLabel()
        self.target_range.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        column.addWidget(self.target_mean)
        column.addWidget(self.target_upside)
        column.addWidget(self.target_range)
        body.addWidget(targets_card)

        # Einschätzung der Analysten: Verteilung der Empfehlungen, Gewinnerwartung gegen Ergebnis
        self.consensus_card = consensus_card = QFrame()
        consensus_card.setObjectName("plain")
        column = QVBoxLayout(consensus_card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(6)
        column.addWidget(explain(caption_label("Einschätzung der Analysten"), "empfehlung"))
        self.consensus_status = QLabel("Wird geladen …")
        self.consensus_status.setWordWrap(True)
        self.consensus_status.setStyleSheet(f"color: {MUTED};")
        column.addWidget(self.consensus_status)
        self.consensus_body = QWidget()
        self.consensus_body.setObjectName("clear")
        inner = QVBoxLayout(self.consensus_body)
        inner.setContentsMargins(0, 0, 0, 0)
        inner.setSpacing(5)
        self.rec_bar = QHBoxLayout()
        self.rec_bar.setSpacing(2)
        inner.addLayout(self.rec_bar)
        self.rec_text = QLabel()
        self.rec_text.setStyleSheet("font-size: 13px; font-weight: 600;")
        self.rec_trend = QLabel()
        self.rec_trend.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        self.rec_warning = QLabel()
        self.rec_warning.setWordWrap(True)
        self.rec_warning.setStyleSheet(f"color: {AMBER}; font-size: 12px;")
        for label in (self.rec_text, self.rec_trend, self.rec_warning):
            inner.addWidget(label)
        self.surprise_title = explain(caption_label("Gewinn je Aktie gegen Erwartung"), "gewinnueberraschung")
        inner.addSpacing(6)
        inner.addWidget(self.surprise_title)
        self.surprise_lines = QVBoxLayout()
        self.surprise_lines.setSpacing(2)
        inner.addLayout(self.surprise_lines)
        self.surprise_summary = QLabel()
        self.surprise_summary.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
        inner.addWidget(self.surprise_summary)
        self.consensus_note = QLabel()
        self.consensus_note.setWordWrap(True)
        self.consensus_note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        inner.addSpacing(4)
        inner.addWidget(self.consensus_note)
        self.consensus_body.hide()
        column.addWidget(self.consensus_body)
        body.addWidget(consensus_card)

        # Kennzahlen (Fundamentaldaten)
        self.fundamentals_card = fundamentals_card = QFrame()
        fundamentals_card.setObjectName("plain")
        column = QVBoxLayout(fundamentals_card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(6)
        column.addWidget(explain(caption_label("Kennzahlen"), "kennzahlen"))
        self.fundamentals_status = QLabel("Wird geladen …")
        self.fundamentals_status.setWordWrap(True)
        self.fundamentals_status.setStyleSheet(f"color: {MUTED};")
        column.addWidget(self.fundamentals_status)
        self.fundamentals_body = QVBoxLayout()
        self.fundamentals_body.setSpacing(4)
        column.addLayout(self.fundamentals_body)
        self.fundamentals_note = QLabel()
        self.fundamentals_note.setWordWrap(True)
        self.fundamentals_note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.fundamentals_note.hide()
        column.addWidget(self.fundamentals_note)
        body.addWidget(fundamentals_card)

        # Historie
        history_card = QFrame()
        history_card.setObjectName("plain")
        column = QVBoxLayout(history_card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(6)
        column.addWidget(explain(caption_label("Historie"), "historie"))
        self.history_lines = QVBoxLayout()
        self.history_lines.setSpacing(3)
        column.addLayout(self.history_lines)
        self.history_hint = QLabel()
        self.history_hint.setWordWrap(True)
        self.history_hint.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        column.addWidget(self.history_hint)
        self.all_entries = QPushButton("Alle Einträge")
        self.all_entries.setCursor(Qt.PointingHandCursor)
        self.all_entries.clicked.connect(lambda: open_transactions(ctl, symbol))
        column.addWidget(self.all_entries)
        body.addWidget(history_card)

        # Nächster Termin
        event_card = QFrame()
        event_card.setObjectName("plain")
        column = QVBoxLayout(event_card)
        column.setContentsMargins(16, 14, 16, 14)
        column.setSpacing(3)
        column.addWidget(explain(caption_label("Nächster Termin"), "termin"))
        self.event_title = QLabel("Wird geladen …")
        self.event_title.setStyleSheet(f"color: {ACCENT}; font-size: 17px; font-weight: 700;")
        self.event_when = QLabel()
        self.event_status = QLabel()
        self.event_status.hide()
        when_row = QHBoxLayout()
        when_row.setSpacing(8)
        when_row.addWidget(self.event_when)
        when_row.addWidget(self.event_status)
        when_row.addStretch()
        self.event_note = QLabel()
        self.event_note.setWordWrap(True)
        self.event_note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.event_note.hide()
        self.event_rows = QVBoxLayout()
        self.event_rows.setSpacing(6)
        self.events_failed = False
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.add_event_button = QPushButton("+ Termin")
        self.add_event_button.setCursor(Qt.PointingHandCursor)
        self.add_event_button.clicked.connect(lambda: event_dialog(ctl, symbol))
        self.all_events_button = QPushButton("Alle Termine")
        self.all_events_button.setCursor(Qt.PointingHandCursor)
        self.all_events_button.clicked.connect(lambda: open_calendar(ctl, symbol))
        buttons.addWidget(self.add_event_button)
        buttons.addWidget(self.all_events_button)
        column.addWidget(self.event_title)
        column.addLayout(when_row)
        column.addWidget(self.event_note)
        column.addLayout(self.event_rows)
        column.addSpacing(4)
        column.addLayout(buttons)
        body.addWidget(event_card)

        body.addWidget(caption_label("News"))
        self.news = QVBoxLayout()
        self.news.setSpacing(8)
        self.news.addStretch()
        body.addLayout(self.news)
        body.addStretch()

        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(460, min(760, screen.height() - 20))

        ctl.changed.connect(self.refresh_view)
        ctl.trade_recorded.connect(self.on_trade)
        ctl.ledger_changed.connect(self.refresh_history)
        ctl.calendar_changed.connect(self.refresh_events)
        ctl.fundamentals_changed.connect(self.on_fundamentals)
        ctl.terms_changed.connect(self.on_terms)
        self.fetched_news, self.news_error = NOT_LOADED, None
        self.refresh_history()
        self.refresh_events()
        self.refresh_fundamentals()
        self.fresh_timer = QTimer(self)
        self.fresh_timer.timeout.connect(self.refresh_view)
        self.fresh_timer.start(15_000)
        self.refresh_view()
        ctl.run(self.load, self.show_loaded)
        ctl.run(lambda: sd.fetch_targets(symbol), self.show_targets, lambda exc: self.show_targets(None, exc))
        ctl.run(lambda: sd.fetch_consensus(symbol), self.show_consensus, lambda exc: self.show_consensus(None, exc))
        ctl.load_fundamentals(symbol)
        self.load_history(self.range_key)

    def load_history(self, key):
        """Lädt den Kursverlauf für den Zeitraum; eine spätere Auswahl verwirft die Antwort einer früheren."""
        self.range_key = key
        self.history_token += 1
        token = self.history_token
        for k, button in self.range_buttons.items():
            button.setChecked(k == key)
        self.chart.show_points(None, "Wird geladen …")
        self.range_change.setText("")

        def done(points):
            if self.alive and token == self.history_token:
                self.chart.show_points(points)
                self.update_overlay()
                self.show_range_change(points)

        def fail(exc):
            if self.alive and token == self.history_token:
                self.chart.show_points(None, f"Kursverlauf nicht ladbar: {exc}")

        self.ctl.run(lambda: sd.fetch_history(self.symbol, key), done, fail)

    def update_overlay(self):
        """Marken für Käufe und Verkäufe im gezeigten Zeitraum und der Einstandskurs."""
        points = self.chart.points
        if not points:
            return
        position = self.ctl.positions.get(self.symbol)
        self.chart.set_overlay(history.chart_markers(self.ctl.transactions.get(self.symbol, []), points),
                               position["cost"] if position else None)

    def refresh_history(self):
        """Die Karte „Historie“: die jüngsten Einträge und was die Historie noch nicht enthält."""
        transactions = sorted(self.ctl.transactions.get(self.symbol, []), key=lambda t: (t.day, t.id), reverse=True)
        while self.history_lines.count():
            widget = self.history_lines.takeAt(0).widget()
            if widget:
                widget.deleteLater()
        for tx in transactions[:5]:
            line = QLabel(history.describe(tx))
            line.setWordWrap(True)
            line.setStyleSheet("font-size: 12px;")
            self.history_lines.addWidget(line)
        if len(transactions) > 5:
            more = QLabel(f"… und {len(transactions) - 5} weitere")
            more.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            self.history_lines.addWidget(more)
        summary = history.summarize_history(transactions)
        if not summary:
            self.history_hint.setText("Noch keine Einträge.")
        elif summary.only_opening:
            self.history_hint.setText("Nur Startbestand: Das Kaufdatum ist unbekannt. Echte Käufe und Verkäufe "
                                      "erscheinen hier, sobald du sie einträgst oder eine Anbindung sie liefert.")
        else:
            self.history_hint.setText(f"{summary.count} Einträge seit {summary.first_day:%d.%m.%Y}"
                                      + (f" · Gebühren {summary.fees:.2f}" if summary.fees else ""))
        self.update_overlay()

    def show_range_change(self, points):
        """Zeigt, um wieviel der Kurs im gewählten Zeitraum gestiegen oder gefallen ist."""
        first, last = points[0][1], points[-1][1]
        delta = last - first
        self.range_change.setText(f"{delta / first * 100:+.2f} %")
        self.range_change.setStyleSheet(f"color: {sign_color(delta)}; font-size: 12px; font-weight: 600;")

    def on_trade(self, symbol, kind):
        if symbol == self.symbol:
            flash(self.position_card, GREEN if kind == "buy" else RED)

    def refresh_view(self):
        quote = self.ctl.quotes.get(self.symbol)
        position = self.ctl.positions.get(self.symbol)
        freshness = self.ctl.freshness(self.symbol)
        stale = bool(freshness and freshness["stale"])
        self.price.setStyleSheet("font-size: 30px; font-weight: 700;" + (f" color: {AMBER};" if stale else ""))
        self.price_note.setText(freshness_text(freshness).replace("\n", " · "))
        self.price_note.setStyleSheet(f"color: {AMBER if stale else MUTED}; font-size: 11px;")
        instrument = self.ctl.instruments.get(self.symbol)
        self.company.setVisible(bool(instrument))
        self.company_meta.setVisible(bool(instrument))
        if instrument:
            self.company.setText(instrument["name"])
            isin = f"ISIN {instrument['isin']}" if instrument.get("isin") else ""
            self.company_meta.setText(" · ".join(part for part in (
                instrument.get("exchange"), instrument.get("sector"), instrument.get("industry"),
                instrument.get("country"), isin) if part))
            self.company_meta.setToolTip(f"Stammdaten: {instrument['source']}, Stand "
                                         f"{instrument['fetched_at']:%d.%m.%Y %H:%M}")
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
            info = (f"{position['shares']:g} Stück · {term_link('Einstand', 'einstand')} {position['cost']:.2f} · "
                    f"Wert {quote['price'] * position['shares']:.2f} {cur}")
            if position["realized"]:
                info += f" · {term_link('realisiert', 'realisiert')} {position['realized']:+.2f} {cur}"
            self.position_info.setText(info)
        else:
            self.pl_percent.setText("–")
            self.pl_percent.setStyleSheet(f"font-size: 24px; font-weight: 700; color: {MUTED};")
            self.pl_amount.setText("")
            info = "Keine Position hinterlegt."
            realized = self.ctl.realized.get(self.symbol)
            if realized:
                info += (f" {term_link('Realisiert', 'realisiert')} insgesamt {realized:+.2f} "
                         f"{quote['currency'] if quote else ''}")
            self.position_info.setText(info)

    def load(self):
        events = news = error = None
        try:
            events = sd.fetch_events(self.symbol)
        except Exception as exc:
            error = exc
        try:
            news = sd.fetch_news(self.symbol, count=NEWS_FETCH_COUNT)
        except Exception as exc:
            error = error or exc
        return events, news, error

    def show_targets(self, targets, error=None):
        """Durchschnittliches Kursziel, Zahl der Analysten, höchstes und niedrigstes Ziel; None = keine Daten."""
        if not self.alive:
            return
        self.targets = targets
        if not targets:
            self.target_mean.setText("Nicht ladbar" if error else "Keine Kursziele vorhanden")
            self.target_upside.setText("")
            self.target_range.setText(str(error) if error else "")
            return
        cur = targets["currency"]
        self.target_mean.setText(f"Ø {targets['mean']:.2f} {cur}".strip())
        self.target_mean.setStyleSheet("font-size: 17px; font-weight: 700;")
        quote = self.ctl.quotes.get(self.symbol)
        if quote and quote["price"] and (not cur or cur == quote["currency"]):
            upside = (targets["mean"] / quote["price"] - 1) * 100
            self.target_upside.setText(f"{upside:+.1f} % zum aktuellen Kurs")
            self.target_upside.setStyleSheet(f"color: {sign_color(upside)}; font-size: 12px; font-weight: 600;")
        else:
            self.target_upside.setText("")
        parts = []
        if targets.get("high"):
            parts.append(f"Höchstes {targets['high']:.2f}")
        if targets.get("low"):
            parts.append(f"Niedrigstes {targets['low']:.2f}")
        if targets.get("count"):
            parts.append(f"{targets['count']} Analyst{'en' if targets['count'] != 1 else ''}")
        self.target_range.setText(" · ".join(parts))
        self.targets_card.setToolTip("Schätzungen einzelner Analysten (Quelle: Yahoo Finance), keine Prognose. "
                                     "Wenige Analysten machen den Durchschnitt unsicher.")

    def show_consensus(self, data, error=None):
        """Verteilung der Empfehlungen (ohne Gesamtnote), Trend, Warnung bei wenigen Analysten und die letzten
        Quartale mit Gewinnerwartung und Ergebnis. data None: Yahoo nennt nichts oder der Abruf schlug fehl."""
        if not self.alive:
            return
        self.consensus = data
        if not data:
            reason = str(error) if isinstance(error, ValueError) else f"Nicht ladbar: {error}"
            self.consensus_status.setText(reason if error else "Keine Einschätzungen vorhanden")
            self.consensus_status.show()
            self.consensus_body.hide()
            return
        self.consensus_status.hide()
        self.consensus_body.show()
        clear_layout(self.rec_bar)
        clear_layout(self.surprise_lines)
        now, before = data["recommendations"]["now"], data["recommendations"]["before"]
        has_rec = bool(now)
        for widget in (self.rec_text, self.rec_trend, self.rec_warning):
            widget.setVisible(has_rec)
        if has_rec:
            for number, color in zip(cons.sides(now), (GREEN, "#6b7185", RED)):
                if number:
                    segment = QFrame()
                    segment.setFixedHeight(8)
                    segment.setStyleSheet(f"background: {color}; border-radius: 4px;")
                    self.rec_bar.addWidget(segment, number)
            count = cons.total(now)
            self.rec_text.setText(f"{cons.distribution_text(now)}  ({count} Analyst{'en' if count != 1 else ''})")
            self.rec_trend.setText(cons.trend_text(now, before))
            self.rec_trend.setVisible(bool(self.rec_trend.text()))
            self.rec_warning.setText(cons.caution(count))
            self.rec_warning.setVisible(bool(self.rec_warning.text()))
        surprises = data["surprises"]
        for widget in (self.surprise_title, self.surprise_summary):
            widget.setVisible(bool(surprises))
        for item in surprises:
            line = QLabel(cons.surprise_line(item))
            line.setStyleSheet(f"color: {sign_color(item['surprise']) if item['surprise'] is not None else MUTED}; "
                               "font-size: 12px;")
            self.surprise_lines.addWidget(line)
        self.surprise_summary.setText(cons.beat_summary(surprises))
        self.consensus_note.setText(f"Quelle: Yahoo Finance, Stand {data['fetched_at']:%d.%m.%Y %H:%M}. "
                                    "Schätzungen von Analysten, keine Prognose.")

    def on_fundamentals(self, symbol):
        if symbol == self.symbol:
            self.refresh_fundamentals()

    def refresh_fundamentals(self):
        """Die Karte „Kennzahlen“: gespeicherte Werte gruppiert, darunter Quelle, Stand und Hinweise.
        Fehlt jede Angabe, steht der Grund da; schlug nur die Aktualisierung fehl, bleiben die alten Werte stehen."""
        if not self.alive:
            return
        while self.fundamentals_body.count():
            item = self.fundamentals_body.takeAt(0)
            widget = item.widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
            elif item.layout():
                clear_layout(item.layout())
        entry = self.ctl.fundamentals.get(self.symbol)
        error = self.ctl.fundamentals_errors.get(self.symbol)
        if not entry:
            self.fundamentals_note.hide()
            if error:
                self.fundamentals_status.setText(f"Keine Kennzahlen: {error}")
            else:
                self.fundamentals_status.setText("Wird geladen …")
            self.fundamentals_status.show()
            return
        self.fundamentals_status.hide()
        data = entry["data"]
        for group, lines in fund.groups(data):
            title = QLabel(group.upper())
            title.setStyleSheet(f"color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px; "
                                "margin-top: 6px;")
            self.fundamentals_body.addWidget(title)
            grid = QGridLayout()
            grid.setHorizontalSpacing(10)
            grid.setVerticalSpacing(3)
            grid.setColumnStretch(0, 1)
            for row, (metric, text, tone) in enumerate(lines):
                label = explain(QLabel(metric.label), metric.term)
                label.setStyleSheet(f"color: {MUTED}; font-size: 12px;")
                value = QLabel(text)
                color = {"good": GREEN, "bad": RED}.get(tone, TEXT if text not in ("–", "negativ") else MUTED)
                value.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600;")
                value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                grid.addWidget(label, row, 0)
                grid.addWidget(value, row, 1)
            self.fundamentals_body.addLayout(grid)
        note = fund.age_note(data, entry["fetched_at"])
        if error:
            note += f" Aktualisierung fehlgeschlagen: {error}"
        stale = fund.is_stale(entry["fetched_at"]) or bool(error)
        self.fundamentals_note.setText(note)
        self.fundamentals_note.setStyleSheet(f"color: {AMBER if stale else MUTED}; font-size: 11px;")
        self.fundamentals_note.show()

    def refresh_events(self):
        """Der nächste Termin groß, die folgenden darunter, jeweils mit Status und Quelle (aus dem Kalender)."""
        if not self.alive:
            return
        today = dt.date.today()
        upcoming = evt.upcoming(self.ctl.calendar_events(self.symbol, today), today)
        while self.event_rows.count():
            widget = self.event_rows.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        if not upcoming:
            self.event_title.setText("Termine nicht ladbar" if self.events_failed else "Kein bevorstehender Termin")
            explain(self.event_title, "termin")
            self.event_when.setText("")
            self.event_status.hide()
            self.event_note.hide()
            return
        first = upcoming[0]
        self.event_title.setText(first.event.title)
        explain(self.event_title, KIND_TERMS.get(first.event.kind, "termin"))
        self.event_when.setText(evt.when_text(first.event, today))
        style_status_badge(self.event_status, first.status)
        self.event_status.show()
        note = event_note(first, today)
        self.event_note.setText(note)
        self.event_note.setVisible(bool(note))
        for item in upcoming[1:5]:
            self.event_rows.addWidget(EventRow(self.ctl, item, today))
        if len(upcoming) > 5:
            more = QLabel(f"… und {len(upcoming) - 5} weitere")
            more.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
            self.event_rows.addWidget(more)

    def show_loaded(self, result):
        if not self.alive:
            return
        events, news, error = result
        self.events_failed = events is None
        if events is not None:
            self.ctl.apply_fetched_events(self.symbol, events)
            self.ctl.changed.emit()
        self.refresh_events()
        self.fetched_news, self.news_error = news, error
        if news is not None:
            self.ctl.store_news(self.symbol, news)  # der Feed muss sie nicht noch einmal laden
        self.render_news()

    def render_news(self):
        """Zeigt die geladenen News, aber nur die, deren Titel zur Aktie passt (siehe keywords.filter_news).
        Kommen später Suchbegriffe an, wird neu gezeichnet; aussortierte News sind nicht sichtbar."""
        if not self.alive or self.fetched_news is NOT_LOADED:
            return
        while self.news.count() > 1:  # das letzte Element ist der Abstandshalter
            widget = self.news.takeAt(0).widget()
            if widget:
                widget.setParent(None)  # sofort aus dem Fenster, damit nichts Altes mitgezählt wird
                widget.deleteLater()
        news = self.fetched_news
        if news is not None:
            news = kw.filter_news(news, self.symbol, self.ctl.news_terms(self.symbol))
        if not news:
            note = QLabel("Keine News gefunden." if news is not None else f"News nicht ladbar: {self.news_error}")
            note.setStyleSheet(f"color: {MUTED};")
            self.news.insertWidget(0, note)
        for title, source, published, link in news or []:
            self.news.insertWidget(self.news.count() - 1, NewsCard(title, source, published, link))

    def on_terms(self, symbol):
        if symbol == self.symbol:
            self.render_news()

    def closeEvent(self, event):
        Dock.remove(self)
        self.alive = False
        for signal, slot in ((self.ctl.changed, self.refresh_view), (self.ctl.trade_recorded, self.on_trade),
                             (self.ctl.ledger_changed, self.refresh_history),
                             (self.ctl.calendar_changed, self.refresh_events),
                             (self.ctl.fundamentals_changed, self.on_fundamentals),
                             (self.ctl.terms_changed, self.on_terms)):
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


# ---------- News-Feed ----------

NEWS_WINDOWS = {}
FEED_HOURS = ((24, "24 Std."), (48, "48 Std."), (168, "7 Tage"))
FEED_STEP = 30
TAG_COLORS = {3: AMBER, 2: ACCENT}


def open_news(ctl, open_symbol=None):
    existing = NEWS_WINDOWS.get("window")
    if existing:
        existing.raise_()
        existing.activateWindow()
        return
    window = NewsFeedWindow(ctl, open_symbol)
    window.closed.connect(lambda: NEWS_WINDOWS.pop("window", None))
    NEWS_WINDOWS["window"] = window
    Dock.add(window)
    window.show()


class FeedRow(QFrame):
    """Eine Meldung im Feed: Titel, Kürzel, Quelle, Zeit, Einstufung und ein Hinweis auf einen bekannten Termin."""

    def __init__(self, item):
        super().__init__()
        self.item, self.link = item, item.link
        self.setObjectName("card")
        self.setCursor(Qt.PointingHandCursor)
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 10, 14, 10)
        column.setSpacing(3)
        top = QHBoxLayout()
        top.setSpacing(8)
        self.headline = QLabel(item.title)
        self.headline.setWordWrap(True)
        self.headline.setStyleSheet("font-weight: 600;")
        top.addWidget(self.headline, 1)
        self.badge = QLabel(item.tag or newsfeed.importance_label(item))
        if item.importance in TAG_COLORS and self.badge.text():
            style_pill(self.badge, TAG_COLORS[item.importance], strong=item.importance == 3)
            top.addWidget(self.badge, 0, Qt.AlignTop)
        else:
            self.badge.hide()
        column.addLayout(top)
        when = item.published.astimezone().strftime("%d.%m. %H:%M") if item.published else ""
        self.meta = QLabel(" · ".join(part for part in (", ".join(item.symbols), item.source, when) if part))
        self.meta.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        column.addWidget(self.meta)
        self.event_note = QLabel(item.event_note)
        self.event_note.setWordWrap(True)
        self.event_note.setStyleSheet(f"color: {AMBER}; font-size: 11px;")
        self.event_note.setVisible(bool(item.event_note))
        column.addWidget(self.event_note)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            QDesktopServices.openUrl(QUrl(self.link))


class NewsFeedWindow(QWidget):
    """Ein kurzer News-Feed über mehrere Aktien: standardmäßig nur Positionen, die letzten 48 Stunden, Wichtiges
    zuerst, Dubletten zusammengeführt und Kursgerede ausgeblendet."""
    closed = Signal()

    def __init__(self, ctl, open_symbol=None):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.ctl = ctl
        self.positions_only, self.hours, self.show_minor, self.limit = True, newsfeed.DEFAULT_HOURS, False, FEED_STEP
        body = make_panel(self, "News", self.close, pin=(ctl, "news"))
        controls = QHBoxLayout()
        controls.setSpacing(4)
        self.only_positions = self._toggle("Nur Positionen", True, self.toggle_positions)
        controls.addWidget(self.only_positions)
        self.group = QButtonGroup(self)
        self.hour_buttons = {}
        for hours, title in FEED_HOURS:
            button = QPushButton(title)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setChecked(hours == self.hours)
            button.clicked.connect(lambda _checked=False, h=hours: self.set_hours(h))
            self.group.addButton(button)
            self.hour_buttons[hours] = button
            controls.addWidget(button)
        controls.addStretch()
        self.reload_button = QPushButton("↻")
        self.reload_button.setObjectName("icon")
        self.reload_button.setToolTip("News neu laden")
        self.reload_button.clicked.connect(lambda: self.load(force=True))
        controls.addWidget(self.reload_button)
        body.addLayout(controls)
        options = QHBoxLayout()
        self.minor_button = self._toggle("Auch Unwichtiges", False, self.toggle_minor)
        self.minor_button.setToolTip("Kursgerede und Ratgeber-Listen („Warum die Aktie heute steigt“) sind ausgeblendet")
        options.addWidget(self.minor_button)
        options.addStretch()
        body.addLayout(options)
        self.status = QLabel()
        self.status.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        body.addWidget(self.status)
        area, self.rows = scroll_area()
        self.rows.addStretch()
        body.addWidget(area, 1)
        self.footer = QLabel()
        self.footer.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        self.footer.setWordWrap(True)
        body.addWidget(self.footer)
        self.more_button = QPushButton("Mehr anzeigen")
        self.more_button.clicked.connect(self.show_more)
        body.addWidget(self.more_button)
        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(500, min(780, screen.height() - 20))
        self.update_timer = QTimer(self)
        self.update_timer.setSingleShot(True)
        self.update_timer.setInterval(200)
        self.update_timer.timeout.connect(self.refresh)
        ctl.news_changed.connect(self.update_timer.start)
        ctl.terms_changed.connect(self.update_timer.start)
        ctl.calendar_changed.connect(self.update_timer.start)
        self.load()
        self.refresh()

    def _toggle(self, title, checked, slot):
        button = QPushButton(title)
        button.setObjectName("segment")
        button.setCheckable(True)
        button.setChecked(checked)
        button.clicked.connect(slot)
        return button

    # ----- Bedienung -----

    def load(self, force=False):
        """Lädt die News der Aktien im gewählten Umfang, die fehlen oder veraltet sind."""
        for symbol in self.ctl.feed_symbols(self.positions_only):
            self.ctl.load_news(symbol, force=force)
        self.refresh()

    def toggle_positions(self):
        self.positions_only = self.only_positions.isChecked()
        self.limit = FEED_STEP
        self.load()

    def set_hours(self, hours):
        self.hours, self.limit = hours, FEED_STEP
        self.refresh()

    def toggle_minor(self):
        self.show_minor, self.limit = self.minor_button.isChecked(), FEED_STEP
        self.refresh()

    def show_more(self):
        self.limit += FEED_STEP
        self.refresh()

    # ----- Anzeige -----

    def refresh(self):
        scope = self.ctl.feed_symbols(self.positions_only)
        feed = self.ctl.build_feed(self.positions_only, self.hours, self.limit, self.show_minor)
        while self.rows.count() > 1:
            widget = self.rows.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        for item in feed.items:
            self.rows.insertWidget(self.rows.count() - 1, FeedRow(item))
        loaded = sum(1 for s in scope if (self.ctl.news_cache.get(s) or {}).get("news") is not None)
        failed = [s for s in scope if (self.ctl.news_cache.get(s) or {}).get("error")]
        loading = [s for s in scope if s in self.ctl.news_loading]
        if not scope:
            self.status.setText("Keine Aktien im Umfang. Mit „Nur Positionen“ aus siehst du die ganze Watchlist.")
        elif loading:
            self.status.setText(f"Lade News … {loaded} von {len(scope)} Aktien")
        else:
            text = f"News von {loaded} von {len(scope)} Aktien"
            if failed:
                text += f" · nicht ladbar: {', '.join(failed)}"
            self.status.setText(text)
        if not feed.items:
            note = QLabel("Keine passenden News." if not loading else "Wird geladen …")
            note.setAlignment(Qt.AlignCenter)
            note.setStyleSheet(f"color: {MUTED};")
            self.rows.insertWidget(0, note)
        parts = []
        if feed.minor_hidden:
            parts.append(f"{feed.minor_hidden} Kursgerede ausgeblendet")
        if feed.old_hidden:
            parts.append(f"{feed.old_hidden} älter als {dict(FEED_HOURS)[self.hours]}")
        self.footer.setText(" · ".join(parts))
        self.footer.setVisible(bool(parts))
        self.more_button.setText(f"Mehr anzeigen ({feed.more} weitere)")
        self.more_button.setVisible(feed.more > 0)

    def closeEvent(self, event):
        Dock.remove(self)
        for signal in (self.ctl.news_changed, self.ctl.terms_changed, self.ctl.calendar_changed):
            try:
                signal.disconnect(self.update_timer.start)
            except (RuntimeError, TypeError):
                pass
        self.closed.emit()
        super().closeEvent(event)


# ---------- Termine ----------

STATUS_COLORS = {"confirmed": GREEN, "expected": AMBER, "speculative": "#a78bfa", "occurred": MUTED}
STATUS_TERMS = {"confirmed": "status_bestaetigt", "expected": "status_erwartet", "speculative": "status_spekulativ",
                "occurred": "status_eingetreten"}
# Art eines Termins -> Begriff im Glossar
KIND_TERMS = {**{kind: EVENT_TERMS[label] for label, kind in evt.YAHOO_KINDS.items()}, "guidance": "guidance"}
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
CALENDAR_WINDOWS = {}


def style_status_badge(label, status):
    """Schild mit dem Status eines Termins; spekulative haben zusätzlich einen gestrichelten Rand."""
    color = STATUS_COLORS[status]
    label.setText(evt.STATUS_LABELS[status])
    style_pill(label, color, strong=status == "confirmed")
    if status == "speculative":
        label.setStyleSheet(label.styleSheet() + f" border: 1px dashed {color};")
    explain(label, STATUS_TERMS[status])
    return label


def event_note(item, today):
    """Quelle, Relevanz, Hinweise und abweichende Angaben zu einem Termin in einer Zeile."""
    event = item.event
    parts = ["Quelle: " + ", ".join(item.sources)]
    if event.relevance != 2:
        parts.append(f"Relevanz {evt.RELEVANCE[event.relevance]}")
    if evt.is_overdue(event, today):
        parts.append("Datum verstrichen")
    parts += [f"{source} nennt {text}" for text, source in item.other_dates]
    if event.note:
        parts.append(event.note)
    return " · ".join(parts)


def check_when(text):
    evt.parse_when(text)
    return text


def event_dialog(ctl, symbol=None, event=None):
    """Dialog zum Anlegen eines eigenen Termins (zu symbol oder mit Auswahl der Aktie) oder zum Ändern von event."""
    editing = event is not None
    fields = []
    if not editing and symbol is None:
        fields.append(("Aktie", Choice([(s, s) for s in ctl.symbols], ctl.symbols[0] if ctl.symbols else None)))
    fields += [
        ("Titel", event.title if editing else "", str),
        ("Datum", evt.date_text(event) if editing else "", check_when),
        ("Art", Choice([(label, key) for key, label in evt.KINDS.items()], event.kind if editing else "product")),
        ("Status", Choice([(evt.STATUS_LABELS[st], st) for st in evt.STATUSES], event.status if editing else "expected")),
        ("Relevanz", Choice([("passend zur Art", None)] + [(evt.RELEVANCE[r], r) for r in (3, 2, 1)],
                            event.relevance if editing else None)),
        ("Notiz", event.note if editing else "", str),
    ]

    def apply(values):
        values = list(values)
        target = symbol if (editing or symbol is not None) else values.pop(0)
        title, when, kind, status, relevance, note = values
        if editing:
            ctl.update_event(event.id, kind, title, when, status, relevance, note)
        else:
            ctl.add_event(target, kind, title, when, status, relevance, note)

    who = event.symbol if editing else symbol
    intro = (f"Eigener Termin{f' für {who}' if who else ''}. Datum: 08.10.2026, 10/2026 (Monat) oder 2026 (Jahr). "
             "Der Status sagt, wie verlässlich der Termin ist.")
    return FieldDialog("Termin ändern" if editing else "Termin anlegen", intro, fields, apply,
                       ok_text="Speichern" if editing else "Anlegen", field_width=190).run()


def delete_event_dialog(ctl, event):
    return FieldDialog("Termin löschen", f"„{event.title}“ ({evt.date_text(event)}, {event.symbol}) wirklich löschen?",
                       [], lambda _values: ctl.delete_event(event.id), ok_text="Löschen").run()


class EventRow(QFrame):
    """Ein Termin als Zeile: Titel, Datum, Quelle und Status; eigene Termine lassen sich ändern und löschen."""
    clicked = Signal(str)

    def __init__(self, ctl, item, today, show_symbol=False, editable=False):
        super().__init__()
        self.setObjectName("plain")
        event = item.event
        self.item, self.symbol, self.show_symbol = item, event.symbol, show_symbol
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 8, 8, 8)
        row.setSpacing(8)
        left = QVBoxLayout()
        left.setSpacing(1)
        self.title = QLabel(f"{event.symbol} · {event.title}" if show_symbol else event.title)
        self.title.setStyleSheet("font-weight: 700;")
        self.when = QLabel(evt.when_text(event, today))
        self.meta = QLabel(event_note(item, today))
        self.meta.setWordWrap(True)
        self.meta.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        for label in (self.title, self.when, self.meta):
            left.addWidget(label)
        row.addLayout(left, 1)
        self.badge = style_status_badge(QLabel(), item.status)
        row.addWidget(self.badge, 0, Qt.AlignTop)
        if editable and event.is_manual:
            for text, tip, action in (("✎", "Termin ändern", lambda: event_dialog(ctl, event=event)),
                                      ("✕", "Termin löschen", lambda: delete_event_dialog(ctl, event))):
                button = QPushButton(text)
                button.setObjectName("icon")
                button.setToolTip(tip)
                button.setCursor(Qt.PointingHandCursor)
                button.clicked.connect(lambda _checked=False, a=action: a())
                row.addWidget(button, 0, Qt.AlignTop)
        if show_symbol:
            self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        if self.show_symbol and event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.symbol)


def open_calendar(ctl, symbol=None, open_symbol=None):
    key = symbol or ""
    existing = CALENDAR_WINDOWS.get(key)
    if existing:
        existing.raise_()
        existing.activateWindow()
        return
    window = CalendarWindow(ctl, symbol, open_symbol)
    window.closed.connect(lambda k: CALENDAR_WINDOWS.pop(k, None))
    CALENDAR_WINDOWS[key] = window
    Dock.add(window)
    window.show()


MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
          "November", "Dezember")
CHIPS_PER_DAY = 3


class ElidedLabel(QLabel):
    """Einzeiliges Label, das zu langen Text mit „…“ kürzt, statt das Layout aufzuweiten."""

    def __init__(self, text=""):
        super().__init__()
        self.full_text = text
        self.setMinimumWidth(10)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
        self._elide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def _elide(self):
        self.setText(self.fontMetrics().elidedText(self.full_text, Qt.ElideRight, max(self.width() - 12, 10)))


def style_chip(label, status):
    """Termin im Tagesfeld: Farbe und Rand folgen dem Status; spekulative sind gestrichelt, eingetretene blass."""
    color = STATUS_COLORS[status]
    c = QColor(color)
    border = "1px dashed" if status == "speculative" else "1px solid"
    label.setStyleSheet(f"background: rgba({c.red()},{c.green()},{c.blue()},{26 if status == 'occurred' else 46}); "
                        f"color: {color}; border: {border} rgba({c.red()},{c.green()},{c.blue()},150); "
                        "border-radius: 5px; padding: 0px 3px; font-size: 11px; font-weight: 600;")
    label.setFixedHeight(18)
    return label


class DayCell(QFrame):
    """Ein Tag im Monatsraster mit Tageszahl und seinen Terminen als Chips."""
    clicked = Signal(object)

    def __init__(self, day, items, in_month, is_today, selected, show_symbol):
        super().__init__()
        self.day, self.items = day, items
        self.setObjectName("daycell")
        self.setMinimumHeight(98)
        self.setCursor(Qt.PointingHandCursor)
        border = ACCENT if selected else (BG if not is_today else "#3a4157")
        self.setStyleSheet(f"QFrame#daycell {{ background: {SURFACE if in_month else BG}; border-radius: 8px; "
                           f"border: {2 if selected else 1}px solid {border}; }}")
        column = QVBoxLayout(self)
        column.setContentsMargins(5, 3, 5, 4)
        column.setSpacing(3)
        self.number = QLabel(str(day.day))
        weight = "700" if is_today else "500"
        color = ACCENT if is_today else (TEXT if in_month else "#5b6178")
        self.number.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: {weight};")
        column.addWidget(self.number)
        self.chips = []
        for item in items[:CHIPS_PER_DAY]:
            event = item.event
            chip = ElidedLabel(f"{event.symbol} · {event.title}" if show_symbol else event.title)
            style_chip(chip, item.status)
            chip.setToolTip(f"{event.symbol}: {event.title} ({STATUS_LABEL_OF[item.status]})")
            column.addWidget(chip)
            self.chips.append(chip)
        self.more = None
        if len(items) > CHIPS_PER_DAY:
            self.more = QLabel(f"+ {len(items) - CHIPS_PER_DAY} weitere")
            self.more.setStyleSheet(f"color: {MUTED}; font-size: 10px;")
            column.addWidget(self.more)
        column.addStretch()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit(self.day)


STATUS_LABEL_OF = evt.STATUS_LABELS


class CalendarWindow(QWidget):
    """Monatskalender mit den Terminen (aller Aktien der Watchlist oder einer Aktie) in den Tagesfeldern.
    Ein Klick auf einen Tag zeigt darunter dessen Termine mit Quelle und Status."""
    closed = Signal(str)

    def __init__(self, ctl, symbol=None, open_symbol=None):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.ctl, self.symbol, self.open_symbol = ctl, symbol, open_symbol
        today = dt.date.today()
        self.positions_only = False
        self.month = today.replace(day=1)
        self.selected = today
        self.cells = {}
        body = make_panel(self, "Termine" if symbol is None else f"{symbol}: Termine", self.close,
                          pin=(ctl, "calendar" if symbol is None else f"calendar:{symbol}"))
        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.prev_button = QPushButton("‹")
        self.prev_button.setObjectName("icon")
        self.prev_button.setToolTip("Voriger Monat")
        self.prev_button.setStyleSheet("font-size: 20px;")
        self.prev_button.clicked.connect(lambda: self.shift_month(-1))
        self.next_button = QPushButton("›")
        self.next_button.setObjectName("icon")
        self.next_button.setToolTip("Nächster Monat")
        self.next_button.setStyleSheet("font-size: 20px;")
        self.next_button.clicked.connect(lambda: self.shift_month(1))
        self.month_label = QLabel()
        self.month_label.setMinimumWidth(150)
        self.month_label.setAlignment(Qt.AlignCenter)
        self.month_label.setStyleSheet("font-size: 16px; font-weight: 700;")
        self.today_button = QPushButton("Heute")
        self.today_button.clicked.connect(self.go_today)
        for widget in (self.prev_button, self.month_label, self.next_button, self.today_button):
            controls.addWidget(widget)
        controls.addStretch()
        self.only_positions = QPushButton("Nur Positionen")
        self.only_positions.setObjectName("segment")
        self.only_positions.setCheckable(True)
        self.only_positions.clicked.connect(self.toggle_positions)
        self.only_positions.setVisible(symbol is None)
        controls.addWidget(self.only_positions)
        self.add_button = QPushButton("+ Termin")
        self.add_button.clicked.connect(lambda: event_dialog(ctl, symbol))
        controls.addWidget(self.add_button)
        body.addLayout(controls)
        self.legend = enable_term_links(QLabel(" · ".join(
            term_link(evt.STATUS_LABELS[st], STATUS_TERMS[st]) for st in evt.STATUSES)))
        self.legend.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        body.addWidget(self.legend)
        self.grid_host = QWidget()
        self.grid_host.setObjectName("clear")
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(4)
        body.addWidget(self.grid_host, 3)
        area, self.detail = scroll_area()
        self.detail.addStretch()
        body.addWidget(area, 2)
        screen = QApplication.primaryScreen().availableGeometry()
        self.setFixedSize(min(1020, screen.width() - 20), min(900, screen.height() - 20))
        self.update_timer = QTimer(self)
        self.update_timer.setSingleShot(True)
        self.update_timer.setInterval(150)
        self.update_timer.timeout.connect(self.refresh)
        ctl.calendar_changed.connect(self.update_timer.start)
        self.refresh()

    # ----- Bedienung -----

    def shift_month(self, step):
        index = self.month.year * 12 + self.month.month - 1 + step
        self.month = dt.date(index // 12, index % 12 + 1, 1)
        self.refresh()

    def go_today(self):
        today = dt.date.today()
        self.month, self.selected = today.replace(day=1), today
        self.refresh()

    def select(self, day):
        self.selected = day
        if day.replace(day=1) != self.month:
            self.month = day.replace(day=1)
        self.refresh()

    def toggle_positions(self):
        self.positions_only = self.only_positions.isChecked()
        self.refresh()

    # ----- Aufbau -----

    def _events(self, today):
        return [m for m in self.ctl.calendar_events(self.symbol, today)
                if not self.positions_only or m.event.symbol in self.ctl.positions]

    def month_days(self):
        """Alle Tage des Rasters: volle Wochen von Montag bis Sonntag um den Monat."""
        first = self.month
        start = first - dt.timedelta(days=first.weekday())
        next_month = (first.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        weeks = ((next_month - dt.timedelta(days=1)) - start).days // 7 + 1
        return [start + dt.timedelta(days=i) for i in range(weeks * 7)]

    @staticmethod
    def _clear(layout, keep_last=False):
        while layout.count() > (1 if keep_last else 0):
            widget = layout.takeAt(0).widget()
            if widget:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

    def refresh(self):
        today = dt.date.today()
        merged = self._events(today)
        exact = [m for m in merged if m.event.precision == "day"]
        by_day = {}
        for item in sorted(exact, key=evt.sort_key):
            by_day.setdefault(item.event.day, []).append(item)
        days = self.month_days()
        self.month_label.setText(f"{MONTHS[self.month.month - 1]} {self.month.year}")
        self._clear(self.grid)
        self.cells = {}
        for column, name in enumerate(WEEKDAYS):
            label = QLabel(name)
            label.setAlignment(Qt.AlignCenter)
            label.setStyleSheet(f"color: {MUTED}; font-size: 11px; font-weight: 700;")
            self.grid.addWidget(label, 0, column)
        for index, day in enumerate(days):
            cell = DayCell(day, by_day.get(day, []), day.month == self.month.month, day == today,
                           day == self.selected, self.symbol is None)
            cell.clicked.connect(self.select)
            self.grid.addWidget(cell, 1 + index // 7, index % 7)
            self.cells[day] = cell
        for column in range(7):
            self.grid.setColumnStretch(column, 1)
        for row in range(1, 1 + len(days) // 7):
            self.grid.setRowStretch(row, 1)
        self._fill_detail(today, by_day, merged)

    def _add(self, widget):
        self.detail.insertWidget(self.detail.count() - 1, widget)

    def _header(self, text, key=None):
        label = QLabel(text.upper())
        label.setStyleSheet(f"color: {MUTED}; font-size: 10px; font-weight: 700; letter-spacing: 1px;")
        self._add(explain(label, key) if key else label)

    def _fill_detail(self, today, by_day, merged):
        self._clear(self.detail, keep_last=True)
        day = self.selected
        self._header(f"{WEEKDAYS[day.weekday()]} {day:%d.%m.%Y} · {evt.relative_day(day, today)}")
        chosen = by_day.get(day, [])
        for item in chosen:
            self._add(self._row(item, today))
        if not chosen:
            note = QLabel("Keine Termine an diesem Tag. Mit „+ Termin“ legst du einen eigenen an.")
            note.setStyleSheet(f"color: {MUTED};")
            note.setWordWrap(True)
            self._add(note)
        vague = [m for m in merged if m.event.precision != "day" and m.event.end >= self.month
                 and m.event.day <= (self.month.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
                 - dt.timedelta(days=1)]
        if vague:
            self._header("Ohne genauen Tag in diesem Monat", "ungenau")
            for item in vague:
                self._add(self._row(item, today))

    def _row(self, item, today):
        row = EventRow(self.ctl, item, today, show_symbol=self.symbol is None, editable=True)
        if self.open_symbol:
            row.clicked.connect(self.open_symbol)
        return row

    def closeEvent(self, event):
        Dock.remove(self)
        try:
            self.ctl.calendar_changed.disconnect(self.update_timer.start)
        except (RuntimeError, TypeError):
            pass
        self.closed.emit(self.symbol or "")
        super().closeEvent(event)


# ---------- Portfolio-Übersicht ----------

PORTFOLIO_WINDOWS = {}
_BASE_PALETTE = [ACCENT, GREEN, AMBER, "#a78bfa", "#22d3ee", "#fb923c", "#f472b6", "#94a3b8"]
PALETTE = _BASE_PALETTE + [QColor(c).lighter(145).name() for c in _BASE_PALETTE]  # 16 unterscheidbare Farben
ALLOCATION_ITEMS = len(PALETTE)  # so viele Einträge zeigt die Aufteilung einzeln; erst darüber steht "Übrige"
ALLOCATIONS = (("position", "Position"), ("sector", "Branche"), ("country", "Land"), ("currency", "Währung"))
SYMBOL_OF_BASE = {"EUR": "€", "USD": "$"}


def money(value):
    return f"{value:,.2f} {SYMBOL_OF_BASE.get(fx_module.BASE, fx_module.BASE)}"


def signed_number(value):
    """Mit Vorzeichen und zwei Nachkommastellen; was auf 0,00 rundet, zeigt kein Minus ("-0.00")."""
    return f"{0.0 if abs(value) < 0.005 else value:+,.2f}"


def signed_money(value):
    return f"{signed_number(value)} {SYMBOL_OF_BASE.get(fx_module.BASE, fx_module.BASE)}"


def signed_percent(value):
    return "–" if value is None else f"{0.0 if abs(value) < 0.005 else value:+.2f} %"


def open_portfolio(ctl, above=None):
    """Öffnet das Portfolio; mit above (dem Hauptfenster) sitzt es über diesem, sonst links daneben."""
    existing = PORTFOLIO_WINDOWS.get("window")
    if existing:
        if above is not None and existing.dock_above is not above:
            existing.dock_above = above
            existing.fit_height()
        existing.raise_()
        existing.activateWindow()
        return
    window = PortfolioWindow(ctl)
    window.dock_above = above
    window.closed.connect(lambda: PORTFOLIO_WINDOWS.pop("window", None))
    PORTFOLIO_WINDOWS["window"] = window
    Dock.add(window)
    window.show()


class DonutChart(QWidget):
    """Ringdiagramm; slices ist [(Name, Wert, Prozent)] und wird in der Reihenfolge der PALETTE gefärbt."""

    def __init__(self):
        super().__init__()
        self.slices = []
        self.setFixedSize(150, 150)

    def set_slices(self, slices):
        self.slices = slices
        self.update()

    @staticmethod
    def color(index):
        return PALETTE[index % len(PALETTE)]

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        ring = QRectF(self.rect()).adjusted(14, 14, -14, -14)
        total = sum(value for _, value, _ in self.slices)
        if total <= 0:
            painter.setPen(QPen(QColor(SURFACE2), 18))
            painter.drawEllipse(ring)
            return
        gap = 2.0 if len(self.slices) > 1 else 0.0  # Grad Lücke zwischen den Teilen
        start = 90.0
        for index, (_, value, _) in enumerate(self.slices):
            span = value / total * 360.0
            painter.setPen(QPen(QColor(self.color(index)), 18, Qt.SolidLine, Qt.FlatCap))
            painter.drawArc(ring, round(start * 16), round(-max(span - gap, 0.5) * 16))
            start -= span


class StatTile(QFrame):
    """Kennzahl mit Überschrift, großem Wert und einer kleinen Zusatzzeile."""

    def __init__(self, caption, term=None):
        super().__init__()
        self.setObjectName("plain")
        column = QVBoxLayout(self)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(3)
        label = self.caption = caption_label(caption)
        label.setWordWrap(True)  # ein langer Titel bricht um, statt die Kachel aus der Box zu schieben
        column.addWidget(explain(label, term) if term else label)
        self.value = QLabel("–")
        self.value.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # die Schrift passt sich der Breite an
        self.color = None
        self.size = self.VALUE_SIZE
        self._style_value()
        self.sub = enable_term_links(QLabel())  # Zusatzzeilen enthalten erklärbare Wörter (Rich Text, Zeilen mit <br>)
        self.sub.setWordWrap(True)
        self.sub.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        column.addWidget(self.value)
        column.addWidget(self.sub)
        column.addStretch()

    VALUE_SIZE, MIN_VALUE_SIZE = 17, 10  # Schriftgröße des Werts in Pixeln, von groß nach klein

    def _style_value(self):
        self.value.setStyleSheet(f"font-size: {self.size}px; font-weight: 700;"
                                 + (f" color: {self.color};" if self.color else ""))

    def _fit_value(self):
        """Wählt die größte Schrift, in der der Wert in die Kachel passt."""
        available = self.width() - 28  # Ränder links und rechts
        font = QFont(self.value.font())
        font.setBold(True)
        size = self.VALUE_SIZE
        while size > self.MIN_VALUE_SIZE:
            font.setPixelSize(size)
            if QFontMetrics(font).horizontalAdvance(self.value.text()) <= available:
                break
            size -= 1
        if size != self.size:
            self.size = size
            self._style_value()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_value()

    def set(self, value, color=None, sub=""):
        self.value.setText(value)
        self.color = color
        self._style_value()
        self._fit_value()
        self.sub.setText(sub)
        self.sub.setVisible(bool(sub))


class PerformanceChart(QWidget):
    """Wert (Fläche) und investiertes Kapital (gestrichelt) des Portfolios über der Zeit;
    points = [history.PerformancePoint]."""

    def __init__(self):
        super().__init__()
        self.setFixedHeight(150)
        self.setMouseTracking(True)
        self.points, self.hover = [], None

    def show_series(self, points):
        self.points, self.hover = list(points), None
        self.update()

    def _plot_rect(self):
        return self.rect().adjusted(4, 6, -52, -18)  # rechts Platz für die Beträge, unten für die Daten

    def _x(self, index):
        rect = self._plot_rect()
        return rect.left() + rect.width() * index / max(len(self.points) - 1, 1)

    def mouseMoveEvent(self, event):
        if len(self.points) >= 2:
            rect = self._plot_rect()
            share = (event.position().x() - rect.left()) / max(rect.width(), 1)
            self.hover = min(max(round(share * (len(self.points) - 1)), 0), len(self.points) - 1)
            self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        if len(self.points) < 2:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignCenter,
                             "Noch kein Verlauf" if not self.points else "Erst ein Tag, noch kein Verlauf")
            return
        rect = self._plot_rect()
        values = [p.value for p in self.points]
        invested = [p.invested for p in self.points]
        low, high = min(values + invested), max(values + invested)
        pad = ((high - low) or max(high, 1.0) * 0.1) * 0.08
        low, high = low - pad, high + pad
        span = high - low

        def y(amount):
            return rect.bottom() - rect.height() * (amount - low) / span

        guide = QColor(MUTED)
        guide.setAlpha(60)
        small = painter.font()
        small.setPixelSize(10)
        painter.setFont(small)
        for step in range(4):
            line_y = rect.bottom() - rect.height() * step / 3
            painter.setPen(QPen(guide, 1, Qt.DotLine))
            painter.drawLine(QPointF(rect.left(), line_y), QPointF(rect.right(), line_y))
            painter.setPen(QColor(MUTED))
            painter.drawText(QRectF(rect.right() + 4, line_y - 7, 48, 14), Qt.AlignLeft | Qt.AlignVCenter,
                             f"{low + span * step / 3:,.0f}")
        for index, align in ((0, Qt.AlignLeft), (len(self.points) - 1, Qt.AlignRight)):
            box = QRectF(rect.left() if index == 0 else rect.right() - 80, rect.bottom() + 3, 80, 12)
            painter.drawText(box, align | Qt.AlignVCenter, f"{self.points[index].day:%d.%m.%y}")

        color = QColor(sign_color(self.points[-1].result))
        path = QPainterPath(QPointF(self._x(0), y(values[0])))
        for index in range(1, len(values)):
            path.lineTo(QPointF(self._x(index), y(values[index])))
        fill = QPainterPath(path)
        fill.lineTo(rect.right(), rect.bottom())
        fill.lineTo(rect.left(), rect.bottom())
        fill.closeSubpath()
        shade = QColor(color)
        shade.setAlpha(36)
        painter.fillPath(fill, shade)
        cost = QPainterPath(QPointF(self._x(0), y(invested[0])))
        for index in range(1, len(invested)):
            cost.lineTo(QPointF(self._x(index), y(invested[index])))
        painter.setPen(QPen(QColor(MUTED), 1.4, Qt.DashLine))
        painter.drawPath(cost)
        painter.setPen(QPen(color, 1.8))
        painter.drawPath(path)
        if self.hover is not None:
            point = self.points[self.hover]
            x = self._x(self.hover)
            painter.setPen(QPen(QColor(MUTED), 1, Qt.DashLine))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setBrush(color)
            painter.setPen(Qt.NoPen)
            painter.drawEllipse(QPointF(x, y(point.value)), 3.5, 3.5)
            text = (f"{point.day:%d.%m.%Y} · Wert {point.value:,.2f} · investiert {point.invested:,.2f} · "
                    f"Ergebnis {signed_number(point.result)}")
            box = QRectF(rect.left() + 2, rect.top() + 1, painter.fontMetrics().horizontalAdvance(text) + 8, 14)
            painter.setBrush(QColor(0, 0, 0, 170))
            painter.drawRoundedRect(box, 3, 3)
            painter.setPen(QColor("#ffffff"))
            painter.drawText(box, Qt.AlignCenter, text)


class PortfolioWindow(QWidget):
    """Gesamtwert, Ergebnis (unrealisiert, realisiert, Währungseffekt) und Aufteilung. Die einzelnen Positionen
    stehen in der Watchlist."""
    closed = Signal()

    WIDTH = 700  # solange es nicht über der Watchlist sitzt; dort gilt deren Breite
    LEGEND_SPLIT = 5  # ab so vielen Einträgen steht die Legende in zwei Spalten

    def __init__(self, ctl):
        super().__init__()
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.ctl = ctl
        self.dimension = "position"
        self.summary = None
        self.dock_above = None
        self.desired_height = 600
        self.base_buttons = {}
        switch = QWidget()
        switch_row = QHBoxLayout(switch)
        switch_row.setContentsMargins(0, 0, 4, 0)
        switch_row.setSpacing(2)
        self.base_group = QButtonGroup(self)
        for code in BASES:
            button = QPushButton(code)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setChecked(code == fx_module.BASE)
            button.setCursor(Qt.PointingHandCursor)
            button.setToolTip(f"Alles in {code} rechnen")
            button.clicked.connect(lambda _checked=False, c=code: ctl.set_base(c))
            self.base_group.addButton(button)
            self.base_buttons[code] = button
            switch_row.addWidget(button)
        body = make_panel(self, "Portfolio", self.close, extras=[switch], pin=(ctl, "portfolio"))
        ctl.base_changed.connect(self.on_base_changed)
        self.area, content = scroll_area()
        body.addWidget(self.area, 1)

        # Vier gleich breite Kennzahlen ohne Zusatzzeilen in einer Reihe
        top = QHBoxLayout()
        top.setSpacing(8)
        self.total_tile = StatTile(f"Gesamtwert in {fx_module.BASE}", "gesamtwert")
        self.total_caption = self.total_tile.caption
        self.total = self.total_tile.value
        self.invested = StatTile("Investiert", "investiert")
        self.cash = StatTile("Guthaben", "guthaben")
        self.result = StatTile("Gesamtgewinn", "gesamtergebnis")
        for tile in (self.total_tile, self.invested, self.cash, self.result):
            top.addWidget(tile, 1)
        content.addLayout(top)

        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setStyleSheet(f"color: {AMBER}; font-size: 11px;")
        self.notes.hide()
        content.addWidget(self.notes)

        performance_card = QFrame()
        performance_card.setObjectName("plain")
        column = QVBoxLayout(performance_card)
        column.setContentsMargins(14, 12, 14, 12)
        column.setSpacing(6)
        head = QHBoxLayout()
        head.addWidget(explain(caption_label("Verlauf"), "verlauf"))
        head.addStretch()
        self.performance_result = QLabel()
        head.addWidget(self.performance_result)
        column.addLayout(head)
        self.performance_chart = PerformanceChart()
        column.addWidget(self.performance_chart)
        self.performance_note = QLabel()
        self.performance_note.setWordWrap(True)
        self.performance_note.setStyleSheet(f"color: {MUTED}; font-size: 11px;")
        column.addWidget(self.performance_note)
        content.addWidget(performance_card)

        allocation_card = QFrame()
        allocation_card.setObjectName("plain")
        column = QVBoxLayout(allocation_card)
        column.setContentsMargins(14, 12, 14, 14)
        column.setSpacing(8)
        column.addWidget(explain(caption_label("Aufteilung"), "aufteilung"))
        segments = QHBoxLayout()
        segments.setSpacing(4)
        self.segment_group = QButtonGroup(self)
        self.segments = {}
        for key, title in ALLOCATIONS:
            button = QPushButton(title)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setChecked(key == self.dimension)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _checked=False, k=key: self.show_allocation(k))
            self.segment_group.addButton(button)
            self.segments[key] = button
            segments.addWidget(button)
        segments.addStretch()
        column.addLayout(segments)
        chart_row = QHBoxLayout()
        chart_row.setSpacing(16)
        self.donut = DonutChart()
        chart_row.addWidget(self.donut, 0, Qt.AlignTop)
        self.legend = QGridLayout()
        self.legend.setHorizontalSpacing(24)
        self.legend.setVerticalSpacing(5)
        chart_row.addLayout(self.legend, 1)
        column.addLayout(chart_row)
        content.addWidget(allocation_card)

        content.addStretch()

        screen = QApplication.primaryScreen().availableGeometry()
        self.screen_height = screen.height() - 20
        self.setFixedWidth(self.WIDTH)

        # Viele Kursmeldungen kurz hintereinander (eine je Aktie) ergeben nur eine Aktualisierung.
        self.update_timer = QTimer(self)
        self.update_timer.setSingleShot(True)
        self.update_timer.setInterval(200)
        self.update_timer.timeout.connect(self.refresh)
        ctl.changed.connect(self.update_timer.start)
        ctl.history_changed.connect(self.update_timer.start)
        self.refresh()

    def refresh_performance(self):
        """Verlauf von Wert und Investiert; die Zeile oben rechts zeigt das Ergebnis bis heute."""
        series = self.ctl.performance()
        self.performance_chart.show_series(series.points)
        notes = []
        if series.points:
            last = series.points[-1]
            self.performance_result.setText(f"{signed_money(last.result)} · {signed_percent(last.return_pct)}")
            self.performance_result.setStyleSheet(f"color: {sign_color(last.result)}; font-size: 12px; font-weight: 600;")
        else:
            self.performance_result.setText("")
        entries = [t for symbol in self.ctl.symbols for t in self.ctl.transactions.get(symbol, [])]
        summary = history.summarize_history(entries)
        if summary and summary.only_opening:
            notes.append(f"Der Verlauf beginnt am {summary.first_day:%d.%m.%Y}, dem Tag der Eintragung: Frühere Käufe "
                         "sind unbekannt. Er wächst mit neuen Käufen und Verkäufen und mit einer Anbindung wie eToro.")
        elif summary:
            notes.append(f"Verlauf ab {summary.first_day:%d.%m.%Y}, dem ersten Eintrag.")
        if series.missing:
            notes.append("Ohne Kurse oder Wechselkurse im Verlauf: " + ", ".join(series.missing))
        self.performance_note.setText("\n".join(notes))
        self.performance_note.setVisible(bool(notes))

    def on_base_changed(self, code):
        self.base_buttons[code].setChecked(True)
        self.refresh()

    def refresh(self):
        summary = self.ctl.portfolio_summary()
        self.summary = summary
        self.total_caption.setText(f"Gesamtwert in {fx_module.BASE}".upper())
        self.base_buttons[fx_module.BASE].setChecked(True)
        self.total_tile.set(money(summary.total_value))
        self.invested.set(money(summary.invested))
        self.cash.set(money(summary.cash or 0))
        self.result.set(signed_money(summary.total_result), sign_color(summary.total_result))
        self.notes.setText("\n".join("⚠ " + warning for warning in summary.warnings))
        self.notes.setVisible(bool(summary.warnings))
        self.refresh_performance()
        self.show_allocation(self.dimension)

    # Fensterrahmen um den Inhalt: Kopfzeile, Ränder des Panels, Rahmen und Schattenrand
    CHROME = 110

    def update_desired_height(self):
        # Umbrechende Texte brauchen mehr Höhe als die sizeHint sagt, deshalb die Höhe für die echte Breite
        width = self.width() - 62  # Panel, Rahmen, Schattenrand und Scrollleiste
        content = self.area.widget().layout().totalHeightForWidth(width)
        self.desired_height = min(content + self.CHROME, self.screen_height)

    def fit_height(self):
        """Das Fenster ist so hoch wie sein Inhalt, höchstens so hoch wie der Bildschirm; das Dock kürzt es
        weiter, wenn es über einem anderen Fenster sitzt."""
        self.update_desired_height()
        if not Dock.fits_above(self):
            self.setFixedHeight(self.desired_height)
        Dock.arrange()

    def show_allocation(self, dimension):
        self.dimension = dimension
        self.segments[dimension].setChecked(True)
        slices = portfolio.allocation(self.summary.holdings, dimension, ALLOCATION_ITEMS) if self.summary else []
        self.donut.set_slices(slices)
        while self.legend.count():
            item = self.legend.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for index in range(self.legend.rowCount()):
            self.legend.setRowStretch(index, 0)
        self.legend.setColumnStretch(0, 1)
        self.legend.setColumnStretch(1, 1)
        columns = 2 if len(slices) > self.LEGEND_SPLIT else 1
        per_column = -(-len(slices) // columns) or 1
        for index, (label, value, percent) in enumerate(slices):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 0, 0, 0)
            line.setSpacing(8)
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background: {DonutChart.color(index)}; border-radius: 5px;")
            name = QLabel(label)
            name.setToolTip(money(value))
            amount = QLabel(f"{percent:.1f} %")
            amount.setStyleSheet(f"color: {MUTED};")
            line.addWidget(dot)
            line.addWidget(name, 1)
            line.addWidget(amount)
            self.legend.addWidget(row, index % per_column, index // per_column)
        if not slices:
            note = QLabel("Keine Daten")
            note.setStyleSheet(f"color: {MUTED};")
            self.legend.addWidget(note, 0, 0)
        self.legend.setRowStretch(max(per_column if slices else 1, 1), 1)
        self.fit_height()  # die Legende hat jetzt vielleicht mehr oder weniger Zeilen

    def closeEvent(self, event):
        Dock.remove(self)
        for signal in (self.ctl.changed, self.ctl.history_changed):
            try:
                signal.disconnect(self.update_timer.start)
            except (RuntimeError, TypeError):
                pass
        try:
            self.ctl.base_changed.disconnect(self.on_base_changed)
        except (RuntimeError, TypeError):
            pass
        self.closed.emit()
        super().closeEvent(event)


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


def restore_pinned(ctl, main):
    """Öffnet alle angehefteten Boxen, die gerade nicht offen sind. Die Positionen sind nie angeheftet, sie öffnen immer."""
    for key in ctl.pinned:
        kind, _, symbol = key.partition(":")
        if kind == "portfolio":
            open_portfolio(ctl, above=main)
        elif kind == "watchlist":
            main.open_watchlist()
        elif kind == "news":
            open_news(ctl, open_symbol=main.open_detail)
        elif kind == "calendar" and (not symbol or symbol in ctl.symbols):
            open_calendar(ctl, symbol or None, open_symbol=main.open_detail)
        elif symbol in ctl.symbols and kind == "detail":
            main.open_detail(symbol)
        elif symbol in ctl.symbols and kind == "tx":
            open_transactions(ctl, symbol)


def widget_is_open(main):
    return main.isVisible() or any(window.isVisible() for window in Dock.windows)


def open_widget(ctl, main):
    """Öffnet die Positionen, die immer dabei sind, und dazu die angehefteten Boxen; mehr nicht."""
    main.show_docked()
    restore_pinned(ctl, main)


def close_widget(main):
    """Schließt alle offenen Fenster des Widgets, angeheftete wie nicht angeheftete. Angeheftet bleibt angeheftet."""
    for window in list(Dock.windows):
        if window is not main:
            window.close()
    main.hide_docked()


def toggle_widget(ctl, main):
    """Klick aufs Symbol im Infobereich: ist etwas offen, wird alles geschlossen, sonst öffnen die angehefteten Boxen."""
    if widget_is_open(main):
        close_widget(main)
    else:
        open_widget(ctl, main)


def close_all_windows(ctl):
    """Schließt beim Beenden alle Fenster geordnet, sonst räumt Python sie in zufälliger Reihenfolge ab."""
    for window in list(Dock.windows):
        window.close()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)  # löscht die geschlossenen Fenster jetzt
    ctl.shutdown()


def sync_etoro_now(ctl, notify):
    """Gleicht mit eToro ab und meldet das Ergebnis über notify(Titel, Text)."""
    if "etoro" not in ctl.sources:
        return notify("eToro", "Noch nicht verbunden. Im Menü „eToro verbinden …“ wählen.")

    def done(plan):
        unmapped = ", ".join(sorted(plan.unmapped))
        notify("eToro abgeglichen", f"{len(plan.new)} neue Transaktionen, {plan.duplicates} schon vorhanden, "
               f"{len(plan.rejected)} abgelehnt." + (f" Kürzel unbekannt: {unmapped}." if unmapped else ""))
    ctl.sync_etoro(done, lambda exc: notify("eToro-Abgleich fehlgeschlagen", str(exc)))


def connect_etoro_dialog(ctl, notify):
    """Fragt API-Key und User-Key ab (api-portal.etoro.com, Leserecht genügt), speichert sie und gleicht ab."""
    def save(values):
        try:
            ctl.connect_etoro(*values)
        except OSError as exc:
            raise ValueError(f"Speichern nicht möglich: {exc}")
    dialog = FieldDialog("eToro verbinden",
                         "Schlüssel aus api-portal.etoro.com (Einstellungen → Trading → API-Schlüssel). "
                         "Ein Schlüssel mit Leserecht genügt; das Widget handelt nicht. Die Schlüssel liegen "
                         "als Klartext in etoro.json im Datenordner.",
                         [("API-Key", "", str), ("User-Key", "", str)], save, ok_text="Verbinden")
    if dialog.run():
        sync_etoro_now(ctl, notify)


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
    tray_menu.addAction("Öffnen", lambda: open_widget(ctl, window))
    tray_menu.addAction("Alle Fenster schließen", lambda: close_widget(window))
    tray_menu.addAction("Portfolio", lambda: open_portfolio(ctl, above=window))
    tray_menu.addAction("Termine", lambda: open_calendar(ctl, open_symbol=window.open_detail))
    tray_menu.addAction("News", lambda: open_news(ctl, open_symbol=window.open_detail))
    tray = QSystemTrayIcon(icon, app)
    notify = lambda title, text: tray.showMessage(title, text, QSystemTrayIcon.Information, 8000)
    tray_menu.addSeparator()
    tray_menu.addAction("eToro verbinden …", lambda: connect_etoro_dialog(ctl, notify))
    tray_menu.addAction("eToro abgleichen", lambda: sync_etoro_now(ctl, notify))
    tray_menu.addSeparator()
    tray_menu.addAction("Beenden", app.quit)
    tray.setToolTip("Aktien-Widget")
    tray.setContextMenu(tray_menu)
    tray.activated.connect(lambda reason: toggle_widget(ctl, window) if reason == QSystemTrayIcon.Trigger else None)
    tray.show()

    # Mit --tray (Autostart) läuft das Widget nur im Infobereich; geöffnet wird erst per Klick aufs Symbol.
    if "--tray" not in sys.argv:
        open_widget(ctl, window)
    ctl.start()
    if "etoro" in ctl.sources:
        sync_etoro_now(ctl, notify)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
