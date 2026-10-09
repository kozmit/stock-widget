"""Bestand und realisierter Gewinn aus Transaktionen, ohne Datenbank und ohne Oberfläche.

Der Bestand wird nie gespeichert, sondern immer aus Käufen und Verkäufen berechnet.
Verkäufe verbrauchen die ältesten Stücke zuerst (FIFO, wie es das deutsche Steuerrecht für
Wertpapiere im selben Depot verlangt). Kaufgebühren erhöhen die Anschaffungskosten,
Verkaufsgebühren mindern den Erlös.

Für Auswertungen in einer anderen Währung bleibt sichtbar, aus welchen Käufen der Bestand
besteht (Lot) und welche Käufe ein Verkauf verbraucht hat (Piece), jeweils mit Kaufdatum.
"""
import datetime as dt
from dataclasses import dataclass

EPS = 1e-9
OPENING_NOTE = "Startbestand"  # Käufe mit dieser Notiz sind übernommene Bestände ohne bekanntes Kaufdatum
OPENING_SOURCE = "opening"     # Herkunft solcher Einträge; alte Daten erkennt man noch an der Notiz


@dataclass(frozen=True)
class Transaction:
    id: int
    symbol: str
    kind: str          # "buy" oder "sell"
    shares: float
    price: float
    fee: float
    day: dt.date
    note: str = ""
    source: str = "manual"      # "manual", "opening" (Startbestand) oder der Name einer Anbindung wie "etoro"
    external_id: str = ""       # Kennung beim Anbieter; leer bei manuellen Einträgen


def is_opening(tx):
    return tx.source == OPENING_SOURCE or tx.note.startswith(OPENING_NOTE)


@dataclass(frozen=True)
class Lot:
    """Ein noch gehaltener Teil eines Kaufs."""
    day: dt.date
    shares: float
    unit_cost: float   # Kaufkurs je Stück inklusive anteiliger Kaufgebühr
    opening: bool = False


@dataclass(frozen=True)
class Piece:
    """Der Teil eines Kaufs, den ein Verkauf verbraucht hat."""
    day: dt.date
    shares: float
    cost: float        # Anschaffungskosten dieser Stücke inklusive Kaufgebühr
    opening: bool = False


@dataclass(frozen=True)
class Sale:
    """Ein einzelner Verkauf mit den nach FIFO zugeordneten Anschaffungskosten."""
    transaction_id: int
    day: dt.date
    shares: float
    proceeds: float    # Erlös abzüglich Verkaufsgebühr
    cost: float        # Anschaffungskosten der verkauften Stücke inklusive Kaufgebühren
    pieces: tuple = () # die verbrauchten Käufe, älteste zuerst

    @property
    def gain(self):
        return self.proceeds - self.cost


@dataclass(frozen=True)
class PositionState:
    shares: float
    cost_total: float  # Anschaffungskosten der noch gehaltenen Stücke
    realized: float    # Summe der Gewinne und Verluste aller Verkäufe
    sales: tuple = ()
    lots: tuple = ()   # die noch gehaltenen Käufe, älteste zuerst

    @property
    def avg_cost(self):
        return self.cost_total / self.shares if self.shares > EPS else 0.0


def validate(tx):
    if tx.kind not in ("buy", "sell"):
        raise ValueError("Art muss Kauf oder Verkauf sein")
    if tx.shares <= 0:
        raise ValueError("Die Stückzahl muss größer als 0 sein")
    # Eine geschenkte Aktie (Abspaltung, Zuteilung) kostet nichts: nur importierte Käufe dürfen den Kurs 0 haben
    free = tx.kind == "buy" and tx.price == 0 and tx.source not in ("manual", "opening")
    if tx.price < 0 or (tx.price == 0 and not free):
        raise ValueError("Der Kurs muss größer als 0 sein")
    if tx.fee < 0:
        raise ValueError("Die Gebühr darf nicht negativ sein")


def replay(transactions):
    """Rechnet alle Transaktionen einer Aktie durch. Wirft ValueError, wenn der Verlauf ungültig ist
    (zum Beispiel ein Verkauf von mehr Stücken, als zu dem Zeitpunkt vorhanden waren)."""
    lots = []          # [Stück, Stückkosten, Kaufdatum, Startbestand], älteste zuerst
    realized = 0.0
    sales = []
    # Am selben Tag zählen Käufe vor Verkäufen.
    for tx in sorted(transactions, key=lambda t: (t.day, t.kind != "buy", t.id)):
        validate(tx)
        if tx.kind == "buy":
            lots.append([tx.shares, (tx.shares * tx.price + tx.fee) / tx.shares, tx.day,
                         is_opening(tx)])
            continue
        held = sum(lot[0] for lot in lots)
        if tx.shares > held + EPS:
            raise ValueError(f"Am {tx.day:%d.%m.%Y} sind nur {held:g} Stück vorhanden, "
                             f"verkauft werden {tx.shares:g}")
        remaining, cost, pieces = tx.shares, 0.0, []
        while remaining > EPS:
            take = min(lots[0][0], remaining)
            cost += take * lots[0][1]
            pieces.append(Piece(lots[0][2], take, take * lots[0][1], lots[0][3]))
            lots[0][0] -= take
            remaining -= take
            if lots[0][0] <= EPS:
                lots.pop(0)
        proceeds = tx.shares * tx.price - tx.fee
        sales.append(Sale(tx.id, tx.day, tx.shares, proceeds, cost, tuple(pieces)))
        realized += proceeds - cost
    kept = tuple(Lot(lot[2], lot[0], lot[1], lot[3]) for lot in lots)
    shares = sum(lot[0] for lot in lots)
    if shares <= EPS:
        return PositionState(0.0, 0.0, realized, tuple(sales))
    return PositionState(shares, sum(lot[0] * lot[1] for lot in lots), realized, tuple(sales), kept)
