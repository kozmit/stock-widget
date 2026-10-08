"""Bestand und realisierter Gewinn aus Transaktionen, ohne Datenbank und ohne Oberfläche.

Der Bestand wird nie gespeichert, sondern immer aus Käufen und Verkäufen berechnet.
Verkäufe verbrauchen die ältesten Stücke zuerst (FIFO, wie es das deutsche Steuerrecht für
Wertpapiere im selben Depot verlangt). Kaufgebühren erhöhen die Anschaffungskosten,
Verkaufsgebühren mindern den Erlös.
"""
import datetime as dt
from dataclasses import dataclass

EPS = 1e-9


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


@dataclass(frozen=True)
class Sale:
    """Ein einzelner Verkauf mit den nach FIFO zugeordneten Anschaffungskosten."""
    transaction_id: int
    day: dt.date
    shares: float
    proceeds: float    # Erlös abzüglich Verkaufsgebühr
    cost: float        # Anschaffungskosten der verkauften Stücke inklusive Kaufgebühren

    @property
    def gain(self):
        return self.proceeds - self.cost


@dataclass(frozen=True)
class PositionState:
    shares: float
    cost_total: float  # Anschaffungskosten der noch gehaltenen Stücke
    realized: float    # Summe der Gewinne und Verluste aller Verkäufe
    sales: tuple = ()

    @property
    def avg_cost(self):
        return self.cost_total / self.shares if self.shares > EPS else 0.0


def validate(tx):
    if tx.kind not in ("buy", "sell"):
        raise ValueError("Art muss Kauf oder Verkauf sein")
    if tx.shares <= 0:
        raise ValueError("Die Stückzahl muss größer als 0 sein")
    if tx.price <= 0:
        raise ValueError("Der Kurs muss größer als 0 sein")
    if tx.fee < 0:
        raise ValueError("Die Gebühr darf nicht negativ sein")


def replay(transactions):
    """Rechnet alle Transaktionen einer Aktie durch. Wirft ValueError, wenn der Verlauf ungültig ist
    (zum Beispiel ein Verkauf von mehr Stücken, als zu dem Zeitpunkt vorhanden waren)."""
    lots = []          # [Stück, Stückkosten], älteste zuerst
    realized = 0.0
    sales = []
    # Am selben Tag zählen Käufe vor Verkäufen.
    for tx in sorted(transactions, key=lambda t: (t.day, t.kind != "buy", t.id)):
        validate(tx)
        if tx.kind == "buy":
            lots.append([tx.shares, (tx.shares * tx.price + tx.fee) / tx.shares])
            continue
        held = sum(lot[0] for lot in lots)
        if tx.shares > held + EPS:
            raise ValueError(f"Am {tx.day:%d.%m.%Y} sind nur {held:g} Stück vorhanden, "
                             f"verkauft werden {tx.shares:g}")
        remaining, cost = tx.shares, 0.0
        while remaining > EPS:
            take = min(lots[0][0], remaining)
            cost += take * lots[0][1]
            lots[0][0] -= take
            remaining -= take
            if lots[0][0] <= EPS:
                lots.pop(0)
        proceeds = tx.shares * tx.price - tx.fee
        sales.append(Sale(tx.id, tx.day, tx.shares, proceeds, cost))
        realized += proceeds - cost
    shares = sum(lot[0] for lot in lots)
    if shares <= EPS:
        return PositionState(0.0, 0.0, realized, tuple(sales))
    return PositionState(shares, sum(lot[0] * lot[1] for lot in lots), realized, tuple(sales))
