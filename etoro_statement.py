"""Import des eToro-Kontoauszugs (.xlsx): holt die Positionen nach, die die Schnittstelle nicht mehr liefert.

Die eToro-Schnittstelle reicht nur etwa ein Jahr zurück. Der Kontoauszug (eToro: Portfolio > Verlauf > Kontoauszug)
enthält dagegen alle geschlossenen Positionen ab Kontoeröffnung. Dieses Modul macht daraus dieselben ExternalTrade-
Einträge wie etoro.build_trades, mit denselben Kennungen ("<Positions-ID>:open" und ":close", Anbindung "etoro").
Was die Schnittstelle schon gebucht hat, wird deshalb als vorhanden erkannt und nicht doppelt gebucht.

Berücksichtigt werden Long-Positionen in Aktien, ETFs und CFDs; gehebelte werden in einen gleichwertigen Kauf und
Verkauf umgerechnet (etoro.leveraged_equivalent). Leerverkäufe kennt das Hauptbuch nicht.
Eine Position, die ohne Kaufbetrag eröffnet wurde (Abspaltung, Zuteilung), kostet nichts: Einstand 0, der ganze
Wert ist Gewinn (wie bei eToro).
Dividenden, Gebühren und Kapitalmaßnahmen kennt das Hauptbuch nicht und werden nicht übernommen.

Aufruf (Probelauf ohne Schreiben, dann mit --apply):
    python etoro_statement.py <Datei.xlsx> [--apply]
"""
import datetime as dt
import re
import sys

import etoro
import xlsx_reader
from sources import ExternalTrade

SOURCE = "etoro"
# Notierungswährung bei eToro -> Yahoo-Endung für Kürzel ohne Endung (dort steht die Börse nicht im Kürzel)
CURRENCY_SUFFIX = {"GBX": "L", "GBP": "L", "AUD": "AX"}
SECURITY_TYPES = {"aktien", "etf", "stocks", "cfd"}  # CFDs nur als Long, siehe etoro.leveraged_equivalent


class StatementError(Exception):
    pass


def _sheet(book, prefix):
    for name, rows in book.items():
        if name.startswith(prefix):
            return rows
    raise StatementError(f"Im Kontoauszug fehlt das Blatt „{prefix} …“. Ist es der eToro-Kontoauszug als Excel-Datei?")


def _table(rows, required):
    """Zeilen eines Blatts als Dicts nach den Spaltenüberschriften der ersten Zeile."""
    header = rows[0] if rows else []
    missing = [name for name in required if name not in header]
    if missing:
        raise StatementError(f"Im Kontoauszug fehlen die Spalten {', '.join(missing)}.")
    return [dict(zip(header, row + [None] * (len(header) - len(row)))) for row in rows[1:] if row]


def parse_day(text):
    """'04/05/2026 13:30:08' (Tag/Monat/Jahr) -> Datum."""
    return dt.datetime.strptime(str(text).strip()[:10], "%d/%m/%Y").date()


def _number(text):
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _currencies(activity):
    """Positions-ID -> Notierungswährung, aus den Zeilen 'Position eröffnen' (Details wie 'SWDA/GBX')."""
    result = {}
    for row in activity:
        details = str(row.get("Details") or "")
        if row.get("Art") == "Position eröffnen" and "/" in details:
            result[str(row.get("Positions-ID"))] = details.rsplit("/", 1)[1].split()[0].upper()
    return result


def yahoo_symbol(etoro_symbol, currency=""):
    """eToro-Kürzel (mit Notierungswährung) -> Yahoo-Kürzel; ohne Börsenendung entscheidet die Währung."""
    symbol = etoro.yahoo_symbol(etoro_symbol)
    suffix = CURRENCY_SUFFIX.get(currency)
    return f"{symbol}.{suffix}" if suffix and "." not in symbol and "=" not in symbol else symbol


def read_statement(path):
    """Liest den Kontoauszug und gibt (Einträge, Hinweise) zurück."""
    return trades_from_book(xlsx_reader.read_workbook(path))


