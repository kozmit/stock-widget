"""eToro-Anbindung (nur lesend): offene Positionen und abgeschlossene Trades werden zu Käufen und Verkäufen.

Quelle ist die öffentliche eToro-API (public-api.etoro.com, Kopfzeilen x-api-key und x-user-key). Es werden nur
GET-Aufrufe abgesetzt; das Widget kann über diese Anbindung nichts handeln.

Abbildung auf das Hauptbuch (ledger.py):
- Jede Position ergibt einen Kauf zum Eröffnungskurs (Kennung "<id>:open").
- Jede geschlossene Position ergibt zusätzlich einen Verkauf zum Schlusskurs (Kennung "<id>:close"), Gebühren
  der Position hängen am Verkauf.
- Nicht abgebildet werden Leerverkäufe und gehebelte Positionen (CFD); sie stehen in den Hinweisen des Abgleichs.

Die Schlüssel liegen im Klartext in etoro.json im Datenordner (auf Wunsch des Nutzers), nie in der Datenbank.
"""
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from sources import ExternalTrade, SyncBatch, TransactionSource

BASE_URL = "https://public-api.etoro.com/api/v1"
PAGE_SIZE = 100
MAX_PAGES = 200
MAX_RETRIES = 3
HISTORY_DAYS = 360       # die API erlaubt je Abruf weniger als ein Jahr Rückblick
OVERLAP_DAYS = 7         # beim Folge-Abgleich etwas früher beginnen; Dubletten fängt plan_import ab


class EtoroError(Exception):
    """Fehler beim Abruf, mit Meldung für den Nutzer."""


def credentials_path(data_dir):
    return os.path.join(data_dir, "etoro.json")


def load_credentials(data_dir):
    """(api_key, user_key) oder None, wenn nichts oder Unbrauchbares gespeichert ist."""
    try:
        with open(credentials_path(data_dir), encoding="utf-8") as f:
            data = json.load(f)
        api_key, user_key = data["api_key"].strip(), data["user_key"].strip()
    except (OSError, ValueError, KeyError, AttributeError, TypeError):
        return None
    return (api_key, user_key) if api_key and user_key else None


def save_credentials(data_dir, api_key, user_key):
    api_key, user_key = api_key.strip(), user_key.strip()
    if not api_key or not user_key:
        raise ValueError("Beide Schlüssel werden gebraucht")
    os.makedirs(data_dir, exist_ok=True)
    with open(credentials_path(data_dir), "w", encoding="utf-8") as f:
        json.dump({"api_key": api_key, "user_key": user_key}, f)


def delete_credentials(data_dir):
    try:
        os.remove(credentials_path(data_dir))
    except FileNotFoundError:
        pass


def _urlopen(url, headers, timeout=20):
    """Gibt (Status, Kopfzeilen, Text) zurück; Fehlerstatus werfen nicht."""
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers), response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as exc:
        raise EtoroError(f"eToro ist nicht erreichbar: {exc}") from exc


class EtoroClient:
    def __init__(self, api_key, user_key, opener=_urlopen, sleep=time.sleep):
        self.api_key, self.user_key, self.opener, self.sleep = api_key, user_key, opener, sleep

    def get(self, path, params=None):
        url = BASE_URL + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(MAX_RETRIES + 1):
            headers = {"x-api-key": self.api_key, "x-user-key": self.user_key,
                       "x-request-id": str(uuid.uuid4()), "Accept": "application/json"}
            status, response_headers, text = self.opener(url, headers)
            if status == 429 and attempt < MAX_RETRIES:
                retry = {k.lower(): v for k, v in response_headers.items()}.get("retry-after", "")
                self.sleep(min(float(retry) if retry.replace(".", "", 1).isdigit() else 2 ** attempt, 60))
                continue
            break
        if status in (401, 403):
            raise EtoroError(f"eToro lehnt die Schlüssel ab (Status {status}). Prüfe Schlüssel und Leserecht.")
        if status == 429:
            raise EtoroError("eToro bremst die Abrufe (zu viele Anfragen). Später noch einmal versuchen.")
        if status != 200:
            raise EtoroError(f"eToro antwortet mit Status {status}: {text[:200]}")
        try:
            return json.loads(text)
        except ValueError as exc:
            raise EtoroError("eToro hat keine lesbare Antwort geliefert") from exc

    def portfolio_positions(self):
        data = self.get("/trading/info/portfolio")
        return list((data.get("clientPortfolio") or {}).get("positions") or [])

    def history(self, min_date):
        """Alle abgeschlossenen Trades seit min_date, seitenweise."""
        trades = []
        for page in range(1, MAX_PAGES + 1):
            chunk = self.get("/trading/info/trade/history",
                             {"minDate": min_date.isoformat(), "page": page, "pageSize": PAGE_SIZE})
            if not isinstance(chunk, list):
                raise EtoroError("Der Handelsverlauf von eToro hat ein unerwartetes Format")
            trades.extend(chunk)
            if len(chunk) < PAGE_SIZE:
                break
        return trades

    def instruments(self, ids):
        """instrumentID -> {symbol, name}; fehlende Kennungen fehlen auch im Ergebnis."""
        result = {}
        ids = sorted(set(ids))
        for start in range(0, len(ids), 50):
            data = self.get("/market-data/instruments", {"instrumentIds": ",".join(map(str, ids[start:start + 50]))})
            for item in data.get("instrumentDisplayDatas") or []:
                result[item.get("instrumentID")] = {"symbol": (item.get("symbolFull") or "").strip(),
                                                    "name": item.get("instrumentDisplayName") or ""}
        return result


