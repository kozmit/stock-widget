"""Historie: Marken im Kursdiagramm, Zusammenfassung der Transaktionen und der Verlauf des Portfolios.
Reine Logik ohne Netzwerk, Datenbank und Oberfläche.

Der Verlauf rechnet Tag für Tag mit denselben Regeln wie die Übersicht (portfolio.summarize), nur mit dem
Bestand und den Kursen des jeweiligen Tages. So stimmt der letzte Punkt des Verlaufs mit der Übersicht überein.
"""
import bisect
import datetime as dt
from dataclasses import dataclass, field

import ledger
import portfolio

SOURCE_LABELS = {"manual": "Manuell", "opening": "Startbestand", "etoro": "eToro"}


def source_label(source):
    return SOURCE_LABELS.get(source, source.capitalize() if source else "Manuell")


# ---------- Kursdiagramm ----------

@dataclass(frozen=True)
class Marker:
    index: int            # Platz im Kursverlauf
    kind: str             # "buy" oder "sell"
    opening: bool         # Startbestand: das Kaufdatum ist nur das Eintragsdatum
    price: float
    text: str


def marker_index(points, day):
    """Platz des letzten Kurspunkts am oder vor day; None, wenn day außerhalb des gezeigten Zeitraums liegt."""
    if not points or day < points[0][0].date() or day > points[-1][0].date():
        return None
    index = 0
    for i, (stamp, _) in enumerate(points):
        if stamp.date() > day:
            break
        index = i
    return index


def describe(tx):
    """Eine Zeile über eine Transaktion: Art, Menge, Kurs, Datum und Herkunft."""
    kind = "Kauf" if tx.kind == "buy" else "Verkauf"
    text = f"{kind} {tx.shares:g} Stk · {tx.price:.2f} · {tx.day:%d.%m.%Y}"
    if ledger.is_opening(tx):
        return f"Startbestand {tx.shares:g} Stk · {tx.price:.2f} · eingetragen {tx.day:%d.%m.%Y} (Kaufdatum unbekannt)"
    if tx.source not in ("", "manual"):
        text += f" · {source_label(tx.source)}"
    return text


def chart_markers(transactions, points):
    """Marken für die Käufe und Verkäufe, die im gezeigten Zeitraum liegen."""
    markers = []
    for tx in sorted(transactions, key=lambda t: (t.day, t.id)):
        index = marker_index(points, tx.day)
        if index is not None:
            markers.append(Marker(index, tx.kind, ledger.is_opening(tx), tx.price, describe(tx)))
    return markers


# ---------- Zusammenfassung ----------

@dataclass(frozen=True)
class HistorySummary:
    count: int
    first_day: dt.date
    last_day: dt.date
    bought: float
    sold: float
    fees: float
    sources: dict           # Herkunft -> Anzahl
    only_opening: bool      # es gibt nur Startbestand-Einträge, also keine echte Kaufhistorie


def summarize_history(transactions):
    if not transactions:
        return None
    sources = {}
    for tx in transactions:
        key = ledger.OPENING_SOURCE if ledger.is_opening(tx) else (tx.source or "manual")
        sources[key] = sources.get(key, 0) + 1
    return HistorySummary(
        len(transactions), min(t.day for t in transactions), max(t.day for t in transactions),
        sum(t.shares for t in transactions if t.kind == "buy"), sum(t.shares for t in transactions if t.kind == "sell"),
        sum(t.fee for t in transactions), sources, all(ledger.is_opening(t) for t in transactions))


# ---------- Verlauf des Portfolios ----------

@dataclass(frozen=True)
class PerformancePoint:
    day: dt.date
    value: float          # Wert der gehaltenen Stücke in der Basiswährung
    invested: float       # Anschaffungskosten der gehaltenen Stücke
    realized: float       # bis zu diesem Tag realisierter Gewinn
    result: float         # unrealisiert + realisiert
    return_pct: float     # Ergebnis auf das bis dahin eingesetzte Kapital, None ohne Kapital


@dataclass
class PerformanceSeries:
    points: list = field(default_factory=list)
    missing: list = field(default_factory=list)   # Aktien, für die es Kurse oder Wechselkurse nicht gab


class _AsOfFx:
    """Wechselkurse so, wie sie an einem bestimmten Tag galten (statt 'jetzt')."""

    def __init__(self, fx, day):
        self.fx, self.day = fx, day

    def now(self, currency):
        return self.fx.on(currency, self.day)

    def on(self, currency, day):
        return self.fx.on(currency, day)


def _close_on(days, closes, day):
    index = bisect.bisect_right(days, day)
    return closes[days[index - 1]] if index else None


def performance_series(transactions, closes, currencies, fx, max_points=250):
    """Verlauf von Wert, Investiert und Ergebnis.

    transactions: Kürzel -> Liste ledger.Transaction; closes: Kürzel -> {Tag: Schlusskurs};
    currencies: Kürzel -> Notierungswährung; fx: fx.FxTable."""
    all_days = sorted({t.day for txs in transactions.values() for t in txs})
    if not all_days:
        return PerformanceSeries()
    start = all_days[0]
    days = {d for d in all_days}
    for symbol_closes in closes.values():
        days.update(d for d in symbol_closes if d >= start)
    days = sorted(days)
    if len(days) > max_points:  # gleichmäßig ausdünnen, erster und letzter Tag bleiben
        step = (len(days) - 1) / (max_points - 1)
        days = [days[round(i * step)] for i in range(max_points)]

    sorted_closes = {symbol: sorted(values) for symbol, values in closes.items()}
    by_day = {symbol: sorted(txs, key=lambda t: (t.day, t.id)) for symbol, txs in transactions.items()}
    entry_days = {symbol: [t.day for t in txs] for symbol, txs in by_day.items()}
    cache = {}  # (Aktie, Anzahl der Einträge bis zum Tag) -> Zustand oder None bei ungültigem Verlauf
    missing = set()
    series = PerformanceSeries()
    for day in days:
        states, quotes = {}, {}
        for symbol, txs in by_day.items():
            count = bisect.bisect_right(entry_days[symbol], day)
            if not count:
                continue
            if (symbol, count) not in cache:
                try:
                    cache[(symbol, count)] = ledger.replay(txs[:count])
                except ValueError:
                    cache[(symbol, count)] = None
            state = cache[(symbol, count)]
            if state is None:
                missing.add(symbol)
                continue
            price = _close_on(sorted_closes.get(symbol, []), closes.get(symbol, {}), day)
            if price is None or symbol not in currencies or fx.on(currencies[symbol], day) is None:
                missing.add(symbol)
                continue
            states[symbol] = state
            quotes[symbol] = {"price": price, "currency": currencies[symbol]}
        summary = portfolio.summarize(states, quotes, {}, _AsOfFx(fx, day))
        series.points.append(PerformancePoint(day, summary.value, summary.invested, summary.realized,
                                              summary.total_result, summary.total_return_pct))
    series.missing = sorted(missing)
    return series
