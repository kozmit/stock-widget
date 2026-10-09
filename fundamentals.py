"""Fundamentaldaten einer Aktie (Bewertung, Rentabilität, Wachstum, Bilanz, Dividende), ohne Oberfläche.

Die Rohdaten kommen von Yahoo Finance (ticker.info). Dieses Modul wählt die Kennzahlen aus, prüft sie auf
Plausibilität und formatiert sie. Yahoo meldet manche Werte uneinheitlich (Dividendenrendite mal als Bruchteil, mal
als Prozent) und rechnet Bilanzzahlen in der Berichtswährung des Unternehmens, nicht in der Handelswährung. Beides
fängt dieses Modul ab, statt es der Oberfläche zu überlassen.
"""
import datetime as dt
from dataclasses import dataclass

MAX_AGE = dt.timedelta(hours=12)        # so alt dürfen gespeicherte Kennzahlen sein, bevor sie neu geladen werden
STALE_AFTER = dt.timedelta(days=7)      # ab hier gilt der Stand als veraltet und wird so markiert


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    field: str          # Feld in ticker.info (die Dividendenrendite wird gesondert berechnet)
    term: str           # Schlüssel im Glossar
    group: str
    kind: str           # ratio (Faktor), percent (Bruchteil), money (Betrag in Berichtswährung),
                        # market_money (Betrag in Handelswährung), per_share (je Aktie, Berichtswährung)
    positive_only: bool = False     # negative Werte (Verlust, negatives Eigenkapital) sind keine sinnvolle Kennzahl


GROUPS = ("Bewertung", "Rentabilität", "Wachstum", "Größe und Bilanz", "Dividende und Risiko")

METRICS = (
    Metric("pe", "KGV", "trailingPE", "kgv", "Bewertung", "ratio", True),
    Metric("forward_pe", "KGV (erwartet)", "forwardPE", "kgv_erwartet", "Bewertung", "ratio", True),
    Metric("pb", "KBV", "priceToBook", "kbv", "Bewertung", "ratio", True),
    Metric("ps", "KUV", "priceToSalesTrailing12Months", "kuv", "Bewertung", "ratio", True),
    Metric("ev_ebitda", "EV/EBITDA", "enterpriseToEbitda", "ev_ebitda", "Bewertung", "ratio", True),
    Metric("peg", "PEG", "pegRatio", "peg", "Bewertung", "ratio", True),
    Metric("gross_margin", "Bruttomarge", "grossMargins", "bruttomarge", "Rentabilität", "percent"),
    Metric("op_margin", "Operative Marge", "operatingMargins", "marge", "Rentabilität", "percent"),
    Metric("net_margin", "Nettomarge", "profitMargins", "nettomarge", "Rentabilität", "percent"),
    Metric("roe", "Eigenkapitalrendite", "returnOnEquity", "eigenkapitalrendite", "Rentabilität", "percent"),
    Metric("revenue_growth", "Umsatzwachstum", "revenueGrowth", "umsatzwachstum", "Wachstum", "percent"),
    Metric("earnings_growth", "Gewinnwachstum", "earningsGrowth", "gewinnwachstum", "Wachstum", "percent"),
    Metric("eps", "Gewinn je Aktie", "trailingEps", "eps", "Wachstum", "per_share"),
    Metric("market_cap", "Marktwert", "marketCap", "marktkapitalisierung", "Größe und Bilanz", "market_money"),
    Metric("revenue", "Umsatz", "totalRevenue", "umsatz", "Größe und Bilanz", "money"),
    Metric("free_cashflow", "Freier Cashflow", "freeCashflow", "free_cashflow", "Größe und Bilanz", "money"),
    Metric("cash", "Bargeld", "totalCash", "bargeld", "Größe und Bilanz", "money"),
    Metric("debt", "Schulden", "totalDebt", "schulden", "Größe und Bilanz", "money"),
    Metric("debt_to_equity", "Verschuldungsgrad", "debtToEquity", "verschuldungsgrad", "Größe und Bilanz", "percent_points"),
    Metric("current_ratio", "Liquidität (Current Ratio)", "currentRatio", "current_ratio", "Größe und Bilanz", "ratio"),
    Metric("dividend_yield", "Dividendenrendite", "", "dividendenrendite", "Dividende und Risiko", "percent"),
    Metric("payout_ratio", "Ausschüttungsquote", "payoutRatio", "ausschuettungsquote", "Dividende und Risiko", "percent"),
    Metric("beta", "Beta", "beta", "beta", "Dividende und Risiko", "ratio"),
)
BY_KEY = {metric.key: metric for metric in METRICS}
MAX_RATIO = 1000.0      # darüber steht nur noch „über 1000“, ein KGV von 40000 sagt nichts mehr


