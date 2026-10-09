"""Einschätzungen der Analysten (Empfehlungen und Gewinnerwartung gegen Ergebnis), ohne Oberfläche.

Analysten sind im Durchschnitt zu optimistisch: Viele Banken vergeben kaum Verkaufen-Empfehlungen, und Kursziele folgen
dem Kurs eher, als ihn vorherzusagen. Deshalb zeigt dieses Modul die Verteilung der Empfehlungen statt einer
Gesamtnote und warnt bei wenigen Analysten. Belastbarer ist der Vergleich einer Gewinnerwartung mit dem späteren
Ergebnis, den es als nachprüfbare Zahl mitliefert.
"""
import datetime as dt

FEW_ANALYSTS = 5        # darunter ist der Durchschnitt kaum aussagekräftig
# (Schlüssel, Feld bei Yahoo, Anzeige)
CATEGORIES = (("strong_buy", "strongBuy", "Stark kaufen"), ("buy", "buy", "Kaufen"), ("hold", "hold", "Halten"),
              ("sell", "sell", "Verkaufen"), ("strong_sell", "strongSell", "Stark verkaufen"))
BEFORE_PERIOD = "-3m"   # Vergleichswert: die Verteilung vor drei Monaten


def _count(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if number == number and number > 0 else 0


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _counts(row):
    counts = {key: _count(row.get(field)) for key, field, _ in CATEGORIES}
    return counts if sum(counts.values()) else None


def parse_recommendations(rows):
    """Aus den Zeilen von Yahoo (je Zeitraum "0m", "-1m", ...): {"now": Zahlen oder None, "before": Zahlen oder None}."""
    by_period = {str(row.get("period")): row for row in rows or []}
    return {"now": _counts(by_period["0m"]) if "0m" in by_period else None,
            "before": _counts(by_period[BEFORE_PERIOD]) if BEFORE_PERIOD in by_period else None}


def total(counts):
    return sum(counts.values()) if counts else 0


def sides(counts):
    """(positiv, neutral, negativ): Stark kaufen und Kaufen, Halten, Verkaufen und Stark verkaufen."""
    return (counts["strong_buy"] + counts["buy"], counts["hold"], counts["sell"] + counts["strong_sell"])


def positive_share(counts):
    """Anteil der Kaufen-Empfehlungen als Bruchteil, None ohne Analysten."""
    count = total(counts)
    return sides(counts)[0] / count if count else None


def distribution_text(counts):
    """„25 Kaufen · 13 Halten · 6 Verkaufen“; leere Gruppen fehlen."""
    names = ("Kaufen", "Halten", "Verkaufen")
    return " · ".join(f"{number} {name}" for number, name in zip(sides(counts), names) if number)


def trend_text(now, before):
    """Wie sich der Anteil der Kaufen-Empfehlungen gegenüber vor drei Monaten verändert hat."""
    if not now or not before:
        return ""
    current, earlier = positive_share(now), positive_share(before)
    change = (current - earlier) * 100
    if abs(change) < 1:
        word = "unverändert"
    else:
        word = f"{change:+.0f} Prozentpunkte"
    return f"Kaufen-Anteil {current * 100:.0f} % ({word} seit 3 Monaten)"


def caution(count):
    """Warnung, wenn die Zahl der Analysten klein ist; sonst leer."""
    if count and count < FEW_ANALYSTS:
        return f"Nur {count} Analyst{'en' if count != 1 else ''}: wenig aussagekräftig."
    return ""


def quarter_label(day):
    return f"Q{(day.month - 1) // 3 + 1} {day.year}"


def parse_surprises(rows, limit=4):
    """Die jüngsten Quartale mit Erwartung und Ergebnis, neuestes zuerst:
    [{quarter: Datum, actual, estimate, surprise: Bruchteil oder None}]. Zeilen ohne beide Zahlen fallen weg; die
    Abweichung wird aus den Zahlen berechnet statt von Yahoo übernommen (bei einer Erwartung von 0 gibt es keine)."""
    result = []
    for row in rows or []:
        actual, estimate = _number(row.get("epsActual")), _number(row.get("epsEstimate"))
        quarter = row.get("quarter")
        if isinstance(quarter, dt.datetime):
            quarter = quarter.date()
        elif isinstance(quarter, str):
            try:
                quarter = dt.date.fromisoformat(quarter[:10])
            except ValueError:
                quarter = None
        if actual is None or estimate is None or not isinstance(quarter, dt.date):
            continue
        surprise = (actual - estimate) / abs(estimate) if estimate else None
        result.append({"quarter": quarter, "actual": actual, "estimate": estimate, "surprise": surprise})
    return sorted(result, key=lambda item: item["quarter"], reverse=True)[:limit]


def surprise_line(item):
    """„Q2 2026: 2.02 statt 1.89 erwartet (+6.7 %)“."""
    text = f"{quarter_label(item['quarter'])}: {item['actual']:.2f} statt {item['estimate']:.2f} erwartet"
    if item["surprise"] is not None:
        text += f" ({item['surprise'] * 100:+.1f} %)"
    return text


def beat_summary(surprises):
    """„3 von 4 Quartalen über der Erwartung“; leer ohne Quartale."""
    known = [item for item in surprises if item["surprise"] is not None]
    if not known:
        return ""
    beaten = sum(1 for item in known if item["surprise"] > 0)
    return f"{beaten} von {len(known)} Quartalen über der Erwartung"


def is_empty(data):
    return not data or (not (data["recommendations"]["now"]) and not data["surprises"])
