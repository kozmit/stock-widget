"""Termine zu Aktien: Art, Status, Zeitraum, Zusammenführen und Kalenderfenster. Reine Logik ohne Oberfläche,
Datenbank und Netzwerk.

Jeder Termin hat einen Status, der ehrlich sagt, wie verlässlich er ist:
- confirmed (bestätigt): vom Unternehmen oder der zuständigen Stelle offiziell genannt,
- expected (erwartet): Schätzung oder Angabe eines Datenanbieters, nicht bestätigt,
- speculative (spekulativ): Gerücht oder Vermutung, nie wie ein bestätigter Termin behandelt,
- occurred (eingetreten): hat stattgefunden. Ein bestätigter oder erwarteter Termin gilt nach Ablauf seines Datums
  von selbst als eingetreten; ein spekulativer bleibt spekulativ und wird als „Datum verstrichen“ gekennzeichnet.

Ein Termin kann ungenau sein (nur Monat oder nur Jahr). Dann steht er in der Kalenderansicht in einem eigenen
Abschnitt „ohne genauen Tag“ statt an einem erfundenen Datum.

Meldet derselbe Termin mehrere Quellen (zum Beispiel Yahoo und ein eigener Eintrag), werden sie zu einem
zusammengeführt (merge_duplicates); abweichende Daten bleiben als Hinweis erhalten.
"""
import calendar
import datetime as dt
import re
from dataclasses import dataclass

MANUAL = "manual"

KINDS = {
    "earnings": "Quartalszahlen",
    "ex_dividend": "Ex-Dividende",
    "dividend": "Dividendenzahlung",
    "guidance": "Unternehmensprognose",
    "product": "Produktstart",
    "regulatory": "Genehmigung oder Entscheidung",
    "merger": "Übernahme oder Fusion",
    "conference": "Branchenveranstaltung",
    "other": "Sonstiges",
}
# Bezeichnung bei Yahoo (stock_data.EVENT_LABELS) -> Art
YAHOO_KINDS = {"Quartalszahlen": "earnings", "Ex-Dividende": "ex_dividend", "Dividendenzahlung": "dividend"}
DEFAULT_RELEVANCE = {"earnings": 3, "guidance": 3, "merger": 3, "regulatory": 3, "product": 2, "conference": 2,
                     "ex_dividend": 2, "dividend": 1, "other": 2}
RELEVANCE = {1: "niedrig", 2: "normal", 3: "hoch"}

STATUSES = ("confirmed", "expected", "speculative", "occurred")
STATUS_LABELS = {"confirmed": "bestätigt", "expected": "erwartet", "speculative": "spekulativ",
                 "occurred": "eingetreten"}
STATUS_RANK = {"occurred": 4, "confirmed": 3, "expected": 2, "speculative": 1}
PRECISIONS = ("day", "month", "year")
PRECISION_RANK = {"day": 3, "month": 2, "year": 1}

# Anbieter nennen Zahlentermine oft um einige Tage versetzt; solche Einträge gelten als derselbe Termin.
TOLERANCE_DAYS = {"earnings": 7, "guidance": 7}
# Diese Arten gibt es je Aktie nur einmal pro Zeitraum; bei allen anderen (Produktstart, Konferenz ...) können
# mehrere Termine zugleich laufen und gelten nur bei gleichem Titel als derselbe.
SINGLE_KINDS = ("earnings", "ex_dividend", "dividend", "guidance")

MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
          "Dezember")
_MONTH_NAMES = {}
for _index, _name in enumerate(MONTHS, start=1):
    _lower = _name.lower()
    for _key in {_lower, _lower[:3], _lower.replace("ä", "ae")}:
        _MONTH_NAMES[_key] = _index
_MONTH_NAMES.update({"jän": 1, "jaen": 1, "mrz": 3, "sept": 9})

WHEN_HINT = "Bitte ein Datum eingeben: 08.10.2026, 10/2026 (Monat) oder 2026 (Jahr)."


@dataclass(frozen=True)
class Event:
    id: int
    symbol: str
    kind: str
    title: str
    day: dt.date            # Beginn des Zeitraums
    end: dt.date            # Ende (inklusive); bei einem genauen Tag gleich day
    precision: str          # "day", "month" oder "year"
    status: str
    source: str             # "manual" oder der Name der Quelle, zum Beispiel "Yahoo Finance"
    source_url: str = ""
    external_id: str = ""   # Kennung bei der Quelle; leer bei eigenen Einträgen
    relevance: int = 2
    note: str = ""

    @property
    def is_manual(self):
        return self.source == MANUAL


