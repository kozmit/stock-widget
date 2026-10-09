# eToro-Anbindung: Plan

Die Historie (Käufe und Verkäufe) soll später aus einer Anbindung an eToro kommen. Bis dahin besteht sie aus
manuellen Einträgen und dem Startbestand (Kaufdatum unbekannt). Das Widget ist darauf vorbereitet; es fehlt nur
die Anbindung selbst.

## Was schon da ist

| Baustein | Datei | Aufgabe |
|---|---|---|
| Herkunft je Transaktion | `store.py`, `ledger.py` | `source`: `manual`, `opening` (Startbestand) oder der Name einer Anbindung (`etoro`). `external_id`: Kennung beim Anbieter. |
| Keine Doppelbuchung | `store.py` | Eindeutiger Index auf (`source`, `external_id`): ein erneuter Abgleich bucht nichts zweimal. |
| Schnittstelle | `sources.py` | `TransactionSource.fetch(cursor)` liefert einen `SyncBatch` aus `ExternalTrade`-Einträgen. |
| Planung des Imports | `sources.plan_import` | Prüft ohne Datenbank: neu, schon vorhanden, Kürzel unbekannt, ungültig (Verlauf bricht), Startbestand ersetzt. |
| Buchen und Abgleichen | `Controller.import_trades`, `Controller.sync_source` | Bucht den Plan, merkt sich den Stand (`sync_state`), hängt neue Aktien an die Watchlist. |
| Kürzel-Zuordnung | `symbol_aliases`, `Controller.set_alias` | Kürzel beim Anbieter -> Yahoo-Kürzel, gespeichert je Anbindung. |
| Anzeige | Detailfenster, Transaktionsliste, Kursdiagramm | Zeigen die Herkunft (Badge „eToro“), Startbestand als Ring, Hinweis, was noch fehlt. |
| Verlauf des Portfolios | `history.performance_series` | Rechnet Wert, Investiert und Ergebnis Tag für Tag aus Einträgen, Tageskursen und Wechselkursen. |

## Was für die eigentliche Anbindung noch zu tun ist

1. Klasse `EtoroSource(TransactionSource)` mit `name = "etoro"`, `label = "eToro"` und `fetch(cursor)`.
   Sie holt die Handelshistorie, wandelt jeden Eintrag in einen `ExternalTrade` und gibt als `cursor` zum
   Beispiel den Zeitstempel des letzten Eintrags zurück.
2. Anmelden im Controller: `ctl.sources["etoro"] = EtoroSource(...)`. Danach genügt `ctl.sync_source("etoro", ...)`.
3. Eine Bedienung dafür (Schaltfläche „Abgleichen“, Auswahl der Kürzel-Zuordnung bei unbekannten Kürzeln,
   Frage „Startbestand durch die echten Käufe ersetzen?“ -> `replace_openings=True`).
4. Zugangsdaten sicher ablegen (nicht in der Datenbank und nicht im Programm): Windows-Anmeldeinformationsspeicher.

## Offene Fragen an eToro (vor der Umsetzung zu klären)

Das ist bewusst keine Annahme über die API, sondern eine Liste dessen, was geprüft werden muss:

- **Zugang:** Gibt es einen Zugang mit Schlüssel für die persönliche Handelshistorie, und welche Rechte
  (nur lesen) braucht er? Gibt es Abrufgrenzen?
- **Modell:** eToro führt Positionen (Eröffnung und Schließung), keine einzelnen Käufe und Verkäufe.
  Eine Eröffnung wird dann zu einem Kauf, eine (Teil-)Schließung zu einem Verkauf. Das Widget rechnet nach
  FIFO; eToro rechnet je Position. Die Ergebnisse können deshalb abweichen. Teilverkäufe, Nachkäufe in dieselbe
  Position und Stop-Loss-Schließungen müssen sauber abgebildet werden.
- **Echte Aktien oder CFD:** Nur echte Aktien gehören in den Bestand. Hebelprodukte und Short-Positionen
  passen nicht ins Modell (nur Kauf und Verkauf).
- **Kürzel:** eToro nennt Instrumente mit eigener Kennung und eigenem Kürzel. Die Zuordnung zum Yahoo-Kürzel
  (zum Beispiel Börsenendungen wie `.AX` oder `.SW`) muss einmal je Wert bestätigt werden.
- **Währung und Gebühren:** In welcher Währung kommt der Preis, und wie werden Gebühren, Spreads und
  Währungsumrechnung gemeldet? `ExternalTrade.price` ist in der Notierungswährung der Aktie, `fee` in derselben.
- **Dividenden und Kapitalmaßnahmen:** Splits, Spin-offs und Dividenden in Aktien ändern den Bestand ohne Kauf.
  Sie brauchen später eigene Einträge, sonst stimmt der Bestand nicht.
- **Startbestand:** Sobald die echte Historie vollständig ist, ersetzt sie den Startbestand
  (`replace_openings=True`). Ist sie unvollständig, würden Verkäufe als „ungültig“ abgelehnt und im Ergebnis
  gemeldet; dann den Startbestand besser behalten.

## Verhalten bei Abgleich und Löschen

- Ein erneuter Abgleich ist unschädlich: schon gebuchte Einträge (gleiche Kennung) werden übersprungen.
- Ein Eintrag, den du löschst, wird beim nächsten Abgleich wieder angelegt. Die Oberfläche warnt davor.
- Was den Verlauf ungültig machen würde (zum Beispiel ein Verkauf ohne Bestand), wird nicht gebucht und im
  Ergebnis des Imports mit Grund genannt.
