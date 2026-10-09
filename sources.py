"""Anbindungen für Transaktionen von außen (zum Beispiel eToro): Schnittstelle und Import-Planung.

Eine Anbindung liefert ExternalTrade-Einträge. plan_import() entscheidet vorab und ohne Datenbank,
was davon gebucht wird, und gibt auch zurück, was nicht gebucht wird und warum:

- schon vorhanden: dieselbe Kennung der Anbindung wurde früher schon gebucht (ein erneuter Abgleich
  bucht nichts doppelt),
- Kürzel unbekannt: der Anbieter nennt ein Kürzel, das weder zugeordnet noch bekannt ist,
- ungültig: der Eintrag würde den Verlauf ungültig machen (zum Beispiel ein Verkauf ohne Bestand),
- ersetzt: ein Startbestand wird durch die echten Käufe ersetzt, wenn das ausdrücklich verlangt wird.

Konkrete Anbindung: etoro.py. Eine neue Anbindung erbt von TransactionSource und
muss nur fetch() umsetzen; der Rest (Dubletten, Zuordnung, Prüfung, Speicherung, Anzeige der Herkunft) ist da.
"""
import datetime as dt
from dataclasses import dataclass, field

import ledger


@dataclass(frozen=True)
class ExternalTrade:
    """Ein Kauf oder Verkauf, wie ihn eine Anbindung meldet."""
    external_id: str        # eindeutige Kennung beim Anbieter
    symbol: str             # Kürzel beim Anbieter (kann vom Yahoo-Kürzel abweichen)
    kind: str               # "buy" oder "sell"
    shares: float
    price: float            # in der Währung der Aktie
    day: dt.date
    fee: float = 0.0
    currency: str = ""      # nur zur Information
    note: str = ""


@dataclass(frozen=True)
class SyncBatch:
    trades: tuple
    cursor: str = ""        # Merkzettel für den nächsten Abruf, zum Beispiel der letzte Zeitstempel
    notes: tuple = ()       # Hinweise der Anbindung (zum Beispiel übersprungene Einträge), erscheinen im Abgleichstand


class TransactionSource:
    """Basisklasse einer Anbindung. fetch() darf Netzwerk benutzen und wird im Hintergrund aufgerufen."""
    name = ""               # kurzer Schlüssel, steht in der Datenbank (zum Beispiel "etoro")
    label = ""              # Anzeigename (zum Beispiel "eToro")

    def fetch(self, cursor):
        """Liefert einen SyncBatch mit allen Einträgen seit cursor (leer: alles)."""
        raise NotImplementedError


@dataclass
class ImportPlan:
    new: list = field(default_factory=list)         # [(Yahoo-Kürzel, ExternalTrade)], zeitlich geordnet
    duplicates: int = 0
    unmapped: dict = field(default_factory=dict)    # Kürzel beim Anbieter -> Anzahl der Einträge
    rejected: list = field(default_factory=list)    # [(ExternalTrade, Grund)]
    remove_ids: list = field(default_factory=list)  # Startbestand-Einträge, die ersetzt werden


def plan_import(source, trades, existing, aliases, known_symbols, replace_openings=False):
    """Plant den Import. existing: vorhandene ledger.Transaction; aliases: Kürzel beim Anbieter -> Yahoo-Kürzel;
    known_symbols: Kürzel, die ohne Zuordnung gelten (Watchlist und Aktien mit Transaktionen)."""
    plan = ImportPlan()
    seen = {t.external_id for t in existing if t.source == source and t.external_id}
    held = {}
    for tx in existing:
        held.setdefault(tx.symbol, []).append(tx)

    candidates = []
    for trade in trades:
        if trade.external_id in seen:
            plan.duplicates += 1
            continue
        seen.add(trade.external_id)
        symbol = aliases.get(trade.symbol) or (trade.symbol.upper() if trade.symbol.upper() in known_symbols else None)
        if symbol is None:
            plan.unmapped[trade.symbol] = plan.unmapped.get(trade.symbol, 0) + 1
            continue
        candidates.append((symbol, trade))

    replaced = set()
    if replace_openings:
        for symbol in {s for s, t in candidates if t.kind == "buy"}:
            for tx in held.get(symbol, []):
                if ledger.is_opening(tx):
                    replaced.add(tx.id)
                    plan.remove_ids.append(tx.id)

    # In zeitlicher Reihenfolge prüfen (am selben Tag Käufe vor Verkäufen), jeden Eintrag im Zusammenhang
    candidates.sort(key=lambda c: (c[1].day, c[1].kind != "buy", c[1].external_id))
    accepted = {}
    for number, (symbol, trade) in enumerate(candidates, start=1):
        trial = ledger.Transaction(-number, symbol, trade.kind, trade.shares, trade.price, trade.fee, trade.day,
                                   trade.note, source, trade.external_id)
        history = [t for t in held.get(symbol, []) if t.id not in replaced] + accepted.get(symbol, []) + [trial]
        try:
            ledger.replay(history)
        except ValueError as exc:
            plan.rejected.append((trade, str(exc)))
            continue
        accepted.setdefault(symbol, []).append(trial)
        plan.new.append((symbol, trade))
    return plan
