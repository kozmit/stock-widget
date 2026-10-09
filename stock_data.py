"""Daten und Logik des Aktien-Widgets ohne jede Oberfläche: Kurse, News, Termine, Positionen, Speicher.

Datenquelle: Yahoo Finance über yfinance (kein API-Key, keine KI nötig).
"""
import datetime as dt
import os
import time
import zoneinfo

import yfinance as yf

import fundamentals

# Fester Ordner im Benutzerprofil, bewusst nicht unter AppData: Programme aus der Claude-App sehen AppData
# umgeleitet, ein per Autostart gestartetes Widget das echte. Nur so gibt es genau eine Datenbank.
DATA_DIR = os.environ.get("STOCKWIDGET_DATA") or os.path.join(os.path.expanduser("~"), "StockWidget")
LEGACY_FILE = os.path.join(DATA_DIR, "watchlist.json")  # nur noch zur einmaligen Übernahme
DB_FILE = os.path.join(DATA_DIR, "stock_widget.db")

SOURCE = "Yahoo Finance"

EVENT_LABELS = {
    "Earnings Date": "Quartalszahlen",
    "Ex-Dividend Date": "Ex-Dividende",
    "Dividend Date": "Dividendenzahlung",
}
EVENT_SHORT = {"Quartalszahlen": "Zahlen", "Ex-Dividende": "Ex-Div.", "Dividendenzahlung": "Dividende",
               # Arten eigener Termine (events.KINDS), kurz genug für die Spalte „Termin“
               "Unternehmensprognose": "Prognose", "Produktstart": "Release", "Genehmigung oder Entscheidung": "Entscheid",
               "Übernahme oder Fusion": "Übernahme", "Branchenveranstaltung": "Event", "Sonstiges": "Termin"}


# ---------- Kurse, Termine, News ----------

# Handelsstatus der Notierung bei Yahoo (marketState) -> "open", "extended" (Vor-/Nachbörse) oder "closed"
MARKET_STATES = {"REGULAR": "open", "PRE": "extended", "POST": "extended",
                 "PREPRE": "closed", "POSTPOST": "closed", "CLOSED": "closed"}


def fetch_market_state(ticker):
    """Handelsstatus der Notierung dieses Symbols; None, wenn Yahoo ihn nicht liefert. Ein Fehler hier
    darf den Kurs nicht verhindern."""
    try:
        return MARKET_STATES.get(ticker.info.get("marketState"))
    except Exception:
        return None


def previous_close(info):
    """Schlusskurs der letzten regulären Sitzung vor der laufenden. fast_info["previous_close"] nimmt dagegen den
    letzten Kurs des Vortags samt Nachbörse und weicht davon ab; er bleibt nur als Ersatz."""
    try:
        regular = info["regular_market_previous_close"]
    except KeyError:
        regular = None
    # Bei manchen Werten (zum Beispiel ETFs in London und Frankfurt) liefert Yahoo hier NaN
    if regular is None or regular == 0 or regular != regular:
        return info["previous_close"]
    return regular


def _epoch(value):
    """Sekunden seit 1970; yfinance liefert je nach Version Zeitstempel (pandas) oder ganze Zahlen."""
    return value.timestamp() if hasattr(value, "timestamp") else float(value)


def session_not_started(ticker, now=None):
    """Wahr, wenn die reguläre Sitzung dieses Handelstages noch nicht begonnen hat (Vorbörse, Wochentag). Dann ist
    der letzte Kurs der Schlusskurs der letzten Sitzung, und heute gibt es noch keine Veränderung. Am Wochenende und
    bei fehlenden Metadaten gilt das nicht: dort zeigt die Veränderung die letzte Sitzung."""
    try:
        meta = ticker.get_history_metadata()
        start = _epoch(meta["currentTradingPeriod"]["regular"]["start"])
        last = _epoch(meta["regularMarketTime"])
        now = time.time() if now is None else now
        local = dt.datetime.fromtimestamp(now, zoneinfo.ZoneInfo(meta["exchangeTimezoneName"]))
        return now < start and last < start and local.weekday() < 5
    except Exception:
        return False


def fetch_quote(symbol):
    ticker = yf.Ticker(symbol)
    info = ticker.fast_info
    price, prev = info["last_price"], previous_close(info)
    if price is None or price != price or prev is None or prev == 0 or prev != prev:  # None, 0 oder NaN
        raise ValueError("keine Kursdaten")
    if session_not_started(ticker):
        prev = price
    return {
        "price": float(price),
        "change_pct": (float(price) / float(prev) - 1) * 100,
        "currency": info["currency"] or "",
        "source": SOURCE,
        "market_state": fetch_market_state(ticker),  # wird nicht gespeichert: ein alter Status wäre irreführend
    }


