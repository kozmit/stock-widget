"""Suchbegriffe je Aktie und der News-Filter, der sie nutzt, ohne jede Oberfläche.

Yahoo ordnet News einer Aktie grob zu: Ratgeber-Artikel und Listen mit vielen Kürzeln tragen auch das Kürzel
dieser Aktie. Eine News gilt hier nur als passend, wenn ihr Titel das Kürzel oder einen der Begriffe nennt, die zur
Firma gehören (Name, Marken, Produkte, Gründer). Die Begriffe sammelt `collect` automatisch: Stammdaten von Yahoo
und die Claude-Code-CLI (`claude -p`) als Sprachmodell. Ohne CLI bleibt der Firmenname."""
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import tempfile

import stock_data as sd

MAX_AGE = dt.timedelta(days=90)     # so lange gelten Begriffe, die das Modell geliefert hat
RETRY_AGE = dt.timedelta(days=1)    # ohne Modell (CLI fehlt, Fehler) wird früher noch einmal versucht
SOURCE_MODEL, SOURCE_NAME = "Yahoo + Claude", "Yahoo"
MODEL = "haiku"
TIMEOUT = 120                       # Sekunden für einen Aufruf der CLI
MAX_TERMS = 30
CORPORATE_WORDS = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc", "ag",
                   "se", "sa", "nv", "llc", "lp", "holdings", "holding", "group", "class", "ordinary", "shares",
                   "common", "stock", "the"}

PROMPT = """Du bekommst die Stammdaten einer börsennotierten Firma. Nenne Suchbegriffe, an denen man in \
Nachrichten-Titeln erkennt, dass es um diese Firma geht.

Firma: {name} (Kürzel {symbol})
Branche: {industry}
Website: {website}
Führungskräfte laut Yahoo: {officers}
Beschreibung: {summary}

Regeln:
- Antworte NUR mit einem JSON-Array aus Strings, ohne Text davor oder danach.
- 8 bis 25 kurze Begriffe: Firmen- und Markenname, Produkte, Spiele, Dienste, Töchter, Gründer und aktueller \
CEO (nur wenn sie in Schlagzeilen oft mit der Firma genannt werden), gängige Kurzformen und Schreibweisen.
- Keine Mitbewerber, keine Branchenwörter ("Aktie", "Software", "stock"), kein Kürzel.
- Prüfe jeden Begriff: Handelt ein Artikel, dessen Titel dieses Wort enthält, praktisch immer von dieser Firma? \
Wenn nicht, lass ihn weg. Das gilt für alltägliche Wörter ("Karma", "Mafia", "Civilization", "Block", "Target"), \
Abkürzungen mit zweitem Sinn ("AMA") und einzelne Vornamen."""


def clean_name(name):
    """„Reddit, Inc.“ → „Reddit“: der Firmenname ohne Rechtsform."""
    words = re.findall(r"[^\s,]+", (name or "").replace("&", " & "))
    while words and words[-1].strip(".").lower() in CORPORATE_WORDS:
        words.pop()
    return " ".join(words).strip()


def name_terms(name):
    """Begriffe allein aus dem Firmennamen: der Rückfall, solange das Modell nichts geliefert hat."""
    cleaned = clean_name(name)
    return [cleaned] if cleaned else []


def normalize(terms, symbol=""):
    """Bereinigt eine Begriffsliste: nur Text, ohne Dubletten (Groß/Klein egal), ohne das Kürzel selbst."""
    base = symbol.split(".")[0].lower()
    seen, result = set(), []
    for term in terms or []:
        if not isinstance(term, str):
            continue
        term = " ".join(term.split())
        key = term.lower()
        if len(term) < 3 or len(term) > 60 or key in seen or key == base or not re.search(r"\w", term):
            continue
        seen.add(key)
        result.append(term)
    return result[:MAX_TERMS]


def parse_terms(text):
    """Liest das JSON-Array aus der Antwort des Modells (auch wenn es in einem Codeblock steht)."""
    match = re.search(r"\[.*\]", text or "", re.S)
    if not match:
        raise ValueError("Antwort ohne Liste")
    data = json.loads(match.group(0))
    if not isinstance(data, list):
        raise ValueError("Antwort ist keine Liste")
    return [item for item in data if isinstance(item, str)]


