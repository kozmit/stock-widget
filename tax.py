"""Steuerschätzung für Aktienverkäufe nach deutschem Recht, ohne Oberfläche und ohne Datenbank.

Das Modul rechnet aus den Verkäufen eines Kalenderjahres (FIFO, wie ledger.py sie liefert) eine Schätzung der
Abgeltungsteuer. Jeder Rechenschritt steht als eigene Zeile im Bericht und sagt, woher sein Wert stammt: berechnet
(aus den Transaktionen), manuell (vom Nutzer eingegeben) oder geschätzt (Ergebnis einer Annahme).

Es gilt (vereinfacht, siehe ASSUMPTIONS): 25 % Abgeltungsteuer, darauf 5,5 % Solidaritätszuschlag und optional
Kirchensteuer (8 % oder 9 % der Steuer, wodurch sich die Abgeltungsteuer verringert). Verluste aus Aktienverkäufen
dürfen nur mit Gewinnen aus Aktienverkäufen verrechnet werden (§ 20 Abs. 6 EStG); was übrig bleibt, wird als
Verlustvortrag ins nächste Jahr getragen. Der Sparer-Pauschbetrag (1.000 €, zusammen veranlagt 2.000 €) mindert die
Summe aller Kapitalerträge. Beträge sind in Euro: Kauf und Verkauf werden mit dem Kurs ihres Tages umgerechnet.
Das ist eine Schätzung und keine Steuerberatung; verbindlich ist die Berechnung der Bank.
"""
import datetime as dt
from dataclasses import dataclass, field

import fx as fx_module

TAX_RATE = 0.25
SOLI_RATE = 0.055
ALLOWANCE_SINGLE, ALLOWANCE_JOINT = 1000.0, 2000.0
CHURCH_OPTIONS = ((0.0, "keine"), (0.08, "8 % (Bayern, Baden-Württemberg)"), (0.09, "9 % (übrige Länder)"))
CALCULATED, MANUAL, ESTIMATED = "berechnet", "manuell", "geschätzt"
ASSUMPTIONS = (
    "Abgeltungsteuer 25 % plus 5,5 % Solidaritätszuschlag darauf, bei Kirchensteuer entsprechend weniger Abgeltungsteuer.",
    "Verluste aus Aktienverkäufen werden nur mit Gewinnen aus Aktienverkäufen verrechnet; der Rest wird vorgetragen.",
    "Verkäufe nach FIFO. Anschaffungskosten und Erlös sind mit dem Wechselkurs des Kauf- und des Verkaufstags in Euro umgerechnet.",
    "Nicht berücksichtigt: ausländische Quellensteuer, Teilfreistellung bei Fonds und ETFs (Aktienfonds 30 %), eigener "
    "Verlusttopf für Fonds, Kapitalmaßnahmen, Derivate und Verkäufe bei anderen Banken.",
    "Eine Schätzung, keine Steuerberatung: Verbindlich ist die Berechnung deiner Bank und deiner Steuererklärung.",
)


@dataclass(frozen=True)
class Settings:
    church_rate: float = 0.0    # 0, 0.08 oder 0.09
    joint: bool = False         # zusammen veranlagt: doppelter Sparer-Pauschbetrag

    @property
    def allowance(self):
        return ALLOWANCE_JOINT if self.joint else ALLOWANCE_SINGLE


@dataclass(frozen=True)
class YearInputs:
    """Was die App nicht wissen kann und der Nutzer für ein Jahr angibt."""
    other_income: float = 0.0           # Dividenden, Zinsen und andere Kapitalerträge (nicht aus Aktienverkäufen)
    allowance_elsewhere: float = 0.0    # bei anderen Banken schon verbrauchter Sparer-Pauschbetrag
    stock_loss_carry: float = None      # Aktienverlustvortrag aus dem Vorjahr; None = aus dem Vorjahr berechnen
    general_loss_carry: float = 0.0     # allgemeiner Verlustvortrag (gilt für sonstige Kapitalerträge)


@dataclass(frozen=True)
class SaleFigures:
    """Ein Verkauf in Euro."""
    transaction_id: int
    symbol: str
    day: dt.date
    shares: float
    proceeds: float             # Erlös abzüglich Verkaufsgebühr
    cost: float                 # Anschaffungskosten inklusive Kaufgebühren
    currency: str               # Währung der Aktie
    estimated: bool = False     # Anschaffung teils aus einem Startbestand: Kaufdatum und Kaufkurs sind geschätzt

    @property
    def gain(self):
        return self.proceeds - self.cost


@dataclass(frozen=True)
class Step:
    key: str
    label: str
    value: float
    source: str                 # CALCULATED, MANUAL oder ESTIMATED
    term: str = ""              # Begriff im Glossar
    note: str = ""
    strong: bool = False        # Zwischen- und Endsummen


