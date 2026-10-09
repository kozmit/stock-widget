"""Portfolio-Kennzahlen in der Basiswährung, ohne Netzwerk, Datenbank und Oberfläche.

Begriffe:
- Investiert: Anschaffungskosten der noch gehaltenen Stücke (inklusive Kaufgebühren), in der
  Basiswährung zum Wechselkurs des Kaufdatums.
- Unrealisiert: Wert heute minus Investiert. Aufgeteilt in Kursgewinn (Gewinn in Fremdwährung,
  umgerechnet zum Kaufkurs-Wechselkurs) und Währungseffekt (Wert mal Änderung des Wechselkurses).
- Realisiert: Gewinn der Verkäufe, Erlös zum Wechselkurs des Verkaufstags minus Anschaffungskosten
  zum Wechselkurs des Kaufs, ebenfalls in Kursgewinn und Währungseffekt aufgeteilt.
- Gesamtrendite: Gesamtergebnis (unrealisiert + realisiert) geteilt durch das gesamte eingesetzte
  Kapital (Investiert + Anschaffungskosten der verkauften Stücke). Keine zeitgewichtete Rendite.

Fehlt für eine Aktie der Kurs oder ein Wechselkurs, wird sie nicht mitgerechnet und in warnings genannt,
statt eine Zahl zu erfinden.
"""
from dataclasses import dataclass, field

import ledger
import fx as fx_module
from fx import currency_code

UNKNOWN = "Unbekannt"
DIMENSIONS = ("position", "sector", "country", "currency")


@dataclass
class Holding:
    """Eine gehaltene Position, bewertet in der Basiswährung."""
    symbol: str
    name: str
    currency: str          # Notierungswährung
    shares: float
    avg_cost: float        # in Notierungswährung
    price: float           # in Notierungswährung
    value: float           # Basiswährung
    cost: float            # Basiswährung
    price_effect: float
    fx_effect: float
    sector: str = ""
    country: str = ""
    share: float = 0.0     # Anteil am Gesamtwert in Prozent

    @property
    def unrealized(self):
        return self.value - self.cost

    @property
    def pl_pct(self):
        if not self.cost:  # zugeteilte Aktie ohne Einstand: der ganze Wert ist Gewinn
            return 100.0 if self.value > 0 else None
        return self.unrealized / self.cost * 100


@dataclass
class Summary:
    holdings: list = field(default_factory=list)
    value: float = 0.0
    invested: float = 0.0
    price_effect: float = 0.0         # unrealisierter Kursgewinn
    fx_effect: float = 0.0            # unrealisierter Währungseffekt
    realized: float = 0.0
    realized_price: float = 0.0
    realized_fx: float = 0.0
    sold_cost: float = 0.0            # Anschaffungskosten der verkauften Stücke
    cash: float = 0.0                 # verfügbares Guthaben beim Anbieter, in der Basiswährung
    warnings: list = field(default_factory=list)

    @property
    def total_value(self):
        """Positionen plus Guthaben: das, was das Konto insgesamt wert ist."""
        return self.value + self.cash

    @property
    def unrealized(self):
        return self.value - self.invested

    @property
    def unrealized_pct(self):
        return self.unrealized / self.invested * 100 if self.invested else None

    @property
    def total_result(self):
        return self.unrealized + self.realized

    @property
    def total_invested(self):
        return self.invested + self.sold_cost

    @property
    def total_return_pct(self):
        return self.total_result / self.total_invested * 100 if self.total_invested else None


def _missing_rates(rates):
    return any(rate is None for rate in rates)