def source_label(source):
    return "Manuell" if source == MANUAL else source


def default_relevance(kind):
    return DEFAULT_RELEVANCE.get(kind, 2)


# ---------- Datum ----------

def parse_when(text):
    """Liest eine Eingabe als Zeitraum: (Beginn, Ende, Genauigkeit). "08.10.2026" ist ein Tag, "10/2026" oder
    "Oktober 2026" ein Monat, "2026" ein Jahr."""
    text = " ".join(str(text).split())
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            day = dt.datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return day, day, "day"
    month = year = None
    match = re.fullmatch(r"(\d{1,2})[./](\d{4})", text)
    if match:
        month, year = int(match[1]), int(match[2])
    else:
        match = re.fullmatch(r"([A-Za-zÄÖÜäöüß]+)\.? (\d{4})", text)
        if match:
            month, year = _MONTH_NAMES.get(match[1].lower()), int(match[2])
    if month is not None and year is not None:
        if not 1 <= month <= 12 or not 1990 <= year <= 2100:
            raise ValueError(WHEN_HINT)
        return dt.date(year, month, 1), dt.date(year, month, calendar.monthrange(year, month)[1]), "month"
    if re.fullmatch(r"\d{4}", text) and 1990 <= int(text) <= 2100:
        return dt.date(int(text), 1, 1), dt.date(int(text), 12, 31), "year"
    raise ValueError(WHEN_HINT)


def date_text(event):
    if event.precision == "month":
        return f"{MONTHS[event.day.month - 1]} {event.day.year}"
    if event.precision == "year":
        return str(event.day.year)
    if event.end != event.day:
        first = f"{event.day:%d.%m.%Y}" if event.day.year != event.end.year else f"{event.day:%d.%m.}"
        return f"{first}–{event.end:%d.%m.%Y}"
    return f"{event.day:%d.%m.%Y}"


def _plural(count, one, many):
    return one if count == 1 else many


def relative_day(day, today):
    """„heute“, „morgen“, „gestern“, „in 7 Tagen“ oder „vor 3 Tagen“."""
    delta = (day - today).days
    if delta == 0:
        return "heute"
    if abs(delta) == 1:
        return "morgen" if delta > 0 else "gestern"
    return f"in {delta} Tagen" if delta > 0 else f"vor {-delta} Tagen"


def relative_text(event, today):
    """Wie relative_day; bei Monat und Jahr entsprechend grob, bei einem laufenden Zeitraum „läuft jetzt“."""
    if event.precision == "day":
        if event.day <= today <= event.end:
            return "heute" if event.day == event.end else "läuft jetzt"
        return relative_day(event.day if event.day > today else event.end, today)
    unit = "month" if event.precision == "month" else "year"
    here = today.year * 12 + today.month if unit == "month" else today.year
    there = event.day.year * 12 + event.day.month if unit == "month" else event.day.year
    delta = there - here
    names = ("Monat", "Monaten") if unit == "month" else ("Jahr", "Jahren")
    if delta == 0:
        return "in diesem Monat" if unit == "month" else "in diesem Jahr"
    if delta == 1:
        return "im nächsten Monat" if unit == "month" else "im nächsten Jahr"
    if delta == -1:
        return "im letzten Monat" if unit == "month" else "im letzten Jahr"
    return f"in {delta} {names[1]}" if delta > 0 else f"vor {-delta} {names[1]}"


def when_text(event, today):
    return f"{date_text(event)} · {relative_text(event, today)}"


# ---------- Status ----------

def effective_status(event, today):
    """Der Status, der heute gilt: bestätigte und erwartete Termine sind nach ihrem Datum eingetreten."""
    if event.status in ("confirmed", "expected") and event.end < today:
        return "occurred"
    return event.status


def is_overdue(event, today):
    """Ein spekulativer Termin, dessen Datum vorbei ist, ohne dass etwas bekannt wäre."""
    return event.status == "speculative" and event.end < today


# ---------- Zusammenführen ----------

