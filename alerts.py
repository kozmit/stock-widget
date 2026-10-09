"""Benachrichtigungsregeln und ihre Auswertung, ohne Oberfläche, Datenbank und Netzwerk.

Das Modul kennt keine Datenquelle. Es bekommt einen Datenstand (Context: Kurse, Termine, News, Kursziele) und prüft damit
die Regeln. Jede Regel liefert Treffer mit einem festen Schlüssel; der Schlüssel verhindert, dass dieselbe Sache
zweimal gemeldet wird. Kursregeln melden einmal beim Überschreiten und scharfen sich erst wieder, wenn die Bedingung
nicht mehr gilt. Mit explain()/evaluate() lässt sich eine Regel gefahrlos testen: sie sagt, was sie jetzt melden würde
und warum nicht.
"""
import datetime as dt
from dataclasses import dataclass, field

import events as evt
import newsfeed

KINDS = {
    "price_above": "Kurs steigt auf …",
    "price_below": "Kurs fällt auf …",
    "day_move": "Tagesbewegung von mindestens …",
    "earnings": "Termin steht bevor",
    "news": "Wichtige News",
    "analyst": "Kursziel der Analysten ändert sich",
}
PRICE_KINDS = ("price_above", "price_below")
BASELINE_KINDS = ("news", "analyst")    # beim ersten Mal wird der Ist-Zustand still gemerkt, nichts gemeldet
NEWS_HOURS = 24                          # so jung muss eine wichtige News sein
MAX_LINES = 12
DIGEST_MODES = {"off": "aus", "daily": "täglich", "weekly": "wöchentlich"}
WEEKDAY_NAMES = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")


@dataclass(frozen=True)
class Rule:
    id: int
    kind: str
    symbol: str = None          # None: alle Positionen
    threshold: float = None     # Kurs (price_*), Prozent (day_move, analyst)
    days: int = None            # Tage im Voraus (earnings)
    enabled: bool = True
    armed: bool = True          # nur für Kursregeln: bereit, beim nächsten Überschreiten zu melden
    baselined: bool = False     # nur für news und analyst: der Ist-Zustand wurde gemerkt

    def describe(self):
        scope = self.symbol or "Alle Positionen"
        if self.kind == "price_above":
            return f"{scope}: Kurs steigt auf {self.threshold:g} oder mehr"
        if self.kind == "price_below":
            return f"{scope}: Kurs fällt auf {self.threshold:g} oder weniger"
        if self.kind == "day_move":
            return f"{scope}: Tagesbewegung von mindestens {self.threshold:g} %"
        if self.kind == "earnings":
            return f"{scope}: Termin in {self.days} Tagen oder früher"
        if self.kind == "news":
            return f"{scope}: wichtige News"
        return f"{scope}: Kursziel ändert sich um mindestens {self.threshold:g} %"


def check_rule(kind, symbol, threshold, days):
    """Prüft die Angaben zu einer Regel und gibt sie bereinigt zurück: (kind, symbol, threshold, days).
    Wirft ValueError mit einer verständlichen Meldung."""
    if kind not in KINDS:
        raise ValueError("Unbekannte Art der Regel")
    symbol = symbol.strip().upper() if symbol else None
    if kind in PRICE_KINDS:
        if not symbol:
            raise ValueError("Eine Kursregel braucht eine Aktie")
        if threshold is None or threshold <= 0:
            raise ValueError("Der Kurs muss größer als 0 sein")
        return kind, symbol, float(threshold), None
    if kind in ("day_move", "analyst"):
        if threshold is None or not 0 < threshold <= 100:
            raise ValueError("Die Schwelle muss zwischen 0 und 100 Prozent liegen")
        return kind, symbol, float(threshold), None
    if kind == "earnings":
        if days is None or not 0 <= days <= 60 or int(days) != days:
            raise ValueError("Die Vorlaufzeit muss eine ganze Zahl zwischen 0 und 60 Tagen sein")
        return kind, symbol, None, int(days)
    return kind, symbol, None, None


