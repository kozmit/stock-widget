# CLAUDE.md

Aktien-Widget: Taskleisten-Tool (Python 3.12, PySide6, yfinance, SQLite). UI-Texte, Kommentare und Docstrings sind Deutsch.

## Regel: Widget nach jeder Änderung neu starten

Nach jeder Änderung am Code muss das laufende Widget neu gestartet werden, damit sie in der Taskleiste wirkt. Die laufende App ist die installierte Exe `C:\Users\Cedri\AppData\Local\StockWidget\StockWidget.exe` (Autostart mit `--tray`), nicht `dist\`. Ablauf:

1. Tests: `python -m unittest discover -s tests -t .` (müssen grün sein).
2. Bauen: `python -m PyInstaller --noconfirm StockWidget.spec` (Ausgabe `dist\StockWidget.exe`).
3. Alle `StockWidget`-Prozesse beenden und etwa 5 Sekunden warten, sonst ist die Exe noch gesperrt.
4. `dist\StockWidget.exe` über die installierte Exe kopieren und prüfen, dass beide denselben Hash haben.
5. Mit `--tray` neu starten und prüfen, dass der Prozess läuft.

Der Nutzer hat dieses Vorgehen dauerhaft freigegeben; dafür muss nicht jedes Mal gefragt werden.

## Regel: genau eine Datenbank

Die Daten liegen in `C:\Users\Cedri\StockWidget\stock_widget.db` (Ordner per `STOCKWIDGET_DATA` überschreibbar, siehe `stock_data.DATA_DIR`). Das ist bewusst nicht `%APPDATA%`: Die Shell in der Claude-App sieht `AppData` umgeleitet nach `...\AppData\Local\Packages\Claude_pzs8sxrjxfjjc\LocalCache\...`, ein per Autostart gestartetes Widget dagegen das echte `AppData`. Das führte schon einmal zu zwei Datenbanken, und das Widget zeigte nach einem Neustart nur die Standardaktien.

- Nie eine zweite Datenbank anlegen oder `DATA_DIR` zurück auf `AppData` stellen.
- Tests nutzen immer temporäre Datenbanken (`tests/support.py`), nie die echte.
- Vor riskanten Eingriffen an der Datenbank eine Kopie nach `.claude\backup\` legen (ist nicht im Repo).