@dataclass(frozen=True)
class MergedEvent:
    event: Event            # der maßgebliche Eintrag
    members: tuple          # alle zusammengeführten Einträge, auch der maßgebliche
    status: str             # wirksamer Status: der am besten gesicherte der Mitglieder

    @property
    def sources(self):
        result = []
        for member in self.members:
            label = source_label(member.source)
            if label not in result:
                result.append(label)
        return tuple(result)

    @property
    def other_dates(self):
        """Abweichende Angaben anderer Quellen: [(Datumstext, Quelle)]."""
        shown = date_text(self.event)
        return [(date_text(m), source_label(m.source)) for m in self.members
                if m is not self.event and date_text(m) != shown]


def _primary_key(event, today):
    return (STATUS_RANK[effective_status(event, today)], PRECISION_RANK[event.precision], event.is_manual,
            event.relevance, -event.id)


def merge_duplicates(events, today):
    """Fasst Einträge zusammen, die denselben Termin meinen: gleiche Aktie, gleiche Art, überlappender Zeitraum
    (bei Zahlenterminen mit einigen Tagen Spielraum)."""
    groups = {}
    for event in events:
        title = "" if event.kind in SINGLE_KINDS else " ".join(event.title.casefold().split())
        groups.setdefault((event.symbol, event.kind, title), []).append(event)
    merged = []
    for (symbol, kind, _title), items in groups.items():
        tolerance = dt.timedelta(days=TOLERANCE_DAYS.get(kind, 0))
        clusters, current, current_end = [], [], None
        for event in sorted(items, key=lambda e: (e.day, e.id)):
            if current and event.day <= current_end + tolerance:
                current.append(event)
                current_end = max(current_end, event.end)
            else:
                if current:
                    clusters.append(current)
                current, current_end = [event], event.end
        if current:
            clusters.append(current)
        for cluster in clusters:
            primary = max(cluster, key=lambda e: _primary_key(e, today))
            status = max((effective_status(e, today) for e in cluster), key=STATUS_RANK.get)
            merged.append(MergedEvent(primary, tuple(cluster), status))
    return merged


# ---------- Kalenderfenster ----------

def sort_key(merged):
    event = merged.event
    return (event.day, -event.relevance, -STATUS_RANK[merged.status], event.symbol, event.id)


def calendar_window(merged, today, days):
    """Kommende Termine der nächsten days Tage: (mit genauem Tag, ohne genauen Tag). Eingetretene fehlen."""
    last = today + dt.timedelta(days=days)
    dated, undated = [], []
    for item in merged:
        event = item.event
        if item.status == "occurred" or event.end < today or event.day > last:
            continue
        (dated if event.precision == "day" else undated).append(item)
    return sorted(dated, key=sort_key), sorted(undated, key=sort_key)


def recent(merged, today, days=30):
    """Was in den letzten days Tagen war (eingetreten oder spekulativ verstrichen), jüngstes zuerst."""
    first = today - dt.timedelta(days=days)
    items = [m for m in merged if m.event.end < today and m.event.end >= first
             and (m.status == "occurred" or is_overdue(m.event, today))]
    return sorted(items, key=lambda m: (m.event.end, m.event.id), reverse=True)


def upcoming(merged, today):
    """Alle Termine, die noch nicht vorbei sind, nach Beginn geordnet (auch ungenaue)."""
    return sorted((m for m in merged if m.status != "occurred" and m.event.end >= today), key=sort_key)


# ---------- Eintragen ----------

def make_event(symbol, kind, title, when_text_input, status, relevance=None, note="", source_url=""):
    """Prüft die Eingaben für einen eigenen Termin und gibt die Felder für die Datenbank zurück."""
    symbol = symbol.strip().upper()
    if not symbol:
        raise ValueError("Zu welcher Aktie gehört der Termin?")
    if kind not in KINDS:
        raise ValueError("Die Art des Termins ist unbekannt")
    if status not in STATUSES:
        raise ValueError("Der Status ist unbekannt")
    relevance = default_relevance(kind) if relevance is None else relevance
    if relevance not in RELEVANCE:
        raise ValueError("Die Relevanz muss niedrig, normal oder hoch sein")
    day, end, precision = parse_when(when_text_input)
    return {"symbol": symbol, "kind": kind, "title": " ".join(str(title).split()) or KINDS[kind], "day": day,
            "end": end, "precision": precision, "status": status, "relevance": relevance,
            "note": " ".join(str(note).split()), "source_url": str(source_url).strip()}