@dataclass
class Context:
    """Der Datenstand, gegen den Regeln geprüft werden."""
    now: dt.datetime
    quotes: dict = field(default_factory=dict)      # Kürzel -> {"price", "change_pct", "currency"}
    fresh: set = field(default_factory=set)        # Kürzel mit aktuellem (nicht veraltetem) Kurs
    positions: list = field(default_factory=list)  # Kürzel mit Bestand
    symbols: list = field(default_factory=list)    # die ganze Watchlist
    events: dict = field(default_factory=dict)     # Kürzel -> [events.MergedEvent]
    news: list = field(default_factory=list)       # [newsfeed.FeedItem], zusammengeführt und eingestuft
    news_loaded: set = field(default_factory=set)  # Kürzel, deren News geladen sind
    targets: dict = field(default_factory=dict)    # Kürzel -> {"mean", "reference", "currency"}

    @property
    def today(self):
        return self.now.date()


@dataclass(frozen=True)
class Hit:
    key: str            # gleicher Schlüssel = gleiche Meldung
    symbol: str
    title: str
    text: str


@dataclass
class Evaluation:
    hits: list = field(default_factory=list)
    armed: bool = True                      # neuer Zustand einer Kursregel
    lines: list = field(default_factory=list)   # Erklärung, was die Regel gesehen hat
    ready: bool = True                      # die nötigen Daten waren da (für den stillen ersten Durchlauf)
    references: dict = field(default_factory=dict)  # analyst: neue Vergleichswerte {Kürzel: Kursziel}

    @property
    def would_fire(self):
        return bool(self.hits)


def scope(rule, ctx):
    """Die Aktien, auf die sich eine Regel bezieht: eine bestimmte oder alle Positionen."""
    if rule.symbol:
        return [rule.symbol] if rule.symbol in ctx.symbols else []
    return [s for s in ctx.symbols if s in ctx.positions]


def _price_rule(rule, ctx):
    result = Evaluation(armed=rule.armed)
    [symbol] = scope(rule, ctx) or [None]
    if symbol is None:
        result.ready = False
        result.lines.append(f"{rule.symbol} steht nicht in der Watchlist.")
        return result
    quote = ctx.quotes.get(symbol)
    if not quote or symbol not in ctx.fresh:
        result.ready = False
        result.lines.append(f"{symbol}: kein aktueller Kurs, die Regel wartet.")
        return result
    price, cur = quote["price"], quote.get("currency", "")
    above = rule.kind == "price_above"
    met = price >= rule.threshold if above else price <= rule.threshold
    if met:
        result.lines.append(f"{symbol} steht bei {price:.2f} {cur}: Schwelle {rule.threshold:g} erreicht.")
    else:
        away = abs(rule.threshold / price - 1) * 100
        result.lines.append(f"{symbol} steht bei {price:.2f} {cur}, Schwelle {rule.threshold:g}: noch {away:.1f} % entfernt.")
    if met and rule.armed:
        word = "über" if above else "unter"
        result.hits.append(Hit(f"price:{rule.id}:{ctx.now:%Y%m%d%H%M%S}", symbol, f"{symbol}: Kursalarm",
                               f"{symbol} steht bei {price:.2f} {cur}, {word} deiner Schwelle {rule.threshold:g}."))
    elif met:
        result.lines.append("Die Regel hat schon gemeldet und scharft sich erst wieder, wenn der Kurs zurückkehrt.")
    result.armed = not met
    return result


def _day_move_rule(rule, ctx):
    result = Evaluation()
    for symbol in scope(rule, ctx):
        quote = ctx.quotes.get(symbol)
        if not quote or symbol not in ctx.fresh:
            continue
        change = quote["change_pct"]
        met = abs(change) >= rule.threshold
        if met or rule.symbol:
            result.lines.append(f"{symbol}: {change:+.2f} % heute" + (" (Schwelle erreicht)" if met else
                                                                      f" (Schwelle {rule.threshold:g} %)"))
        if met:
            word = "gestiegen" if change > 0 else "gefallen"
            result.hits.append(Hit(f"move:{rule.id}:{symbol}:{ctx.today.isoformat()}", symbol,
                                   f"{symbol}: {change:+.1f} % heute",
                                   f"{symbol} ist heute um {abs(change):.1f} % {word} (Schwelle {rule.threshold:g} %)."))
    if not result.lines:
        result.lines.append(f"Keine Aktie hat sich heute um {rule.threshold:g} % oder mehr bewegt.")
    return result


