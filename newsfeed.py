"""Gemeinsamer News-Feed über mehrere Aktien, ohne Oberfläche.

Die News einer Aktie kommen aus stock_data.fetch_news als [(titel, quelle, datum, link)]. Dieses Modul macht daraus
einen kurzen Feed: unpassende News fallen weg (keywords.filter_news), dieselbe Meldung bei mehreren Aktien erscheint
einmal mit allen Kürzeln, jede Meldung wird einfach nach Stichwörtern eingestuft (Zahlen, Übernahme, Zulassung ...),
Wichtiges steht oben, Kursgerede und Ratgeber-Listen sind ausgeblendet, und Meldungen zu einem bekannten Termin
tragen diesen als Hinweis. Die Einstufung ist bewusst grob und nachvollziehbar: jede Regel steht in RULES.
"""
import datetime as dt
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import events as evt
import keywords as kw

DEFAULT_HOURS = 48
DEFAULT_LIMIT = 30
MIN_IMPORTANT = 3       # ab dieser Stufe gilt eine Meldung als wichtig (Zahlen, Übernahme, Zulassung ...)
NORMAL = 1
NOISE = 0               # Kursgerede und Ratgeber: nur mit „Auch Unwichtiges“ sichtbar


def _words(*patterns):
    return re.compile("|".join(patterns), re.I)


