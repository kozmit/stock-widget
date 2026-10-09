"""Minimaler Leser für .xlsx-Dateien, nur mit der Standardbibliothek (kein openpyxl nötig).

Liest alle Tabellenblätter als Listen von Zeilen; Zellen sind Texte oder None. Formeln und Formate werden nicht
ausgewertet, es zählt der zuletzt gespeicherte Wert.
"""
import re
import xml.etree.ElementTree as ET
import zipfile

MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _column_index(ref):
    index = 0
    for letter in re.match(r"[A-Z]+", ref).group():
        index = index * 26 + ord(letter) - 64
    return index - 1


def _text(node):
    return "".join(t.text or "" for t in node.iter(MAIN + "t"))


def read_workbook(path):
    """{Blattname: [Zeile, ...]} mit Zeilen als Listen; fehlende Zellen sind None."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = [_text(si) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(MAIN + "si")]
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        book = ET.fromstring(z.read("xl/workbook.xml"))
        sheets = {}
        for sheet in book.find(MAIN + "sheets"):
            target = rels[sheet.get(REL + "id")].lstrip("/")
            target = target if target.startswith("xl/") else "xl/" + target
            rows = []
            for row in ET.fromstring(z.read(target)).iter(MAIN + "row"):
                cells = {}
                for cell in row.findall(MAIN + "c"):
                    value = cell.find(MAIN + "v")
                    if cell.get("t") == "s" and value is not None:
                        text = shared[int(value.text)]
                    elif cell.get("t") == "inlineStr":
                        text = _text(cell)
                    else:
                        text = value.text if value is not None else None
                    cells[_column_index(cell.get("r"))] = text
                rows.append([cells.get(i) for i in range(max(cells) + 1)] if cells else [])
            sheets[sheet.get("name")] = rows
    return sheets