def _earnings_rule(rule, ctx):
    result = Evaluation()
    for symbol in scope(rule, ctx):
        for merged in ctx.events.get(symbol, []):
            event = merged.event
            if event.precision != "day" or merged.status == "occurred":
                continue
            delta = (event.day - ctx.today).days
            if not 0 <= delta <= rule.days:
                continue
            status = evt.STATUS_LABELS[merged.status]
            when = evt.relative_day(event.day, ctx.today)
            text = f"{symbol}: {event.title}, {evt.date_text(event)} ({when}), Status {status}, Quelle {', '.join(merged.sources)}."
            result.lines.append(text)
            result.hits.append(Hit(f"event:{rule.id}:{event.id}:{event.day.isoformat()}", symbol,
                                   f"{symbol}: {event.title} {when}", text))
    if not result.lines:
        result.lines.append(f"Kein Termin in den nächsten {rule.days} Tagen.")
    return result


def _news_rule(rule, ctx):
    result = Evaluation()
    watched = scope(rule, ctx)
    result.ready = any(s in ctx.news_loaded for s in watched)
    if not result.ready:
        result.lines.append("Die News sind noch nicht geladen.")
        return result
    oldest = ctx.now - dt.timedelta(hours=NEWS_HOURS)  # ctx.now und die Zeiten der News haben eine Zeitzone
    for item in ctx.news:
        if item.importance < newsfeed.MIN_IMPORTANT or not item.published or item.published < oldest:
            continue
        symbols = [s for s in item.symbols if s in watched]
        if not symbols:
            continue
        label = f"{', '.join(symbols)}: {item.tag or 'wichtige News'}"
        result.lines.append(f"{label} · {item.title}")
        result.hits.append(Hit(f"news:{rule.id}:{newsfeed.link_key(item.link) or newsfeed.title_key(item.title)}",
                               symbols[0], label, item.title))
    if not result.lines:
        result.lines.append(f"Keine wichtige News in den letzten {NEWS_HOURS} Stunden.")
    return result


def _analyst_rule(rule, ctx):
    result = Evaluation()
    watched = scope(rule, ctx)
    known = [s for s in watched if s in ctx.targets]
    result.ready = bool(known)
    if not known:
        result.lines.append("Die Kursziele sind noch nicht geladen.")
        return result
    for symbol in known:
        target = ctx.targets[symbol]
        mean, reference, cur = target["mean"], target.get("reference"), target.get("currency", "")
        if not reference:
            result.references[symbol] = mean
            result.lines.append(f"{symbol}: Kursziel Ø {mean:.2f} {cur} wird als Ausgangswert gemerkt.")
            continue
        change = (mean / reference - 1) * 100
        met = abs(change) >= rule.threshold
        result.lines.append(f"{symbol}: Kursziel Ø {mean:.2f} {cur}, zuletzt {reference:.2f}: {change:+.1f} % "
                            + ("(Schwelle erreicht)" if met else f"(Schwelle {rule.threshold:g} %)"))
        if met:
            result.hits.append(Hit(f"target:{rule.id}:{symbol}:{mean:.2f}", symbol, f"{symbol}: Kursziel {change:+.1f} %",
                                   f"Das durchschnittliche Kursziel der Analysten für {symbol} liegt bei {mean:.2f} {cur} "
                                   f"({change:+.1f} % gegenüber {reference:.2f}). Analysten-Ziele sind Schätzungen."))
            result.references[symbol] = mean
    return result


_EVALUATORS = {"price_above": _price_rule, "price_below": _price_rule, "day_move": _day_move_rule,
               "earnings": _earnings_rule, "news": _news_rule, "analyst": _analyst_rule}


def evaluate(rule, ctx):
    """Prüft eine Regel gegen den Datenstand. Die Treffer enthalten noch nicht, ob sie schon gemeldet wurden."""
    result = _EVALUATORS[rule.kind](rule, ctx)
    del result.lines[MAX_LINES:]
    return result


# ---------- Einstellungen und Zusammenfassung ----------