def fetch_sources(symbol):
    """Stammdaten für die Begriffssuche laut Yahoo."""
    info = sd.yf.Ticker(symbol).info or {}
    name = info.get("longName") or info.get("shortName")
    if not name:
        raise ValueError("keine Stammdaten")
    officers = []
    for officer in (info.get("companyOfficers") or [])[:6]:
        person = " ".join((officer.get("name") or "").split())
        if person:
            officers.append(f"{person} ({officer['title']})" if officer.get("title") else person)
    return {"name": name, "industry": info.get("industry") or "", "website": info.get("website") or "",
            "officers": officers, "summary": (info.get("longBusinessSummary") or "")[:1500]}


def claude_command():
    """Pfad der Claude-Code-CLI oder None. Ein per Autostart gestartetes Widget hat oft einen kürzeren PATH,
    deshalb wird zusätzlich der Standardort der CLI geprüft."""
    found = shutil.which("claude")
    if found:
        return found
    for candidate in (os.path.expanduser(r"~\.local\bin\claude.exe"), os.path.expanduser("~/.local/bin/claude")):
        if os.path.isfile(candidate):
            return candidate
    return None


def ask_model(symbol, sources):
    """Fragt das Modell nach Begriffen. Wirft RuntimeError, wenn die CLI fehlt oder fehlschlägt."""
    command = claude_command()
    if not command:
        raise RuntimeError("Claude-Code-CLI nicht gefunden")
    prompt = PROMPT.format(name=sources["name"], symbol=symbol, industry=sources["industry"] or "unbekannt",
                           website=sources["website"] or "unbekannt",
                           officers="; ".join(sources["officers"]) or "unbekannt",
                           summary=sources["summary"] or "keine")
    args = [command, "-p", prompt, "--model", MODEL, "--tools", "", "--no-session-persistence",
            "--disable-slash-commands", "--strict-mcp-config", "--output-format", "text"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        # In einem leeren Ordner, damit keine Projektdateien (CLAUDE.md) in die Anfrage geraten
        with tempfile.TemporaryDirectory() as folder:
            done = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=TIMEOUT,
                                  cwd=folder, stdin=subprocess.DEVNULL, creationflags=flags)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Claude-Code-CLI nicht ausführbar: {exc}") from exc
    if done.returncode != 0:
        raise RuntimeError(f"Claude-Code-CLI Fehler {done.returncode}: {(done.stderr or done.stdout)[:200]}")
    return parse_terms(done.stdout)


def collect(symbol):
    """Sammelt die Begriffe einer Aktie: {"terms": [...], "source": SOURCE_MODEL | SOURCE_NAME}.
    Wirft nur, wenn Yahoo gar keine Stammdaten kennt."""
    sources = fetch_sources(symbol)
    base = name_terms(sources["name"])
    try:
        from_model = ask_model(symbol, sources)
    except (RuntimeError, ValueError):
        return {"terms": normalize(base, symbol), "source": SOURCE_NAME}
    return {"terms": normalize(base + from_model, symbol), "source": SOURCE_MODEL}


def needs_refresh(entry, now=None):
    """entry ist der gespeicherte Stand {"source", "fetched_at", ...} oder None."""
    if entry is None:
        return True
    age = (now or dt.datetime.now()) - entry["fetched_at"]
    return age > (MAX_AGE if entry["source"] == SOURCE_MODEL else RETRY_AGE)


def _term_pattern(term):
    # Buchstaben- und Zifferngruppen dürfen durch beliebige Trenner (oder keinen) getrennt sein: „GTA 6“ = „GTA6“,
    # „Take-Two“ = „Take Two“. Davor und danach darf kein weiteres Wort-Zeichen stehen.
    parts = re.findall(r"[^\W\d_]+|\d+", term)
    return re.compile(r"(?<![^\W_])" + r"[\W_]*".join(map(re.escape, parts)) + r"(?![^\W_])", re.I)


def is_relevant(title, symbol, terms):
    """Nennt der Titel das Kürzel (genau so geschrieben) oder einen der Begriffe?"""
    base = symbol.split(".")[0]
    if len(base) >= 3 and re.search(rf"(?<![A-Za-z0-9]){re.escape(base)}(?![A-Za-z0-9])", title):
        return True
    return any(_term_pattern(term).search(title) for term in terms if re.search(r"\w", term))


def filter_news(news, symbol, terms):
    """News [(titel, quelle, datum, link)], reduziert auf die, deren Titel zur Aktie passt. Ohne Begriffe und
    mit einem zu kurzen Kürzel gibt es nichts, woran man filtern könnte; dann bleibt die Liste unverändert."""
    if not terms and len(symbol.split(".")[0]) < 3:
        return list(news)
    return [item for item in news if is_relevant(item[0], symbol, terms)]