@dataclass
class YearReport:
    year: int
    sales: list
    steps: list = field(default_factory=list)
    gains: float = 0.0
    losses: float = 0.0
    net_stock: float = 0.0
    stock_carry_in: float = 0.0
    stock_carry_auto: bool = True
    stock_carry_used: float = 0.0
    stock_carry_out: float = 0.0
    other_income: float = 0.0
    general_carry_used: float = 0.0
    general_carry_out: float = 0.0
    subtotal: float = 0.0
    allowance_available: float = 0.0
    allowance_applied: float = 0.0
    allowance_left: float = 0.0
    taxable: float = 0.0
    income_tax: float = 0.0
    soli: float = 0.0
    church: float = 0.0
    tax: float = 0.0
    tax_on_sales: float = 0.0   # Mehrsteuer durch die Aktienverkäufe gegenüber dem Jahr ohne sie
    proceeds: float = 0.0
    net_proceeds: float = 0.0
    warnings: list = field(default_factory=list)


@dataclass(frozen=True)
class Impact:
    """Was ein einzelner Verkauf steuerlich bewirkt."""
    sale: SaleFigures
    tax: float
    allowance_before: float
    allowance_after: float


def rates(church_rate):
    """(Abgeltungsteuer, Soli, Kirchensteuer) als Anteile des steuerpflichtigen Betrags. Die Kirchensteuer mindert
    die Abgeltungsteuer: 25 % ÷ (1 + 25 % × Kirchensteuersatz)."""
    income = TAX_RATE / (1 + TAX_RATE * church_rate)
    return income, income * SOLI_RATE, income * church_rate


def eur_rates(fx):
    """Funktion (Währung, Tag) -> Euro je Einheit, oder None, wenn kein Kurs bekannt ist. Die Basiswährung der App
    kann Euro oder Dollar sein; ist sie Dollar, wird über den Euro-Kurs in Dollar umgerechnet."""
    def rate(currency, day):
        code, scale = fx_module.split_currency(currency)
        if code == "EUR":
            return scale
        value = fx.on(currency, day)
        if value is None:
            return None
        if fx_module.BASE == "EUR":
            return value
        euro = fx.on("EUR", day)
        return value / euro if euro else None
    return rate


def convert_sale(symbol, sale, currency, rate):
    """Der Verkauf in Euro; None, wenn für den Verkauf oder einen der verbrauchten Käufe kein Kurs bekannt ist."""
    sale_rate = rate(currency, sale.day)
    piece_rates = [rate(currency, piece.day) for piece in sale.pieces]
    if sale_rate is None or any(r is None for r in piece_rates):
        return None
    cost = sum(piece.cost * r for piece, r in zip(sale.pieces, piece_rates))
    return SaleFigures(sale.transaction_id, symbol, sale.day, sale.shares, sale.proceeds * sale_rate, cost, currency,
                       any(piece.opening for piece in sale.pieces))


def _core(sales, inputs, settings, carry_in, auto_carry):
    """Die Rechnung eines Jahres als Bericht ohne Rechenschritte und ohne Mehrsteuer."""
    report = YearReport(0, list(sales))
    report.gains = sum(s.gain for s in sales if s.gain > 0)
    report.losses = sum(-s.gain for s in sales if s.gain < 0)
    report.net_stock = report.gains - report.losses
    report.stock_carry_in, report.stock_carry_auto = carry_in, auto_carry
    report.stock_carry_used = min(carry_in, report.net_stock) if report.net_stock > 0 else 0.0
    stock_after = max(report.net_stock - report.stock_carry_used, 0.0)
    report.stock_carry_out = carry_in - report.stock_carry_used + max(-report.net_stock, 0.0)
    report.other_income = inputs.other_income
    report.general_carry_used = min(inputs.general_loss_carry, inputs.other_income)
    report.general_carry_out = inputs.general_loss_carry - report.general_carry_used
    report.subtotal = stock_after + inputs.other_income - report.general_carry_used
    report.allowance_available = max(settings.allowance - inputs.allowance_elsewhere, 0.0)
    report.allowance_applied = min(report.allowance_available, report.subtotal)
    report.allowance_left = report.allowance_available - report.allowance_applied
    report.taxable = report.subtotal - report.allowance_applied
    income, soli, church = rates(settings.church_rate)
    report.income_tax = round(report.taxable * income, 2)
    report.soli = round(report.taxable * soli, 2)
    report.church = round(report.taxable * church, 2)
    report.tax = round(report.income_tax + report.soli + report.church, 2)
    report.proceeds = sum(s.proceeds for s in sales)
    return report


