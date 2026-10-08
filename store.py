"""SQLite-Speicher für Watchlist und Transaktionen.

Gespeichert werden nur Rohdaten. Bestände, Einstandskurse und Gewinne entstehen in ledger.py.
"""
import datetime as dt
import json
import os
import shutil
import sqlite3

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
    price REAL NOT NULL CHECK (price > 0),
    fee REAL NOT NULL DEFAULT 0 CHECK (fee >= 0),
    executed_on TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
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
CREATE TABLE IF NOT EXISTS fx_latest (
    currency TEXT PRIMARY KEY,
    rate REAL NOT NULL,
    source TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""
# 1: Watchlist und Transaktionen. 2: dazu letzter Kurs und Stammdaten je Aktie, jeweils mit Quelle und Zeitpunkt.
# 3: dazu Wechselkurse in die Basiswährung (Tageskurse und letzter Kurs).
SCHEMA_VERSION = "3"
INSTRUMENT_FIELDS = ("name", "exchange", "currency", "sector", "industry", "country", "isin")
DEFAULT_SYMBOLS = ["AAPL", "MSFT", "DELL"]


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        # Neue Tabellen legt SCHEMA an; bestehende Daten bleiben unverändert, nur die Versionsnummer zieht nach.
        self.db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', ?)", (SCHEMA_VERSION,))
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
            "SELECT id, symbol, kind, shares, price, fee, executed_on, note FROM transactions ORDER BY id")
        return [Transaction(r[0], r[1], r[2], r[3], r[4], r[5], dt.date.fromisoformat(r[6]), r[7]) for r in rows]

    def add_transaction(self, symbol, kind, shares, price, fee, day, note=""):
        cursor = self.db.execute(
            "INSERT INTO transactions (symbol, kind, shares, price, fee, executed_on, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, kind, shares, price, fee, day.isoformat(), note, dt.datetime.now().isoformat(timespec="seconds")))
        self.db.commit()
        return cursor.lastrowid

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

    # -- Wechselkurse (Basiswährung je Einheit Fremdwährung) --
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
                                     "Startbestand (aus dem bisherigen Widget übernommen)")
        self.set_meta("legacy_imported", today.isoformat())