# (Stufe, Hinweis, Muster), die erste passende Regel mit der höchsten Stufe gilt
RULES = (
    (3, "Zahlen", _words(r"\bearnings\b", r"\bquartals?zahlen\b", r"\bgesch[äa]ftszahlen\b", r"\bquarterly results\b",
                         r"\b(first|second|third|fourth)[- ]quarter\b", r"\bQ[1-4]\b.{0,20}\b(results|earnings|revenue)\b",
                         r"\b(results|earnings) (for|in) (the )?(first|second|third|fourth|Q[1-4])\b",
                         r"\bbilanz\b", r"\bquartal\b")),
    (3, "Prognose", _words(r"\bguidance\b", r"\boutlook\b", r"\bprognose\b", r"\bausblick\b",
                           r"\b(raises|lifts|lowers|cuts|slashes|boosts|hikes|reaffirms|withdraws)\b.{0,25}\b(forecast|outlook|guidance|target)\b",
                           r"\b(hebt|senkt|kappt|erh[öo]ht)\b.{0,25}\b(prognose|ausblick)\b")),
    (3, "Übernahme", _words(r"\bacqui(re|res|red|sition|sitions)\b", r"\btakeover\b", r"\bmerger\b", r"\bmerges?\b",
                            r"\bbuyout\b", r"\bbid for\b", r"\bstake in\b", r"\b[üu]bernahme\b", r"\b[üu]bernimmt\b",
                            r"\bfusion\b", r"\bbeteiligung\b", r"\bspin-?off\b", r"\babspaltung\b")),
    (3, "Zulassung", _words(r"\bfda\b", r"\bapproval\b", r"\bapproves?\b", r"\bapproved\b", r"\bclears?\b.{0,15}\bregulat",
                            r"\bzulassung\b", r"\bgenehmig", r"\bregulator\b")),
    (3, "Rechtliches", _words(r"\blawsuit\b", r"\bsues\b", r"\bsued\b", r"\bprobe\b", r"\binvestigation\b",
                              r"\bsettle(s|ment)\b", r"\bantitrust\b", r"\bfined?\b", r"\bklage\b", r"\bermittlung",
                              r"\bstrafe\b", r"\brecall\b", r"\br[üu]ckruf\b", r"\bbankrupt", r"\binsolvenz\b",
                              r"\bsanction", r"\bsanktion")),
    (3, "Führung", _words(r"\bsteps? down\b", r"\bresigns?\b", r"\bappoints?\b", r"\bnames? (a )?new\b",
                          r"\bnew (ceo|cfo|chief)\b", r"\br[üu]cktritt\b", r"\bneuer (chef|vorstand)\b")),
    (2, "Auftrag", _words(r"\bcontracts?\b", r"\bawarded\b", r"\bwins?\b.{0,20}\b(order|deal|contract)\b", r"\bdeal\b",
                          r"\bpartnership\b", r"\bagreement\b", r"\bauftrag\b", r"\bvertrag\b", r"\bpartnerschaft\b")),
    (2, "Analysten", _words(r"\bupgrad(e|es|ed)\b", r"\bdowngrad(e|es|ed)\b", r"\bprice target\b", r"\bkursziel\b",
                            r"\binitiates coverage\b", r"\bhochgestuft\b", r"\babgestuft\b", r"\banalyst")),
    (2, "Produkt", _words(r"\blaunch(es|ed)?\b", r"\bunveils?\b", r"\bintroduces?\b", r"\breleases?\b", r"\bpr[äa]sentiert\b",
                          r"\bvorgestellt\b", r"\bmarkteinf[üu]hrung\b")),
    (2, "Dividende", _words(r"\bdividends?\b", r"\bbuybacks?\b", r"\bshare repurchase\b", r"\bdividende\b",
                            r"\baktienr[üu]ckkauf\b")),
)
# Kursgerede, Bewertungs-Ratgeber und Listen ohne Neuigkeit; zählt nur, wenn keine andere Regel passt (Stufe 1)
NOISE_PATTERN = _words(r"^why\b.{0,60}\b(stock|shares?)\b", r"\bshould you (buy|sell)\b", r"\bis .{0,40}\ba (buy|sell|good)\b",
                       r"\b\d+ (best|top|stocks)\b", r"\bbest .{0,25}stocks?\b", r"\bstocks? to (buy|watch|sell)\b",
                       r"\b(stock|shares?|aktie)\b.{0,30}\b(jumps?|falls?|rises?|slides?|surges?|drops?|climbs?|soars?|plunges?|"
                       r"tumbles?|sinks?|rallies|rallys?|gains?|slips?|steigt|f[äa]llt|springt|st[üu]rzt)\b",
                       r"\b(up|down) (today|this week|so far)\b", r"\bmagnificent seven\b", r"\bwhat to know\b",
                       r"\bhere'?s why\b", r"\bprice prediction\b", r"\bprediction\b", r"\bheute\b.{0,20}\b(steigt|f[äa]llt)\b",
                       r"\bkaufen oder\b", r"\bchance oder\b", r"\b(fair value|undervalued|overvalued|unterbewertet|"
                       r"[üu]berbewertet)\b", r"\bbetter (buy|stock|investment)\b", r"\ba buy now\b",
                       r"\bworth (investing|buying)\b", r"\bjim cramer\b", r"\b(stocks?|shares?) (is|are) (expensive|cheap)\b",
                       r"\bstocks? (in focus|trade|move|surge|slide|rally)\b", r"\bbullish calls?\b",
                       r"\bwall street'?s? (bullish|bearish)\b", r"\btop wall street analyst research calls\b",
                       r"\bfutures\b", r"\bread this before\b", r"\bbull call spreads?\b", r"\bstocktwits\b")
# Hinweis -> Arten von Terminen, zu denen so eine Meldung gehören kann
TAG_KINDS = {"Zahlen": ("earnings",), "Prognose": ("guidance", "earnings"), "Dividende": ("ex_dividend", "dividend"),
             "Zulassung": ("regulatory",), "Rechtliches": ("regulatory",), "Übernahme": ("merger",),
             "Produkt": ("product",)}
EVENT_WINDOW_DAYS = 5   # so weit darf die Meldung vom Termin entfernt liegen


@dataclass
class FeedItem:
    title: str
    source: str
    published: dt.datetime        # mit Zeitzone, oder None
    link: str
    symbols: list = field(default_factory=list)
    importance: int = NORMAL
    tag: str = ""
    event_note: str = ""


def link_key(link):
    """Gleiche Meldung unter verschiedenen Adressen (mit Verfolgungsparametern, www, Schrägstrich)."""
    parts = urlsplit(link or "")
    return (parts.netloc.lower().removeprefix("www.") + parts.path.rstrip("/")).lower()