def compute_years(sales, inputs_by_year, settings, years=None):
    """Berichte für mehrere Jahre als {Jahr: YearReport}. Der Aktienverlustvortrag eines Jahres ist, wenn der Nutzer
    keinen angibt, der berechnete Vortrag des Vorjahres; deshalb werden alle Jahre ab dem ersten Verkaufsjahr
    gerechnet."""
    by_year = {}
    for sale in sales:
        by_year.setdefault(sale.day.year, []).append(sale)
    first = min(list(by_year) + list(years or []) + [dt.date.today().year])
    last = max(list(by_year) + list(years or []) + [dt.date.today().year])
    reports, carry = {}, 0.0
    for year in range(first, last + 1):
        inputs = inputs_by_year.get(year) or YearInputs()
        manual = inputs.stock_loss_carry is not None
        carry_in = inputs.stock_loss_carry if manual else carry
        year_sales = sorted(by_year.get(year, []), key=lambda s: (s.day, s.transaction_id))
        report = _core(year_sales, inputs, settings, carry_in, not manual)
        report.year = year
        without = _core([], inputs, settings, carry_in, not manual)
        report.tax_on_sales = round(report.tax - without.tax, 2)
        report.net_proceeds = report.proceeds - report.tax_on_sales
        report.steps = _steps(report, inputs, settings)
        reports[year] = report
        carry = report.stock_carry_out
    return reports


def sale_impact(sales, transaction_id, inputs_by_year, settings):
    """Die steuerliche Wirkung eines Verkaufs: Mehrsteuer und verfügbarer Sparer-Pauschbetrag davor und danach.
    None, wenn es den Verkauf nicht gibt."""
    sale = next((s for s in sales if s.transaction_id == transaction_id), None)
    if sale is None:
        return None
    year = sale.day.year
    with_sale = compute_years(sales, inputs_by_year, settings, [year])[year]
    without = compute_years([s for s in sales if s.transaction_id != transaction_id], inputs_by_year, settings, [year])[year]
    return Impact(sale, round(with_sale.tax - without.tax, 2), without.allowance_left, with_sale.allowance_left)


def _steps(report, inputs, settings):
    """Die Rechenschritte des Jahres in der Reihenfolge der Rechnung."""
    steps = [Step("gains", "Gewinne aus Aktienverkäufen", report.gains, CALCULATED, "realisiert",
                  f"{sum(1 for s in report.sales if s.gain > 0)} Verkäufe mit Gewinn"),
             Step("losses", "Verluste aus Aktienverkäufen", -report.losses, CALCULATED, "aktienverlusttopf",
                  f"{sum(1 for s in report.sales if s.gain < 0)} Verkäufe mit Verlust"),
             Step("net_stock", "Ergebnis aus Aktienverkäufen", report.net_stock, CALCULATED, "", "", True)]
    if report.stock_carry_in or report.stock_carry_used:
        origin = "aus dem Vorjahr berechnet" if report.stock_carry_auto else "manuell eingegeben"
        steps.append(Step("stock_carry", "Aktienverlustvortrag (verrechnet)", -report.stock_carry_used,
                          CALCULATED if report.stock_carry_auto else MANUAL, "verlustvortrag",
                          f"Vortrag {report.stock_carry_in:.2f} €, {origin}"))
    if report.other_income:
        steps.append(Step("other", "Sonstige Kapitalerträge (Dividenden, Zinsen)", report.other_income, MANUAL,
                          "sonstige_kapitalertraege"))
    if report.general_carry_used:
        steps.append(Step("general_carry", "Allgemeiner Verlustvortrag (verrechnet)", -report.general_carry_used,
                          MANUAL, "verlustvortrag"))
    steps.append(Step("subtotal", "Zwischensumme", report.subtotal, CALCULATED, "", "", True))
    note = f"{report.allowance_available:.2f} € verfügbar"
    if inputs.allowance_elsewhere:
        note += f" ({settings.allowance:.0f} € minus {inputs.allowance_elsewhere:.2f} € anderswo verbraucht)"
    steps.append(Step("allowance", "Sparer-Pauschbetrag", -report.allowance_applied,
                      MANUAL if inputs.allowance_elsewhere else CALCULATED, "sparer_pauschbetrag",
                      note + f", {report.allowance_left:.2f} € bleiben übrig"))
    steps.append(Step("taxable", "Steuerpflichtiger Betrag", report.taxable, CALCULATED, "steuerpflichtiger_betrag",
                      "", True))
    income, soli, church = rates(settings.church_rate)
    steps.append(Step("income_tax", f"Abgeltungsteuer ({income * 100:.2f} %)", report.income_tax, ESTIMATED,
                      "abgeltungsteuer"))
    steps.append(Step("soli", "Solidaritätszuschlag (5,5 % der Steuer)", report.soli, ESTIMATED, "soli"))
    if settings.church_rate:
        steps.append(Step("church", f"Kirchensteuer ({settings.church_rate * 100:.0f} % der Steuer)", report.church,
                          ESTIMATED, "kirchensteuer"))
    steps.append(Step("tax", "Geschätzte Steuer gesamt", report.tax, ESTIMATED, "", "", True))
    return steps


SIGNED_KEYS = ("losses", "stock_carry", "general_carry", "allowance")  # Abzüge: mit Vorzeichen anzeigen


def eur(value):
    return f"{value:,.2f} €"
