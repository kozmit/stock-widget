"""Wechselkurse in die Basiswährung, ohne Netzwerk und ohne Datenbank.

Ein Kurs bedeutet immer: so viele Einheiten der Basiswährung kostet eine Einheit der Fremdwährung
(USD -> EUR: 0.89). Notierungen in Pence (London) werden hier auf das Pfund zurückgeführt.
"""
import bisect

BASE = "EUR"           # Basiswährung; set_base() ändert sie, alle Module lesen sie als fx.BASE
BASES = ("EUR", "USD")  # zur Wahl stehen Euro und Dollar (eToro rechnet in Dollar)
# Notierungswährung -> (Währung, Faktor auf die Haupteinheit)
SUBUNITS = {"GBp": ("GBP", 0.01), "GBX": ("GBP", 0.01), "ZAc": ("ZAR", 0.01)}


def set_base(code):
    """Stellt die Basiswährung um. Gespeicherte Wechselkurse gelten nur für eine Basis und müssen danach neu geladen
    werden (Controller.set_base kümmert sich darum)."""
    global BASE
    if code not in BASES:
        raise ValueError(f"Basiswährung {code} ist nicht vorgesehen")
    BASE = code


def split_currency(currency):
    """("GBp") -> ("GBP", 0.01); alle anderen Währungen bleiben unverändert mit Faktor 1."""
    return SUBUNITS.get(currency, (currency, 1.0))


def currency_code(currency):
    return split_currency(currency)[0]


class FxTable:
    """Tageskurse je Fremdwährung (Historie) und der zuletzt abgerufene Kurs."""

    def __init__(self, history=None, latest=None):
        self.history = {code: dict(days) for code, days in (history or {}).items()}  # Code -> {Datum: Kurs}
        self.latest = {code: dict(info) for code, info in (latest or {}).items()}    # Code -> {rate, fetched_at, source}
        self._sorted = {}

    def add_history(self, code, rates):
        self.history.setdefault(code, {}).update(rates)
        self._sorted.pop(code, None)

    def set_latest(self, code, rate, fetched_at, source=""):
        self.latest[code] = {"rate": rate, "fetched_at": fetched_at, "source": source}

    def coverage(self, code):
        """(erster, letzter) Tag der gespeicherten Historie oder None."""
        days = self._days(code)
        return (days[0], days[-1]) if days else None

    def _days(self, code):
        if code not in self._sorted:
            self._sorted[code] = sorted(self.history.get(code, {}))
        return self._sorted[code]

    def now(self, currency):
        """Aktueller Kurs; fehlt er, gilt der neueste Tageskurs. None, wenn gar nichts bekannt ist."""
        code, scale = split_currency(currency)
        if code == BASE:
            return scale
        if code in self.latest:
            return self.latest[code]["rate"] * scale
        days = self._days(code)
        return self.history[code][days[-1]] * scale if days else None

    def on(self, currency, day):
        """Kurs an einem Tag. Am Wochenende gilt der letzte Handelstag, vor Beginn der Historie der erste."""
        code, scale = split_currency(currency)
        if code == BASE:
            return scale
        days = self._days(code)
        if not days:
            return None
        index = bisect.bisect_right(days, day)
        return self.history[code][days[index - 1 if index else 0]] * scale