# Zeiträume des Kursdiagramms: Schlüssel, Beschriftung, yfinance-Zeitraum und Kerzenlänge
HISTORY_RANGES = (
    ("1w", "1 W", "7d", "1h"),
    ("1m", "1 M", "1mo", "1d"),
    ("6m", "6 M", "6mo", "1d"),
    ("1y", "1 J", "1y", "1d"),
    ("5y", "5 J", "5y", "1wk"),
)
DEFAULT_RANGE = "6m"


def fetch_history(symbol, range_key=DEFAULT_RANGE):
    """Schlusskurse als Liste [(Zeitpunkt, Kurs)] für einen Zeitraum aus HISTORY_RANGES."""
    _, _, period, interval = next(r for r in HISTORY_RANGES if r[0] == range_key)
    frame = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=False)
    closes = frame["Close"].dropna()
    points = [(stamp.to_pydatetime(), float(value)) for stamp, value in closes.items()]
    if len(points) < 2:
        raise ValueError("keine Kursverlaufsdaten")
    return points


def fetch_daily_closes(symbol, start):
    """Tagesschlusskurse ab start als {Datum: Kurs}; Grundlage für den Verlauf des Portfolios."""
    frame = yf.Ticker(symbol).history(start=start.isoformat(), interval="1d", auto_adjust=False)
    closes = {stamp.date(): float(value) for stamp, value in frame["Close"].dropna().items() if value > 0}
    if not closes:
        raise ValueError("keine Kursverlaufsdaten")
    return closes


def fx_pair(currency, base):
    return f"{currency}{base}=X"


def fetch_fx_rate(currency, base):
    """Aktueller Kurs: so viele Einheiten base kostet eine Einheit currency."""
    rate = yf.Ticker(fx_pair(currency, base)).fast_info["last_price"]
    if not rate or rate <= 0:
        raise ValueError(f"kein Wechselkurs {currency} → {base}")
    return float(rate)


def fetch_fx_history(currency, base, start):
    """Tageskurse ab start als {Datum: Kurs}."""
    history = yf.Ticker(fx_pair(currency, base)).history(start=start.isoformat(), interval="1d")
    rates = {stamp.date(): float(close) for stamp, close in history["Close"].items() if close and close > 0}
    if not rates:
        raise ValueError(f"keine Wechselkurs-Historie {currency} → {base}")
    return rates


def fetch_instrument(symbol):
    """Stammdaten einer Aktie. Die ISIN liefert Yahoo nur bei ETFs; sonst bleibt das Feld leer."""
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}
    name = info.get("longName") or info.get("shortName")
    if not name:
        raise ValueError("keine Stammdaten")
    try:
        isin = ticker.isin
    except Exception:
        isin = None
    return {
        "name": name,
        "exchange": info.get("fullExchangeName") or info.get("exchange") or "",
        "currency": info.get("currency") or "",
        "sector": info.get("sector") or "",
        "industry": info.get("industry") or "",
        "country": info.get("country") or "",
        "isin": isin if isin and isin != "-" else "",
        "source": SOURCE,
    }


def fetch_targets(symbol):
    """Kursziele der Analysten laut Yahoo: {mean, high, low, count, currency}. None, wenn es keine gibt
    (Rohstoffe, ETFs und kleine Werte haben oft keine). Die Währung ist die der Kursziele."""
    info = yf.Ticker(symbol).info or {}
    mean = info.get("targetMeanPrice")
    if not mean:
        return None
    return {"mean": float(mean), "high": info.get("targetHighPrice"), "low": info.get("targetLowPrice"),
            "count": info.get("numberOfAnalystOpinions"),
            "currency": info.get("financialCurrency") or info.get("currency") or ""}


def fetch_fundamentals(symbol):
    """Kennzahlen einer Aktie laut Yahoo (siehe fundamentals.from_info). ValueError, wenn Yahoo keine nennt:
    ETFs, Rohstoffe und kleine Werte haben meist keine, das ist kein Fehler der Verbindung."""
    info = yf.Ticker(symbol).info or {}
    data = fundamentals.from_info(info)
    if fundamentals.is_empty(data):
        raise ValueError("Yahoo nennt für diesen Wert keine Kennzahlen")
    return data


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
        return f"{EVENT_SHORT.get(label, label)} {day.strftime('%d.%m.')} · {days} T"
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
    """Gewinn/Verlust in % der gehaltenen Stücke; position["cost"] ist der durchschnittliche Einstandskurs.
    Ohne Einstand (zugeteilte Aktie) ist der ganze Wert Gewinn: 100 %."""
    if not position["cost"]:
        return 100.0
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