def _number(value):
    """Eine endliche Zahl oder None (Yahoo liefert auch Text, NaN und „Infinity“)."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


# Börsen, an denen in Untereinheiten notiert wird (Pence statt Pfund): Kurs ÷ Faktor ist der Kurs in der Hauptwährung.
MINOR_UNITS = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100), "ILA": ("ILS", 100)}
MAX_YIELD = 0.30    # eine Dividendenrendite über 30 % ist fast sicher ein Datenfehler und wird nicht gezeigt


def major_currency(currency):
    """Die Hauptwährung zu einer Notierungswährung: Pence (GBp) sind Hundertstel Pfund (GBP)."""
    return MINOR_UNITS.get(currency, (currency, 1))[0]


def dividend_yield(info):
    """Dividendenrendite als Bruchteil. Yahoos Feld dividendYield steht in Prozent (0,43 heißt 0,43 %), ältere Versionen
    meldeten einen Bruchteil. Welcher Fall vorliegt, zeigt die Gegenrechnung aus Jahresdividende und Kurs (der Kurs
    wird bei Pence-Notierung vorher in Pfund umgerechnet). Das Vorjahresfeld trailingAnnualDividendYield taugt dafür
    nicht: es rechnet Dividende und Kurs oft in verschiedenen Währungen (ADR einer taiwanischen Firma in Dollar)."""
    rate = _number(info.get("dividendRate"))
    price = _number(info.get("currentPrice")) or _number(info.get("regularMarketPrice"))
    factor = MINOR_UNITS.get(info.get("currency"), ("", 1))[1]
    computed = rate / (price / factor) if rate is not None and price else None
    field = _number(info.get("dividendYield"))
    if field is None:
        value = computed
    elif computed and 0.5 <= field / computed <= 2:
        value = field           # schon ein Bruchteil (ältere Datenlage)
    else:
        value = field / 100
    return value if value is not None and 0 <= value <= MAX_YIELD else None


def _period(info):
    stamp = _number(info.get("mostRecentQuarter"))
    if stamp is None:
        return None
    try:
        return dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def from_info(info):
    """Die Kennzahlen aus ticker.info als JSON-fähiges Dict:
    {values: {key: Zahl oder None}, currency: Berichtswährung, price_currency: Handelswährung, period: ISO-Datum}."""
    values = {}
    for metric in METRICS:
        values[metric.key] = dividend_yield(info) if metric.key == "dividend_yield" else _number(info.get(metric.field))
    # Yahoo gibt den Verschuldungsgrad in Prozent an (150 = 1,5-faches Eigenkapital); als Bruchteil speichern
    if values["debt_to_equity"] is not None:
        values["debt_to_equity"] /= 100
    return {"values": values,
            "currency": info.get("financialCurrency") or info.get("currency") or "",
            "price_currency": major_currency(info.get("currency") or ""),
            "period": _period(info)}


def is_empty(data):
    """Ob Yahoo gar nichts geliefert hat (ETFs, Rohstoffe und kleine Werte haben meist keine Kennzahlen)."""
    return not data or all(value is None for value in data["values"].values())


def _scaled(amount):
    for limit, name in ((1e12, "Bio."), (1e9, "Mrd."), (1e6, "Mio.")):
        if abs(amount) >= limit:
            return f"{amount / limit:.2f} {name}"
    return f"{amount:,.0f}"


def format_value(metric, value, data):
    """Text zu einem Wert. Fehlt er, steht „–“; negative Verhältniszahlen (Verlust) heißen „negativ“."""
    if value is None:
        return "–"
    if metric.positive_only and value <= 0:
        return "negativ"
    if metric.kind == "ratio":
        return f"über {MAX_RATIO:.0f}" if value > MAX_RATIO else f"{value:.2f}"
    if metric.kind == "percent":
        return f"{value * 100:.1f} %"
    if metric.kind == "percent_points":
        return f"{value * 100:.0f} %"
    currency = data["price_currency"] if metric.kind == "market_money" else data["currency"]
    if metric.kind == "per_share":
        return f"{value:.2f} {currency}".strip()
    return f"{_scaled(value)} {currency}".strip()


def tone(metric, value):
    """Wie ein Wert einzufärben ist: 'good', 'bad' oder '' (neutral). Nur dort, wo das Vorzeichen etwas aussagt."""
    if value is None:
        return ""
    if metric.kind == "percent" and metric.key in ("net_margin", "op_margin", "revenue_growth", "earnings_growth", "roe"):
        return "good" if value > 0 else "bad" if value < 0 else ""
    if metric.key in ("eps", "free_cashflow"):
        return "good" if value > 0 else "bad" if value < 0 else ""
    return ""


def groups(data):
    """Für die Anzeige: [(Gruppe, [(Metric, Text, Tonart)])]. Gruppen ohne jeden Wert fallen weg."""
    result = []
    for group in GROUPS:
        lines = []
        for metric in METRICS:
            if metric.group != group:
                continue
            value = data["values"].get(metric.key)
            lines.append((metric, format_value(metric, value, data), tone(metric, value)))
        if any(value is not None for value in (data["values"].get(m.key) for m, _, _ in lines)):
            result.append((group, lines))
    return result


def age_note(data, fetched_at, now=None):
    """Quelle, Stand und Berichtszeitraum in einem Satz; ist der Stand veraltet, steht das davor."""
    now = now or dt.datetime.now()
    parts = []
    if now - fetched_at > STALE_AFTER:
        parts.append(f"Veraltet: Stand {fetched_at:%d.%m.%Y}.")
    else:
        parts.append(f"Stand {fetched_at:%d.%m.%Y %H:%M}.")
    parts.append("Quelle: Yahoo Finance.")
    if data.get("period"):
        parts.append(f"Letztes Quartal laut Bilanz: {dt.date.fromisoformat(data['period']):%d.%m.%Y}.")
    if data["currency"] and data["price_currency"] and data["currency"] != data["price_currency"]:
        parts.append(f"Bilanzzahlen in {data['currency']}, Kurs in {data['price_currency']}.")
    return " ".join(parts)


def is_stale(fetched_at, now=None):
    return (now or dt.datetime.now()) - fetched_at > STALE_AFTER


def needs_refresh(fetched_at, now=None):
    return fetched_at is None or (now or dt.datetime.now()) - fetched_at > MAX_AGE
