"""Daten und Logik des Aktien-Widgets ohne jede Oberfläche: Kurse, News, Termine, Positionen, Speicher.

Datenquelle: Yahoo Finance über yfinance (kein API-Key, keine KI nötig).
"""
import datetime as dt
import os

import yfinance as yf

DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "StockWidget")
LEGACY_FILE = os.path.join(DATA_DIR, "watchlist.json")  # nur noch zur einmaligen Übernahme
DB_FILE = os.path.join(DATA_DIR, "stock_widget.db")

EVENT_LABELS = {
    "Earnings Date": "Quartalszahlen",
    "Ex-Dividend Date": "Ex-Dividende",
    "Dividend Date": "Dividendenzahlung",
}
EVENT_SHORT = {"Quartalszahlen": "Zahlen", "Ex-Dividende": "Ex-Div.", "Dividendenzahlung": "Dividende"}


# ---------- Kurse, Termine, News ----------

def fetch_quote(symbol):
    info = yf.Ticker(symbol).fast_info
    price, prev = info["last_price"], info["previous_close"]
    if price is None or prev in (None, 0):
        raise ValueError("keine Kursdaten")
    return {
        "price": float(price),
        "change_pct": (float(price) / float(prev) - 1) * 100,
        "currency": info["currency"] or "",
    }


def _to_date(value):
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return None


def fetch_events(symbol):
    """Alle künftigen Termine als sortierte Liste [(date, label)]."""
    calendar = yf.Ticker(symbol).calendar or {}
    today = dt.date.today()
    events = []
    for key, label in EVENT_LABELS.items():
        values = calendar.get(key)
        if values is None:
            continue
        if not isinstance(values, (list, tuple)):
            values = [values]
        for value in values:
            day = _to_date(value)
            if day and day >= today:
                events.append((day, label))
    return sorted(events)


def days_until(day):
    return (day - dt.date.today()).days


def format_event(event, short=False):
    day, label = event
    days = days_until(day)
    if short:
        return f"{EVENT_SHORT[label]} {day.strftime('%d.%m.')} · {days} T"
    when = "heute" if days == 0 else f"in {days} T"
    return f"{label} {day.strftime('%d.%m.%Y')} ({when})"


# Börsenendungen anderer Anbieter, übersetzt in die von Yahoo ("ORA.US" -> "ORA", "ABBN.ZU" -> "ABBN.SW")
SUFFIX_ALIASES = {".US": "", ".ZU": ".SW", ".UK": ".L", ".AU": ".AX"}


def symbol_variants(text):
    """Das eingegebene Kürzel und, falls es eine fremde Börsenendung hat, die Yahoo-Schreibweise."""
    text = text.strip().upper()
    variants = [text]
    base, dot, suffix = text.rpartition(".")
    alias = SUFFIX_ALIASES.get(dot + suffix) if dot else None
    if alias is not None and base:
        variants.append(base + alias)
    return variants


def search_symbols(query, count=8):
    """Sucht Aktien und ETFs nach Name oder Kürzel. Gibt [{symbol, name, exchange, type}] zurück."""
    quotes = yf.Search(query, max_results=count, news_count=0).quotes
    return [{"symbol": q["symbol"], "name": q.get("longname") or q.get("shortname") or "",
             "exchange": q.get("exchDisp") or q.get("exchange") or "", "type": q["quoteType"]}
            for q in quotes if q.get("quoteType") in ("EQUITY", "ETF") and q.get("symbol")]


def describe_event(event):
    """(Titel, Zeitangabe) für die Detailansicht."""
    day, label = event
    days = days_until(day)
    when = "heute" if days == 0 else f"in {days} Tag{'en' if days != 1 else ''}"
    return label, f"{day.strftime('%d.%m.%Y')} · {when}"


def fetch_news(symbol, count=15):
    """News als Liste [(titel, quelle, datetime|None, link)], neueste zuerst."""
    items = []
    try:
        items = yf.Search(symbol, news_count=count, max_results=1).news
    except Exception:
        pass
    if not items:
        items = yf.Ticker(symbol).news or []
    result = []
    for item in items:
        content = item.get("content") or item  # neues und altes Format
        title = content.get("title")
        link = (content.get("canonicalUrl") or {}).get("url") or item.get("link")
        publisher = (content.get("provider") or {}).get("displayName") or item.get("publisher", "")
        stamp = item.get("providerPublishTime")
        if stamp:
            published = dt.datetime.fromtimestamp(stamp).astimezone()
        elif content.get("pubDate"):
            published = dt.datetime.fromisoformat(content["pubDate"].replace("Z", "+00:00")).astimezone()
        else:
            published = None
        if title and link:
            result.append((title, publisher, published, link))
    oldest = dt.datetime.min.replace(tzinfo=dt.timezone.utc)  # alle Zeiten sind zeitzonenbehaftet, sonst ist der Vergleich nicht möglich
    return sorted(result, key=lambda n: n[2] or oldest, reverse=True)


# ---------- Kennzahlen und Eingaben ----------

def pl_percent(position, price):
    """Gewinn/Verlust in % der gehaltenen Stücke; position["cost"] ist der durchschnittliche Einstandskurs."""
    return (price / position["cost"] - 1) * 100


def pl_amount(position, price):
    return (price - position["cost"]) * position["shares"]


def parse_number(text):
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        raise ValueError("Bitte eine Zahl eingeben, z. B. 12,5") from None


def parse_date(text):
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            day = dt.datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
        if day > dt.date.today():
            raise ValueError("Das Datum liegt in der Zukunft")
        return day
    raise ValueError("Bitte ein Datum als TT.MM.JJJJ eingeben")