def summarize(states, quotes, instruments, fx, opening_realized=None, order=None, cash=None):
    """Rechnet alle Positionen und Verkäufe in die Basiswährung um.

    states: Symbol -> ledger.PositionState; quotes: Symbol -> {price, currency}; instruments: Symbol -> Stammdaten;
    fx: fx.FxTable; opening_realized: übernommene realisierte Gewinne je Symbol (Notierungswährung);
    cash: [(Betrag, Währung)] verfügbares Guthaben bei den Anbietern."""
    opening_realized = opening_realized or {}
    summary = Summary()
    warnings = summary.warnings
    foreign_opening = 0      # Positionen mit Startbestand in Fremdwährung
    opening_gain_used = False

    for symbol in order or list(states):
        state = states.get(symbol)
        if state is None:
            continue
        quote = quotes.get(symbol)
        info = instruments.get(symbol, {})
        currency = (quote or {}).get("currency") or info.get("currency") or None

        # --- noch gehaltene Stücke ---
        if state.shares > ledger.EPS:
            if not quote:
                warnings.append(f"{symbol}: kein Kurs, nicht enthalten")
            else:
                now = fx.now(currency)
                rates = [fx.on(currency, lot.day) for lot in state.lots]
                if now is None or _missing_rates(rates):
                    warnings.append(f"{symbol}: kein Wechselkurs {currency_code(currency)} → {fx_module.BASE}, nicht enthalten")
                else:
                    cost = sum(lot.shares * lot.unit_cost * rate for lot, rate in zip(state.lots, rates))
                    value = state.shares * quote["price"] * now
                    price_effect = sum((lot.shares * quote["price"] - lot.shares * lot.unit_cost) * rate
                                       for lot, rate in zip(state.lots, rates))
                    summary.holdings.append(Holding(
                        symbol, info.get("name", ""), currency, state.shares, state.avg_cost, quote["price"],
                        value, cost, price_effect, (value - cost) - price_effect,
                        info.get("sector", ""), info.get("country", "")))
                    if currency_code(currency) != fx_module.BASE and any(lot.opening for lot in state.lots):
                        foreign_opening += 1

        # --- Verkäufe ---
        skipped = 0
        for sale in state.sales:
            sale_rate = fx.on(currency, sale.day) if currency else None
            piece_rates = [fx.on(currency, piece.day) for piece in sale.pieces] if currency else [None]
            if sale_rate is None or _missing_rates(piece_rates):
                skipped += 1
                continue
            for piece, rate in zip(sale.pieces, piece_rates):
                proceeds = sale.proceeds * piece.shares / sale.shares
                gain = proceeds * sale_rate - piece.cost * rate
                price_part = (proceeds - piece.cost) * rate
                summary.realized += gain
                summary.realized_price += price_part
                summary.realized_fx += gain - price_part
                summary.sold_cost += piece.cost * rate
        if skipped:
            warnings.append(f"{symbol}: {skipped} Verkauf(e) ohne Wechselkurs, nicht enthalten")

        # --- übernommener realisierter Gewinn aus dem alten Widget (Datum und Kurs unbekannt) ---
        carried = opening_realized.get(symbol, 0.0)
        if carried:
            rate = fx.now(currency) if currency else None
            if rate is None:
                warnings.append(f"{symbol}: übernommener realisierter Gewinn ohne Wechselkurs, nicht enthalten")
            else:
                summary.realized += carried * rate
                summary.realized_price += carried * rate
                opening_gain_used = True

    total = sum(h.value for h in summary.holdings)
    for holding in summary.holdings:
        holding.share = holding.value / total * 100 if total else 0.0
    summary.value = total
    summary.invested = sum(h.cost for h in summary.holdings)
    summary.price_effect = sum(h.price_effect for h in summary.holdings)
    summary.fx_effect = sum(h.fx_effect for h in summary.holdings)

    for amount, currency in cash or []:
        rate = fx.now(currency)
        if rate is None:
            warnings.append(f"Guthaben {amount:,.2f} {currency}: kein Wechselkurs {currency_code(currency)} → {fx_module.BASE}, "
                            "nicht enthalten")
        else:
            summary.cash += amount * rate

    if foreign_opening:
        warnings.append(f"Startbestand ({foreign_opening} Positionen in Fremdwährung): Das Kaufdatum ist unbekannt, "
                        "der Währungseffekt wird erst ab dem Eintragsdatum gemessen.")
    if opening_gain_used:
        warnings.append("Übernommener realisierter Gewinn aus dem alten Widget ist mit dem heutigen Wechselkurs umgerechnet.")
    return summary


def allocation(holdings, dimension, max_items=8):
    """Aufteilung des Gesamtwerts als [(Name, Wert, Prozent)], größte zuerst; der Rest wird zusammengefasst."""
    label_of = {
        "position": lambda h: h.symbol,
        "sector": lambda h: h.sector or UNKNOWN,
        "country": lambda h: h.country or UNKNOWN,
        "currency": lambda h: currency_code(h.currency),
    }[dimension]
    totals = {}
    for holding in holdings:
        label = label_of(holding)
        totals[label] = totals.get(label, 0.0) + holding.value
    items = sorted(totals.items(), key=lambda item: -item[1])
    if len(items) > max_items:
        rest = items[max_items - 1:]
        items = items[:max_items - 1] + [(f"Übrige ({len(rest)})", sum(value for _, value in rest))]
    total = sum(value for _, value in items)
    return [(label, value, value / total * 100 if total else 0.0) for label, value in items]