def trades_from_book(book):
    closed = _table(_sheet(book, "Geschlossene Positionen"),
                    ["Positions-ID", "Aktion", "Long / Short", "Einheiten", "Eröffnungsdatum", "Schließungsdatum",
                     "Hebel", "Eröffnungskurs", "Schlusskurs", "Art"])
    activity = _table(_sheet(book, "Kontoaktivität"), ["Art", "Details", "Betrag", "Einheiten", "Positions-ID", "Datum"])
    currencies = _currencies(activity)
    trades, notes, skipped = [], [], []
    closed_ids = set()

    for row in closed:
        key = str(row["Positions-ID"])
        closed_ids.add(key)
        name = str(row["Aktion"] or "")
        match = re.search(r"\(([^()]+)\)\s*$", name)
        leverage = int(_number(row["Hebel"])) or 1
        plain = row["Long / Short"] == "Long" and str(row["Art"] or "").lower() in SECURITY_TYPES
        if not plain or not match:
            skipped.append(name or key)
            continue
        symbol = yahoo_symbol(match.group(1), currencies.get(key, ""))
        shares, open_rate, close_rate = etoro.leveraged_equivalent(
            float(row["Einheiten"]), float(row["Eröffnungskurs"]), float(row["Schlusskurs"]), leverage)
        trades.append(ExternalTrade(f"{key}:open", symbol, "buy", shares, open_rate,
                                    parse_day(row["Eröffnungsdatum"]), note=etoro.leverage_note(leverage)))
        trades.append(ExternalTrade(f"{key}:close", symbol, "sell", shares, close_rate,
                                    parse_day(row["Schließungsdatum"]), note=etoro.leverage_note(leverage)))

    # Positionen ohne Kaufbetrag, die nicht geschlossen wurden: Abspaltungen und Zuteilungen
    for row in activity:
        key = str(row.get("Positions-ID"))
        if (row.get("Art") != "Position eröffnen" or key in closed_ids or _number(row.get("Betrag")) != 0
                or _number(row.get("Einheiten")) <= 0):
            continue
        if str(row.get("Anlagentyp") or "").lower() not in SECURITY_TYPES:
            continue
        symbol = yahoo_symbol(str(row["Details"]).rsplit("/", 1)[0], currencies.get(key, ""))
        day = parse_day(row["Datum"])
        trades.append(ExternalTrade(f"{key}:open", symbol, "buy", float(row["Einheiten"]), 0.0, day,
                                    note="Zuteilung ohne Kaufpreis, Einstand 0"))
        notes.append(f"{symbol}: Zuteilung ohne Kaufbetrag am {day:%d.%m.%Y}, Einstand 0 (der ganze Wert ist Gewinn)")
    if skipped:
        notes.append(f"{len(skipped)} geschlossene Position(en) übersprungen (Leerverkauf oder Krypto): "
                     + ", ".join(sorted(set(skipped))))
    return trades, notes


def apply_plan(store, plan, source=SOURCE):
    """Bucht den Plan (sources.plan_import) in die Datenbank, so wie es Controller.import_trades auch tut,
    aber ohne den Abgleichstand der Schnittstelle zu verändern."""
    for symbol, trade in plan.new:
        store.add_symbol(symbol)
        store.add_transaction(symbol, trade.kind, trade.shares, trade.price, trade.fee, trade.day, trade.note,
                              source, trade.external_id)


def describe(plan, notes):
    lines = [f"neu: {len(plan.new)}, schon vorhanden: {plan.duplicates}, abgelehnt: {len(plan.rejected)}, "
             f"unbekanntes Kürzel: {sum(plan.unmapped.values())}"]
    for symbol, trade in plan.new:
        lines.append(f"  + {trade.day:%d.%m.%Y} {trade.kind:4} {symbol:9} {trade.shares:>12.6f} x {trade.price:g}")
    for trade, reason in plan.rejected:
        lines.append(f"  ! abgelehnt {trade.symbol} {trade.external_id}: {reason}")
    for name, count in plan.unmapped.items():
        lines.append(f"  ? unbekanntes Kürzel {name} ({count})")
    lines.extend(f"  Hinweis: {note}" for note in notes)
    return "\n".join(lines)


def main(argv):
    import sources
    import stock_data as sd
    from store import Store
    apply = "--apply" in argv
    paths = [a for a in argv if not a.startswith("--")]
    if len(paths) != 1:
        print(__doc__)
        return 2
    trades, notes = read_statement(paths[0])
    store = Store(sd.DB_FILE)
    existing = store.transactions()
    known = set(store.symbols()) | {t.symbol for t in existing}
    known |= {trade.symbol.upper() for trade in trades}  # die Kürzel sind schon Yahoo-Kürzel
    plan = sources.plan_import(SOURCE, trades, existing, store.aliases(SOURCE), known)
    print(describe(plan, notes))
    if apply and not plan.rejected:
        apply_plan(store, plan)
        print("gebucht.")
    elif apply:
        print("Nichts gebucht: abgelehnte Einträge zuerst klären.")
    else:
        print("Probelauf, nichts gebucht (mit --apply buchen).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