def title_key(title):
    return re.sub(r"[\W_]+", " ", title.casefold()).strip()


def classify(title):
    """(Stufe, Hinweis) zu einem Titel: 3 wichtig, 2 interessant, 1 normal, 0 Kursgerede/Ratgeber."""
    best = (NORMAL, "")
    for level, tag, pattern in RULES:
        if level > best[0] and pattern.search(title):
            best = (level, tag)
    if best[0] == NORMAL and NOISE_PATTERN.search(title):
        return NOISE, ""
    return best


def merge(news_by_symbol, terms_for=None):
    """Führt die News mehrerer Aktien zusammen. news_by_symbol: {Kürzel: [(titel, quelle, datum, link)]}.
    terms_for(symbol) liefert die Suchbegriffe für den Filter; ohne sie wird nicht gefiltert. Dieselbe Meldung
    (gleiche Adresse oder gleicher Titel) erscheint einmal mit allen Kürzeln."""
    by_link, by_title, items = {}, {}, []
    for symbol, news in news_by_symbol.items():
        if terms_for is not None:
            news = kw.filter_news(news, symbol, terms_for(symbol))
        for title, source, published, link in news:
            keys = (("link", link_key(link)), ("title", title_key(title)))
            item = by_link.get(keys[0][1]) if keys[0][1] else None
            item = item or by_title.get(keys[1][1])
            if item is None:
                level, tag = classify(title)
                item = FeedItem(title, source, published, link, [], level, tag)
                items.append(item)
            if symbol not in item.symbols:
                item.symbols.append(symbol)
            if keys[0][1]:
                by_link[keys[0][1]] = item
            by_title[keys[1][1]] = item
    return items


def attach_events(items, events_by_symbol, today=None):
    """Hängt an Meldungen, die zu einem bekannten Termin der Aktie passen, einen Hinweis darauf. events_by_symbol:
    {Kürzel: [Event]} (aus dem Kalender)."""
    today = today or dt.date.today()
    for item in items:
        kinds = TAG_KINDS.get(item.tag)
        if not kinds or not item.published:
            continue
        published = item.published.date()
        margin = dt.timedelta(days=EVENT_WINDOW_DAYS)
        for symbol in item.symbols:
            for event in events_by_symbol.get(symbol, []):
                if event.kind in kinds and event.day - margin <= published <= event.end + margin:
                    status = evt.STATUS_LABELS[evt.effective_status(event, today)]
                    prefix = f"{symbol}: " if len(item.symbols) > 1 else ""
                    item.event_note = f"Termin {prefix}{event.title}, {evt.date_text(event)} ({status})"
                    break
            if item.event_note:
                break
    return items


@dataclass
class Feed:
    items: list           # was gezeigt wird, in der Reihenfolge der Anzeige
    minor_hidden: int     # wegen Kursgerede und Ratgeber ausgeblendet
    old_hidden: int       # älter als der gewählte Zeitraum
    more: int             # über dem Limit


def build(items, now=None, hours=DEFAULT_HOURS, limit=DEFAULT_LIMIT, show_minor=False):
    """Wählt aus den Meldungen aus und ordnet sie: wichtige zuerst, innerhalb einer Stufe die neuesten.
    hours None zeigt jede Zeit; limit None zeigt alle."""
    now = now or dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(hours=hours) if hours else None
    recent, old_hidden = [], 0
    for item in items:
        if cutoff and item.published and item.published < cutoff:
            old_hidden += 1
        else:
            recent.append(item)
    minor_hidden = 0
    shown = []
    for item in recent:
        if item.importance == NOISE and not show_minor:
            minor_hidden += 1
        else:
            shown.append(item)
    oldest = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    shown.sort(key=lambda item: (item.importance, item.published or oldest), reverse=True)
    more = max(len(shown) - limit, 0) if limit else 0
    return Feed(shown[:limit] if limit else shown, minor_hidden, old_hidden, more)


def importance_label(item):
    return {3: "wichtig", 2: "interessant", 1: "", 0: "Kursgerede"}[item.importance]
