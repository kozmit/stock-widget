"""SQLite-Speicher für Watchlist und Transaktionen.

Gespeichert werden nur Rohdaten. Bestände, Einstandskurse und Gewinne entstehen in ledger.py.
"""
import datetime as dt
import json
import os
import shutil
import sqlite3

from events import Event
from ledger import Transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist (
    symbol TEXT PRIMARY KEY,
    sort INTEGER NOT NULL,
    opening_realized REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('buy', 'sell')),
    shares REAL NOT NULL CHECK (shares > 0),
    price REAL NOT NULL CHECK (price >= 0),  -- 0 nur für zugeteilte Aktien, das prüft ledger.validate
    fee REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
    executed_on TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    external_id TEXT,
    imported_at TEXT
);
CREATE INDEX IF NOT EXISTS transactions_symbol ON transactions (symbol);
CREATE TABLE IF NOT EXISTS price_snapshots (
    symbol TEXT PRIMARY KEY,
    price REAL NOT NULL,
    change_pct REAL NOT NULL,
    currency TEXT NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS instruments (
    symbol TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    exchange TEXT NOT NULL DEFAULT '',
    currency TEXT NOT NULL DEFAULT '',
    sector TEXT NOT NULL DEFAULT '',
    industry TEXT NOT NULL DEFAULT '',
    country TEXT NOT NULL DEFAULT '',
    isin TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS fx_rates (
    currency TEXT NOT NULL,
    day TEXT NOT NULL,
    rate REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (currency, day)
);
CREATE TABLE IF NOT EXISTS price_history (
    symbol TEXT NOT NULL,
    day TEXT NOT NULL,
    close REAL NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (symbol, day)
);
CREATE TABLE IF NOT EXISTS symbol_aliases (
    source TEXT NOT NULL,
    source_symbol TEXT NOT NULL,
    symbol TEXT NOT NULL,
    PRIMARY KEY (source, source_symbol)
);
CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT PRIMARY KEY,
    cursor TEXT,
    last_sync_at TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fx_latest (
    currency TEXT PRIMARY KEY,
    rate REAL NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    day TEXT NOT NULL,
    end_day TEXT NOT NULL,
    precision TEXT NOT NULL DEFAULT 'day' CHECK (precision IN ('day', 'month', 'year')),
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'expected', 'speculative', 'occurred')),
    source TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    external_id TEXT,
    relevance INTEGER NOT NULL DEFAULT 2 CHECK (relevance BETWEEN 1 AND 3),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_symbol ON events (symbol);
CREATE UNIQUE INDEX IF NOT EXISTS events_external ON events (source, symbol, external_id) WHERE external_id IS NOT NULL;
"""
# 1: Watchlist und Transaktionen. 2: dazu letzter Kurs und Stammdaten je Aktie, jeweils mit Quelle und Zeitpunkt.
# 3: dazu Wechselkurse in die Basiswährung (Tageskurse und letzter Kurs).
# 4: Transaktionen kennen ihre Herkunft (manual, opening, etoro, ...) und die Kennung beim Anbieter;
#    dazu Tageskurse der Aktien (für den Verlauf), Kürzel-Zuordnungen und der Stand der Synchronisation.
# 5: Termine (Ereigniskalender) mit Art, Zeitraum, Status, Quelle und Relevanz.
SCHEMA_VERSION = "5"
EVENT_FIELDS = ("symbol", "kind", "title", "day", "end", "precision", "status", "relevance", "note", "source_url")
INSTRUMENT_FIELDS = ("name", "exchange", "currency", "sector", "industry", "country", "isin")
DEFAULT_SYMBOLS = ["AAPL", "MSFT", "DELL"]


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self._upgrade()
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
        self.db.commit()

    def _upgrade(self):
        """Hebt eine ältere Datenbank an. Neue Tabellen legt SCHEMA an; hier kommen neue Spalten und
        Umbenennungen dazu. Vorhandene Daten bleiben unverändert."""
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(transactions)")}
        for name, ddl in (("source", "TEXT NOT NULL DEFAULT 'manual'"), ("external_id", "TEXT"),
                          ("imported_at", "TEXT")):
            if name not in columns:
                self.db.execute(f"ALTER TABLE transactions ADD COLUMN {name} {ddl}")
        self._allow_free_allocations()
        # Startbestände waren bisher nur an der Notiz zu erkennen
        self.db.execute("UPDATE transactions SET source = 'opening' WHERE source = 'manual' AND note LIKE 'Startbestand%'")
        # Eine Kennung beim Anbieter darf nur einmal vorkommen, sonst würde ein erneuter Abgleich doppelt buchen.
        self.db.execute("CREATE UNIQUE INDEX IF NOT EXISTS transactions_external ON transactions (source, external_id) "
                        "WHERE external_id IS NOT NULL")

    TRANSACTION_COLUMNS = ("id, symbol, kind, shares, price, fee, executed_on, note, created_at, source, "
                           "external_id, imported_at")

    def _allow_free_allocations(self):
        """Ältere Datenbanken verlangen price > 0. Zugeteilte Aktien (Abspaltung) haben den Einstand 0, deshalb wird
        die Tabelle einmal mit price >= 0 neu angelegt; alle Einträge samt Nummern bleiben erhalten."""
        sql = self.db.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'transactions'").fetchone()[0]
        if "price > 0" not in sql:
            return
        self.db.commit()
        self.db.execute("ALTER TABLE transactions RENAME TO transactions_old")
        self.db.execute("DROP INDEX IF EXISTS transactions_symbol")
        self.db.execute("DROP INDEX IF EXISTS transactions_external")
        self.db.execute(sql.replace("price > 0", "price >= 0"))
        self.db.execute(f"INSERT INTO transactions ({self.TRANSACTION_COLUMNS}) "
                        f"SELECT {self.TRANSACTION_COLUMNS} FROM transactions_old")
        self.db.execute("DROP TABLE transactions_old")
        self.db.execute("CREATE INDEX IF NOT EXISTS transactions_symbol ON transactions (symbol)")
        self.db.commit()

    def close(self):
        self.db.close()

    # -- Meta --
    def meta(self, key):
        row = self.db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set_meta(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (key, value))
        self.db.commit()

    # -- Watchlist --
    def symbols(self):
        return [r[0] for r in self.db.execute("SELECT symbol FROM watchlist ORDER BY sort")]

    def opening_realized(self):
        return dict(self.db.execute("SELECT symbol, opening_realized FROM watchlist"))

    def add_symbol(self, symbol, opening_realized=0.0):
        sort = self.db.execute("SELECT COALESCE(MAX(sort), 0) + 1 FROM watchlist").fetchone()[0]
        self.db.execute("INSERT OR IGNORE INTO watchlist VALUES (?, ?, ?)", (symbol, sort, opening_realized))
        self.db.commit()

    def remove_symbol(self, symbol):
        """Entfernt nur aus der Watchlist; Transaktionen bleiben gespeichert."""
        self.db.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        self.db.commit()

    # -- Transaktionen --
    def transactions(self):
        rows = self.db.execute(
            "SELECT id, symbol, kind, shares, price, fee, executed_on, note, source, external_id "
            "FROM transactions ORDER BY id")
        return [Transaction(r[0], r[1], r[2], r[3], r[4], r[5], dt.date.fromisoformat(r[6]), r[7], r[8], r[9] or "")
                for r in rows]

    def add_transaction(self, symbol, kind, shares, price, fee, day, note="", source="manual", external_id=None):
        now = dt.datetime.now().isoformat(timespec="seconds")
        cursor = self.db.execute(
            "INSERT INTO transactions (symbol, kind, shares, price, fee, executed_on, note, created_at, "
            "source, external_id, imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, kind, shares, price, fee, day.isoformat(), note, now, source, external_id,
             now if external_id else None))
        self.db.commit()
        return cursor.lastrowid

    def external_ids(self, source):
        """Kennungen beim Anbieter, die schon gespeichert sind."""
        rows = self.db.execute("SELECT external_id FROM transactions WHERE source = ? AND external_id IS NOT NULL",
                               (source,))
        return {r[0] for r in rows}

    def delete_transactions(self, ids):
        self.db.executemany("DELETE FROM transactions WHERE id = ?", [(i,) for i in ids])
        self.db.commit()

    def delete_transaction(self, transaction_id):
        self.db.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
        self.db.commit()

    # -- Letzter Kurs und Stammdaten (mit Quelle und Zeitpunkt des Abrufs) --
    def save_snapshot(self, symbol, quote, fetched_at):
        self.db.execute(
            "INSERT OR REPLACE INTO price_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, quote["price"], quote["change_pct"], quote["currency"], quote.get("source", ""),
             fetched_at.isoformat(timespec="seconds")))
        self.db.commit()

    def snapshots(self):
        rows = self.db.execute(
            "SELECT symbol, price, change_pct, currency, source, fetched_at FROM price_snapshots")
        return {r[0]: {"price": r[1], "change_pct": r[2], "currency": r[3], "source": r[4],
                       "fetched_at": dt.datetime.fromisoformat(r[5])} for r in rows}

    def save_instrument(self, symbol, info, fetched_at):
        values = [info.get(field) or "" for field in INSTRUMENT_FIELDS]
        self.db.execute(
            "INSERT OR REPLACE INTO instruments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, *values, info.get("source", ""), fetched_at.isoformat(timespec="seconds")))
        self.db.commit()

    def instruments(self):
        rows = self.db.execute(
            "SELECT symbol, name, exchange, currency, sector, industry, country, isin, source, fetched_at "
            "FROM instruments")
        return {r[0]: {**dict(zip(INSTRUMENT_FIELDS, r[1:8])), "source": r[8],
                       "fetched_at": dt.datetime.fromisoformat(r[9])} for r in rows}

    # -- Tageskurse der Aktien (für den Verlauf des Portfolios) --
    def save_closes(self, symbol, closes, source):
        self.db.executemany("INSERT OR REPLACE INTO price_history VALUES (?, ?, ?, ?)",
                            [(symbol, day.isoformat(), close, source) for day, close in closes.items()])
        self.db.commit()

    def closes(self):
        history = {}
        for symbol, day, close in self.db.execute("SELECT symbol, day, close FROM price_history"):
            history.setdefault(symbol, {})[dt.date.fromisoformat(day)] = close
        return history

    # -- Anbindungen: Kürzel-Zuordnung und Stand der Synchronisation --
    def aliases(self, source):
        return dict(self.db.execute("SELECT source_symbol, symbol FROM symbol_aliases WHERE source = ?", (source,)))

    def set_alias(self, source, source_symbol, symbol):
        self.db.execute("INSERT OR REPLACE INTO symbol_aliases VALUES (?, ?, ?)", (source, source_symbol, symbol))
        self.db.commit()

    def sync_state(self, source):
        row = self.db.execute("SELECT cursor, last_sync_at, message FROM sync_state WHERE source = ?",
                              (source,)).fetchone()
        return {"cursor": row[0], "last_sync_at": dt.datetime.fromisoformat(row[1]), "message": row[2]} if row else None

    def save_sync_state(self, source, cursor, when, message=""):
        self.db.execute("INSERT OR REPLACE INTO sync_state VALUES (?, ?, ?, ?)",
                        (source, cursor, when.isoformat(timespec="seconds"), message))
        self.db.commit()

    # -- Termine (Ereigniskalender) --
    @staticmethod
    def _event_row(fields):
        return (fields["symbol"], fields["kind"], fields["title"], fields["day"].isoformat(),
                fields["end"].isoformat(), fields["precision"], fields["status"], fields["relevance"],
                fields.get("note", ""), fields.get("source_url", ""))

    def add_event(self, fields, source="manual", external_id=None):
        """Legt einen Termin an; fields siehe events.make_event. Gibt die Nummer zurück."""
        now = dt.datetime.now().isoformat(timespec="seconds")
        cursor = self.db.execute(
            "INSERT INTO events (symbol, kind, title, day, end_day, precision, status, relevance, note, source_url, "
            "source, external_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (*self._event_row(fields), source, external_id, now, now))
        self.db.commit()
        return cursor.lastrowid

    def update_event(self, event_id, fields):
        row = self._event_row(fields)
        self.db.execute(
            "UPDATE events SET symbol = ?, kind = ?, title = ?, day = ?, end_day = ?, precision = ?, status = ?, "
            "relevance = ?, note = ?, source_url = ?, updated_at = ? WHERE id = ?",
            (*row, dt.datetime.now().isoformat(timespec="seconds"), event_id))
        self.db.commit()

    def upsert_event(self, source, external_id, fields):
        """Legt einen Termin einer Quelle an oder aktualisiert ihn (Schlüssel: Quelle, Aktie, Kennung).
        Gibt (Nummer, geändert) zurück; ein unveränderter Termin wird nicht angefasst."""
        found = self.db.execute("SELECT id, kind, title, day, end_day, precision, status, relevance, note, source_url "
                                "FROM events WHERE source = ? AND symbol = ? AND external_id = ?",
                                (source, fields["symbol"], external_id)).fetchone()
        if found is None:
            return self.add_event(fields, source, external_id), True
        if tuple(found[1:]) == (fields["kind"], fields["title"], fields["day"].isoformat(), fields["end"].isoformat(),
                                fields["precision"], fields["status"], fields["relevance"], fields.get("note", ""),
                                fields.get("source_url", "")):
            return found[0], False
        self.update_event(found[0], fields)
        return found[0], True

    def delete_event(self, event_id):
        self.db.execute("DELETE FROM events WHERE id = ?", (event_id,))
        self.db.commit()

    def delete_stale_events(self, source, symbol, kind, keep_external_ids, from_day):
        """Löscht künftige Termine einer Quelle und Art, die sie nicht mehr meldet (der Termin hat sich verschoben).
        Vergangene bleiben stehen. Gibt die Zahl der gelöschten zurück."""
        rows = self.db.execute("SELECT id, external_id FROM events WHERE source = ? AND symbol = ? AND kind = ? "
                               "AND external_id IS NOT NULL AND day >= ?",
                               (source, symbol, kind, from_day.isoformat())).fetchall()
        stale = [row[0] for row in rows if row[1] not in keep_external_ids]
        self.db.executemany("DELETE FROM events WHERE id = ?", [(i,) for i in stale])
        self.db.commit()
        return len(stale)

    def events(self):
        rows = self.db.execute("SELECT id, symbol, kind, title, day, end_day, precision, status, source, source_url, "
                               "external_id, relevance, note FROM events ORDER BY day, id")
        return [Event(r[0], r[1], r[2], r[3], dt.date.fromisoformat(r[4]), dt.date.fromisoformat(r[5]), r[6], r[7],
                      r[8], r[9], r[10] or "", r[11], r[12]) for r in rows]

    # -- Wechselkurse (Basiswährung je Einheit Fremdwährung) --
    def clear_fx(self):
        """Löscht alle gespeicherten Wechselkurse (nach dem Wechsel der Basiswährung)."""
        self.db.execute("DELETE FROM fx_rates")
        self.db.execute("DELETE FROM fx_latest")
        self.db.commit()

    def save_fx_rates(self, currency, rates, source):
        self.db.executemany(
            "INSERT OR REPLACE INTO fx_rates VALUES (?, ?, ?, ?)",
            [(currency, day.isoformat(), rate, source) for day, rate in rates.items()])
        self.db.commit()

    def fx_rates(self):
        history = {}
        for currency, day, rate in self.db.execute("SELECT currency, day, rate FROM fx_rates"):
            history.setdefault(currency, {})[dt.date.fromisoformat(day)] = rate
        return history

    def save_fx_latest(self, currency, rate, fetched_at, source):
        self.db.execute("INSERT OR REPLACE INTO fx_latest VALUES (?, ?, ?, ?)",
                        (currency, rate, source, fetched_at.isoformat(timespec="seconds")))
        self.db.commit()

    def fx_latest(self):
        rows = self.db.execute("SELECT currency, rate, source, fetched_at FROM fx_latest")
        return {r[0]: {"rate": r[1], "source": r[2], "fetched_at": dt.datetime.fromisoformat(r[3])} for r in rows}

    # -- Übernahme der bisherigen Daten (watchlist.json) --
    def import_legacy(self, json_path, today=None):
        """Übernimmt einmalig die alte watchlist.json. Positionen werden zu einem Startbestand-Kauf.
        Die Datei wird vorher gesichert und danach nicht mehr verändert."""
        if self.meta("legacy_imported"):
            return
        today = today or dt.date.today()
        if os.path.exists(json_path):
            backup = json_path + ".bak"
            if not os.path.exists(backup):
                shutil.copy2(json_path, backup)
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                symbols, positions = [s for s in data if isinstance(s, str)], {}
            else:
                symbols = [s for s in data.get("symbols", []) if isinstance(s, str)]
                positions = data.get("positions", {})
        else:
            symbols, positions = list(DEFAULT_SYMBOLS), {}
        for symbol in symbols:
            position = positions.get(symbol)
            self.add_symbol(symbol, position.get("realized", 0.0) if position else 0.0)
            if position and position.get("shares", 0) > 0 and position.get("cost", 0) > 0:
                self.add_transaction(symbol, "buy", position["shares"], position["cost"], 0.0, today,
                                     "Startbestand (aus dem bisherigen Widget übernommen)", source="opening")
        self.set_meta("legacy_imported", today.isoformat())