@dataclass(frozen=True)
class Settings:
    enabled: bool = True
    types: tuple = tuple((kind, True) for kind in KINDS)   # (Art, an?) – einzelne Arten lassen sich abschalten
    digest_mode: str = "off"
    digest_hour: int = 18
    digest_weekday: int = 4                                 # Freitag

    def type_enabled(self, kind):
        return dict(self.types).get(kind, True)

    def to_dict(self):
        return {"enabled": self.enabled, "types": dict(self.types), "digest_mode": self.digest_mode,
                "digest_hour": self.digest_hour, "digest_weekday": self.digest_weekday}

    @staticmethod
    def from_dict(data):
        data = data if isinstance(data, dict) else {}
        flags = data.get("types") if isinstance(data.get("types"), dict) else {}
        hour, weekday = data.get("digest_hour", 18), data.get("digest_weekday", 4)
        mode = data.get("digest_mode", "off")
        return Settings(bool(data.get("enabled", True)),
                        tuple((kind, bool(flags.get(kind, True))) for kind in KINDS),
                        mode if mode in DIGEST_MODES else "off",
                        hour if isinstance(hour, int) and 0 <= hour <= 23 else 18,
                        weekday if isinstance(weekday, int) and 0 <= weekday <= 6 else 4)


def digest_key(settings, now):
    """Schlüssel der fälligen Zusammenfassung oder None. Täglich ab der gewählten Stunde, wöchentlich am gewählten
    Wochentag ab dieser Stunde (und danach in derselben Woche, falls das Widget da nicht lief)."""
    if settings.digest_mode == "daily" and now.hour >= settings.digest_hour:
        return f"digest:day:{now.date().isoformat()}"
    if settings.digest_mode == "weekly":
        due = (now.weekday(), now.hour) >= (settings.digest_weekday, settings.digest_hour)
        if due:
            year, week, _ = now.isocalendar()
            return f"digest:week:{year}-{week:02d}"
    return None


def build_digest(settings, ctx, days=7):
    """(Titel, Text) der Zusammenfassung: Bewegung der Positionen, Termine der nächsten Tage, wichtige News."""
    weekly = settings.digest_mode == "weekly"
    year, week, _ = ctx.now.isocalendar()
    title = f"Wochenzusammenfassung KW {week}" if weekly else f"Tageszusammenfassung {ctx.today:%d.%m.%Y}"
    lines = []
    moves = sorted(((ctx.quotes[s]["change_pct"], s) for s in ctx.positions if s in ctx.quotes and s in ctx.fresh),
                   reverse=True)
    if moves:
        up = sum(1 for change, _ in moves if change > 0)
        lines.append(f"Positionen heute: {up} im Plus, {len(moves) - up} im Minus.")
        best, worst = moves[0], moves[-1]
        lines.append(f"Stärkste: {best[1]} {best[0]:+.1f} %, schwächste: {worst[1]} {worst[0]:+.1f} %.")
    upcoming = []
    for symbol in ctx.positions:
        for merged in ctx.events.get(symbol, []):
            event = merged.event
            if event.precision == "day" and merged.status != "occurred" and 0 <= (event.day - ctx.today).days <= days:
                upcoming.append((event.day, symbol, event.title, merged.status))
    upcoming.sort()
    if upcoming:
        shown = [f"{day:%d.%m.} {symbol} {title} ({evt.STATUS_LABELS[status]})" for day, symbol, title, status in upcoming[:5]]
        lines.append(f"Termine der nächsten {days} Tage: " + "; ".join(shown) + (" …" if len(upcoming) > 5 else ""))
    else:
        lines.append(f"Keine Termine der Positionen in den nächsten {days} Tagen.")
    important = [i for i in ctx.news if i.importance >= newsfeed.MIN_IMPORTANT and i.published
                 and any(s in ctx.positions for s in i.symbols)]
    if important:
        lines.append(f"Wichtige News: {len(important)}, zum Beispiel „{important[0].title}“ ({', '.join(important[0].symbols)}).")
    return title, "\n".join(lines)


def short(text, limit=240):
    """Für die Meldung im Infobereich: Windows kürzt lange Texte."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"