def _day(text):
    return dt.date.fromisoformat(str(text)[:10])


def _is_plain_long(item):
    """Nur ungehebelte Käufe sind echte Wertpapiere; alles andere ist ein CFD oder eine Leerposition."""
    return bool(item.get("isBuy")) and (item.get("leverage") or 1) == 1


def build_trades(open_positions, closed_trades, instruments):
    """Rechnet die Antworten der API in ExternalTrade um. Gibt (Einträge, Hinweise) zurück."""
    trades, notes = [], []
    skipped, unnamed = 0, set()

    def symbol_of(item):
        info = instruments.get(item.get("instrumentID", item.get("instrumentId")))
        if not info or not info["symbol"]:
            unnamed.add(item.get("instrumentID", item.get("instrumentId")))
            return None
        return info["symbol"]

    for item in open_positions:
        if item.get("mirrorID"):       # Kopierte Positionen (Copy Trading) gehören nicht zu den eigenen Käufen
            skipped += 1
            continue
        if not _is_plain_long(item) or not item.get("units") or not item.get("openRate"):
            skipped += 1
            continue
        symbol = symbol_of(item)
        if symbol:
            trades.append(ExternalTrade(f"{item['positionID']}:open", symbol, "buy", float(item["units"]),
                                        float(item["openRate"]), _day(item["openDateTime"])))
    for item in closed_trades:
        if not _is_plain_long(item) or not item.get("units") or not item.get("openRate") or not item.get("closeRate"):
            skipped += 1
            continue
        symbol = symbol_of(item)
        if not symbol:
            continue
        key = item["positionId"]
        trades.append(ExternalTrade(f"{key}:open", symbol, "buy", float(item["units"]), float(item["openRate"]),
                                    _day(item["openTimestamp"])))
        trades.append(ExternalTrade(f"{key}:close", symbol, "sell", float(item["units"]), float(item["closeRate"]),
                                    _day(item["closeTimestamp"]), fee=max(float(item.get("fees") or 0), 0.0)))
    if skipped:
        notes.append(f"{skipped} Position(en) übersprungen (gehebelt, Leerverkauf, kopiert oder ohne Stückzahl)")
    if unnamed:
        notes.append(f"{len(unnamed)} Instrument(e) ohne Kürzel bei eToro")
    return trades, notes


class EtoroSource(TransactionSource):
    name, label = "etoro", "eToro"

    def __init__(self, client, today=dt.date.today):
        self.client, self.today = client, today

    def fetch(self, cursor):
        today = self.today()
        floor = today - dt.timedelta(days=HISTORY_DAYS)
        try:
            start = max(dt.date.fromisoformat(cursor) - dt.timedelta(days=OVERLAP_DAYS), floor) if cursor else floor
        except ValueError:
            start = floor
        positions = self.client.portfolio_positions()
        closed = self.client.history(start)
        ids = {p.get("instrumentID") for p in positions} | {t.get("instrumentId") for t in closed}
        instruments = self.client.instruments(i for i in ids if i is not None)
        trades, notes = build_trades(positions, closed, instruments)
        if not cursor:
            notes.append(f"Handelsverlauf nur ab {floor:%d.%m.%Y} (Grenze der eToro-Schnittstelle); "
                         "ältere, schon geschlossene Positionen fehlen")
        return SyncBatch(tuple(trades), today.isoformat(), tuple(notes))
