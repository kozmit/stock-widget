"""Zentral gepflegte Erklärungen aller Fachbegriffe, ohne Oberfläche.

Jeder Begriff steht genau einmal hier. Die Oberfläche (stock_widget.explain, stock_widget.term_link) holt den Text
von hier, deshalb wird ein Begriff überall gleich erklärt. Neue Begriffe: einen Term in GLOSSARY ergänzen und im
Widget mit explain(widget, "schlüssel") oder term_link("Text", "schlüssel") anbinden.

Aufbau einer Erklärung: Langform, Erklärung, Formel, Einheit, Deutung, Beispiel. Leere Felder werden nicht gezeigt.
"""
import html
from dataclasses import dataclass

LINK_PREFIX = "term:"


@dataclass(frozen=True)
class Term:
    key: str
    title: str                  # so steht der Begriff in der Oberfläche
    text: str                   # die eigentliche Erklärung
    full: str = ""              # Langform, auch englischer Name
    formula: str = ""
    unit: str = ""
    interpretation: str = ""    # wie man den Wert liest
    example: str = ""

    def sections(self):
        """Die nicht leeren Teile als [(Überschrift, Text)], in der Reihenfolge, in der sie gezeigt werden."""
        parts = (("Formel", self.formula), ("Einheit", self.unit), ("Deutung", self.interpretation),
                 ("Beispiel", self.example))
        return [(label, value) for label, value in parts if value]


def _terms(*terms):
    return {term.key: term for term in terms}


GLOSSARY = _terms(
    # ---- Kurse und Position ----
    Term("kurs", "Kurs", "Der zuletzt gehandelte Preis einer Aktie, in der Währung, in der sie an ihrer Börse notiert ist.",
         unit="Währung der Börse, z. B. USD, EUR, CHF, AUD",
         interpretation="Der Kurs kann leicht verzögert sein. Ist er älter als drei Minuten oder schlug der letzte "
                        "Abruf fehl, erscheint er gelb und kursiv als veraltet."),
    Term("tag", "Tag (Tagesänderung)", "Die Veränderung des Kurses gegenüber dem Schlusskurs des Vortags.",
         formula="(Kurs heute ÷ Schlusskurs gestern − 1) × 100", unit="Prozent",
         interpretation="Ein Pfeil nach oben bedeutet Plus, nach unten Minus. Der Wert zeigt nur den heutigen Tag, "
                        "nicht deinen Gewinn oder Verlust."),
    Term("position", "Position", "Die Menge einer Aktie, die du hältst, samt ihrem Wert und deinem Ergebnis damit.",
         interpretation="Eine Position entsteht durch Käufe und wird durch Verkäufe kleiner. Der Bestand wird immer "
                        "aus den eingetragenen Käufen und Verkäufen berechnet."),
    Term("positionswert", "Position (Wert)", "Der aktuelle Wert der gehaltenen Stücke einer Aktie.",
         formula="Stückzahl × aktueller Kurs", unit="Währung der Aktie (nicht umgerechnet)",
         interpretation="Der Mauszeiger auf dem Wert zeigt die Stückzahl. In der Portfolio-Übersicht stehen die "
                        "Werte dagegen in der Basiswährung."),
    Term("startbestand", "Startbestand",
         "Ein übernommener Bestand ohne bekanntes Kaufdatum, zum Beispiel aus dem alten Widget oder über „Startbestand "
         "festlegen“. Der Einstandskurs wird aus Stückzahl und deinem damaligen Gewinn oder Verlust berechnet.",
         interpretation="Als Datum steht der Tag der Eintragung, nicht der Kauf. Deshalb ist der Währungseffekt erst ab "
                        "diesem Tag messbar, und der Verlauf beginnt erst dort. Echte Käufe, etwa aus einer Anbindung "
                        "wie eToro, können den Startbestand ersetzen."),
    Term("einstand", "Einstandskurs",
         "Der durchschnittliche Kaufkurs der Stücke, die du noch hältst, einschließlich der anteiligen Kaufgebühren.",
         formula="Summe aus (Stück × Kaufkurs + Gebühr) der gehaltenen Stücke ÷ Anzahl gehaltener Stücke",
         unit="Währung der Aktie, je Stück",
         interpretation="Liegt der Kurs über dem Einstand, bist du mit der Position im Plus.",
         example="10 Stück zu 100 und 10 Stück zu 120, keine Gebühr → Einstand 110."),
    Term("gebuehr", "Gebühr", "Kosten einer Transaktion (Provision, Spesen). Sie erhöhen beim Kauf die "
                              "Anschaffungskosten und mindern beim Verkauf den Erlös.",
         unit="Währung der Aktie"),
    Term("fifo", "FIFO (First In, First Out)",
         "Beim Verkauf gelten die ältesten gekauften Stücke zuerst als verkauft. Danach richtet sich, welcher Einstand "
         "zu einem Verkauf gehört und wie hoch der realisierte Gewinn ist.",
         interpretation="In Deutschland gilt dieses Verfahren steuerlich für Wertpapiere im selben Depot. Dieses Widget "
                        "rechnet deshalb ebenso.",
         example="Gekauft: 10 zu 100, dann 10 zu 120. Verkauf von 15 Stück: 10 zu 100 und 5 zu 120 gelten als verkauft."),
    # ---- Ergebnis ----
    Term("gv", "Gewinn/Verlust (G/V)", "Der Betrag, um den der heutige Wert der gehaltenen Stücke über oder unter dem "
                                       "liegt, was du dafür bezahlt hast.",
         formula="(Kurs − Einstandskurs) × Stückzahl", unit="Währung der Aktie",
         interpretation="Grün ist ein Gewinn, rot ein Verlust. Er wird erst beim Verkauf wirklich (siehe "
                        "unrealisiert und realisiert).",
         example="10 Stück, Einstand 80, Kurs 100 → +200."),
    Term("gv_prozent", "G/V in Prozent", "Gewinn oder Verlust der gehaltenen Stücke im Verhältnis zu dem, was du dafür "
                                         "bezahlt hast.",
         formula="(Kurs ÷ Einstandskurs − 1) × 100", unit="Prozent",
         interpretation="+25 % heißt: Die Stücke sind 25 % mehr wert als beim Kauf."),
    Term("unrealisiert", "Unrealisierter Gewinn/Verlust",
         "Gewinn oder Verlust der Stücke, die du noch hältst. Er ist nur auf dem Papier, solange du nicht verkaufst.",
         formula="Wert − Investiert", unit="Basiswährung (Euro oder Dollar)",
         interpretation="Er ändert sich mit Kurs und Wechselkurs. Steuerlich zählt er erst beim Verkauf."),
    Term("realisiert", "Realisierter Gewinn/Verlust",
         "Gewinn oder Verlust aus Stücken, die du bereits verkauft hast. Er steht endgültig fest.",
         formula="Erlös (nach Gebühr) − Anschaffungskosten der verkauften Stücke (FIFO)", unit="Basiswährung (Euro oder Dollar)",
         interpretation="Erlös und Anschaffungskosten werden je zum Wechselkurs des Verkaufs- und des Kauftags in die Basiswährung "
                        "umgerechnet. Ein späterer Verkauf ändert frühere Verkäufe nicht."),
    Term("gesamtergebnis", "Gesamtergebnis", "Alles, was du mit dem Portfolio bisher verdient oder verloren hast: das "
                                             "Ergebnis der gehaltenen Stücke plus das der verkauften.",
         formula="unrealisiert + realisiert", unit="Basiswährung (Euro oder Dollar)"),
    Term("gesamtrendite", "Gesamtrendite",
         "Das Gesamtergebnis im Verhältnis zum gesamten Kapital, das du je eingesetzt hast (gehaltene und bereits "
         "verkaufte Stücke).",
         formula="(unrealisiert + realisiert) ÷ (Investiert + Anschaffungskosten der verkauften Stücke) × 100",
         unit="Prozent",
         interpretation="Das ist keine zeitgewichtete Rendite: Sie sagt nichts darüber, wie lange das Kapital "
                        "gebunden war, und zählt Ein- und Auszahlungen nicht nach dem Zeitpunkt.",
         example="Ergebnis +168 €, eingesetzt 1.150 € → +14,6 %."),
    Term("investiert", "Investiert", "Die Anschaffungskosten der Stücke, die du noch hältst, in der Basiswährung, einschließlich "
                                     "der Gebühren.",
         formula="Summe aus Stück × Einstand × Wechselkurs am Kauftag", unit="Basiswährung (Euro oder Dollar)",
         interpretation="Der Wechselkurs des Kauftags bleibt fest. Verkaufte Stücke zählen hier nicht mehr mit."),
    Term("gesamtwert", "Gesamtwert", "Der heutige Wert aller gehaltenen Stücke plus das verfügbare Guthaben bei "
                                     "eToro, in der Basiswährung (Euro oder Dollar).",
         formula="Summe aus Stückzahl × Kurs × aktueller Wechselkurs, plus Guthaben × aktueller Wechselkurs",
         unit="Basiswährung (Euro oder Dollar)",
         interpretation="Aktien ohne Kurs oder ohne Wechselkurs fehlen im Wert; der gelbe Hinweis nennt sie. Ohne "
                        "bekanntes Guthaben (kein eToro-Abgleich) zählen nur die Stücke."),
    Term("guthaben", "Verfügbares Guthaben", "Das Geld auf deinem eToro-Konto, das nicht in Positionen steckt "
                                             "(bei eToro „Credit“, in US-Dollar).",
         unit="Basiswährung, umgerechnet vom Dollar",
         interpretation="Der Stand stammt vom letzten eToro-Abgleich. Er gehört zum Gesamtwert, nicht zu Investiert "
                        "und nicht zum Ergebnis."),
    Term("basiswaehrung", "Basiswährung", "Die Währung, in der das ganze Portfolio gerechnet wird: Euro oder Dollar, oben im "
                                          "Portfolio-Fenster umschaltbar. Aktien in anderen Währungen werden mit dem "
                                          "Wechselkurs umgerechnet.",
         unit="EUR oder USD"),
    Term("kursgewinn", "Kursgewinn", "Der Teil des Ergebnisses, der aus der Kursentwicklung der Aktie selbst kommt, "
                                    "ohne die Veränderung des Wechselkurses.",
         formula="(Wert − Kosten in Aktienwährung) × Wechselkurs am Kauftag", unit="Basiswährung (Euro oder Dollar)"),
    Term("waehrungseffekt", "Währungseffekt",
         "Der Teil des Ergebnisses, der nur daher kommt, dass sich der Wechselkurs seit dem Kauf verändert hat.",
         formula="Wert in Aktienwährung × (Wechselkurs heute − Wechselkurs am Kauftag)", unit="Basiswährung (Euro oder Dollar)",
         interpretation="Wird der Euro stärker, sinkt der Euro-Wert von Aktien in Dollar, Franken oder Australischen "
                        "Dollar, auch wenn ihr Kurs gleich bleibt. Kursgewinn und Währungseffekt ergeben zusammen das "
                        "Ergebnis in der Basiswährung."),
    Term("kurs_waehrung", "Kursgewinn und Währungseffekt",
         "Das Ergebnis besteht aus zwei Teilen: dem Kursgewinn der Aktie und dem Effekt der Wechselkursänderung.",
         formula="Ergebnis = Kursgewinn + Währungseffekt",
         interpretation="Kurs: Gewinn in der Aktienwährung, zum Wechselkurs des Kaufs umgerechnet. Währung: Wert × "
                        "Änderung des Wechselkurses seit dem Kauf. Aktien in der Basiswährung haben keinen Währungseffekt."),
    Term("anteil", "Anteil", "Wie viel Prozent des Gesamtwerts diese Position ausmacht.",
         formula="Wert der Position ÷ Gesamtwert × 100", unit="Prozent",
         interpretation="Ein hoher Anteil heißt: Das Portfolio hängt stark an dieser Aktie."),
    Term("aufteilung", "Aufteilung", "Wie sich der Gesamtwert verteilt: nach Position, Branche, Land oder Währung.",
         interpretation="Hilft zu erkennen, ob das Portfolio einseitig ist, zum Beispiel nur eine Branche oder nur "
                        "US-Dollar. Branche und Land stammen von Yahoo Finance; fehlen sie, steht „Unbekannt“."),
    Term("verlauf", "Verlauf des Portfolios",
         "Wert und investiertes Kapital des Portfolios Tag für Tag, berechnet aus deinen Einträgen, den Tageskursen "
         "und den Wechselkursen des jeweiligen Tages.",
         interpretation="Die Fläche ist der Wert, die gestrichelte Linie das investierte Kapital. Beginnt der Verlauf "
                        "erst am Tag der Eintragung, kennt das Widget frühere Käufe noch nicht."),
    Term("historie", "Historie", "Die Liste deiner Käufe und Verkäufe einer Aktie mit Datum, Menge, Kurs und Herkunft.",
         interpretation="Herkunft: manuell eingetragen, Startbestand oder aus einer Anbindung wie eToro. "
                        "Aus ihr werden Bestand, Einstand und realisierter Gewinn berechnet."),
    Term("veraltet", "Veralteter Kurs", "Ein Kurs gilt als veraltet, wenn der letzte erfolgreiche Abruf länger als drei "
                                        "Minuten her ist oder der letzte Abruf fehlgeschlagen ist.",
         interpretation="Der letzte bekannte Kurs bleibt sichtbar, aber gelb und kursiv. Werte, die auf ihm beruhen, "
                        "sind dann nicht aktuell."),
    # ---- Termine ----
    Term("termine", "Termine (Kalender)",
         "Alle bekannten Termine zu deinen Aktien: Quartalszahlen und Dividenden von Yahoo Finance und eigene Einträge wie "
         "Produktstarts oder Genehmigungen. Jeder Termin zeigt, wie verlässlich er ist.",
         interpretation="Der Status ist wichtiger als das Datum: Nur bestätigte Termine sind offiziell. Daten von Yahoo "
                        "gelten als erwartet, weil Yahoo nicht sagt, ob das Unternehmen sie bestätigt hat."),
    Term("termin_status", "Status eines Termins",
         "Sagt, wie verlässlich das Datum eines Termins ist: bestätigt, erwartet, spekulativ oder eingetreten.",
         interpretation="Ein spekulativer Termin wird nie wie ein bestätigter dargestellt. Nach Ablauf des Datums gilt ein "
                        "bestätigter oder erwarteter Termin als eingetreten; ein spekulativer bleibt spekulativ und "
                        "wird als „Datum verstrichen“ gekennzeichnet."),
    Term("status_bestaetigt", "Status: bestätigt",
         "Der Termin wurde vom Unternehmen oder von der zuständigen Stelle offiziell genannt.",
         interpretation="Die verlässlichste Stufe. Auch ein bestätigter Termin kann sich noch verschieben."),
    Term("status_erwartet", "Status: erwartet",
         "Der Termin ist eine Schätzung oder stammt von einem Datenanbieter wie Yahoo Finance, ohne dass das Unternehmen "
         "ihn bestätigt hat.",
         interpretation="Gut zum Planen, aber das Datum kann sich noch ändern, vor allem bei Quartalszahlen."),
    Term("status_spekulativ", "Status: spekulativ",
         "Der Termin beruht auf Gerüchten, Vermutungen oder Aussagen Dritter.",
         interpretation="Plane nicht damit. Er kann sich stark verschieben oder ganz entfallen, zum Beispiel ein "
                        "Erscheinungstermin, den nur ein Branchengerücht nennt."),
    Term("status_eingetreten", "Status: eingetreten",
         "Das Datum des Termins ist vorbei; das Widget geht davon aus, dass er stattgefunden hat.",
         interpretation="Bei bestätigten und erwarteten Terminen setzt das Widget den Status selbst. Wurde ein Termin "
                        "verschoben, legt Yahoo oder du den neuen an."),
    Term("relevanz", "Relevanz eines Termins",
         "Wie wichtig der Termin für die Aktie ist: hoch, normal oder niedrig.",
         interpretation="Quartalszahlen sind meist hoch, Dividendenzahlungen niedrig. Am selben Tag stehen wichtigere "
                        "Termine oben."),
    Term("ungenau", "Termin ohne genauen Tag",
         "Manche Termine sind nur als Monat oder Jahr bekannt, zum Beispiel ein Erscheinungstermin „Mai 2027“.",
         interpretation="Sie stehen im Kalender in einem eigenen Abschnitt, damit kein Datum vorgetäuscht wird."),
    Term("termin", "Nächster Termin", "Das nächste wichtige Datum zu dieser Aktie: Quartalszahlen, Ex-Dividende oder "
                                      "Dividendenzahlung.",
         interpretation="Rund um Quartalszahlen bewegen sich Kurse oft stärker."),
    Term("quartalszahlen", "Quartalszahlen", "Die Veröffentlichung der Geschäftszahlen eines Quartals: Umsatz, Gewinn "
                                             "und oft ein Ausblick.",
         full="Quartalsbericht (Earnings)",
         interpretation="Liegen die Zahlen über oder unter den Erwartungen der Analysten, bewegt sich der Kurs häufig "
                        "deutlich. Das Datum kann sich noch verschieben, solange es nicht bestätigt ist."),
    Term("ex_dividende", "Ex-Dividende", "Ab diesem Tag wird die Aktie ohne die nächste Dividende gehandelt. Wer sie am "
                                         "Tag davor besitzt, bekommt die Dividende.",
         full="Ex-Dividenden-Tag (Ex-Dividend Date)",
         interpretation="Am Ex-Tag fällt der Kurs meist ungefähr um die Dividende. Das ist kein Verlust, "
                        "denn die Dividende wird gutgeschrieben."),
    Term("dividendenzahlung", "Dividendenzahlung", "Der Tag, an dem die Dividende auf dein Konto gezahlt wird.",
         full="Zahltag (Payment Date)",
         interpretation="Er liegt meist einige Tage nach dem Ex-Dividenden-Tag."),
    # ---- Kennzahlen für die Bewertung (kommen mit den Fundamentaldaten, stehen hier schon zentral) ----
    Term("kgv", "KGV", "Das KGV setzt den Aktienkurs ins Verhältnis zum Gewinn je Aktie. Es zeigt, mit dem Wievielfachen "
                       "des jährlichen Gewinns die Aktie bewertet wird.",
         full="Kurs-Gewinn-Verhältnis (Price-to-Earnings Ratio, P/E)",
         formula="Aktienkurs ÷ Gewinn je Aktie", unit="Faktor, z. B. 18,5",
         interpretation="Ein hohes KGV kann auf hohe Wachstumserwartungen oder eine hohe Bewertung hindeuten. Ein "
                        "niedriges KGV kann günstig wirken, aber auch auf Probleme des Unternehmens hindeuten. "
                        "Sinnvoll ist der Vergleich mit Mitbewerbern derselben Branche und mit der eigenen Historie.",
         example="Kurs 120 €, Gewinn je Aktie 6 € → KGV 20."),
    Term("kuv", "KUV", "Das KUV setzt den Börsenwert ins Verhältnis zum Jahresumsatz.",
         full="Kurs-Umsatz-Verhältnis (Price-to-Sales Ratio, P/S)",
         formula="Marktkapitalisierung ÷ Jahresumsatz", unit="Faktor, z. B. 4,2",
         interpretation="Nützlich bei Unternehmen ohne oder mit stark schwankendem Gewinn. Ein niedriger Wert kann "
                        "günstig sein, sagt aber nichts über die Gewinnmarge; Vergleiche nur innerhalb einer Branche.",
         example="Börsenwert 5 Mrd. €, Umsatz 1 Mrd. € → KUV 5."),
    Term("peg", "PEG-Ratio", "Das PEG setzt das KGV ins Verhältnis zum erwarteten Gewinnwachstum. Es soll zeigen, ob "
                             "ein hohes KGV durch Wachstum gerechtfertigt ist.",
         full="Price/Earnings-to-Growth Ratio",
         formula="KGV ÷ erwartetes jährliches Gewinnwachstum in Prozent", unit="Faktor, z. B. 1,3",
         interpretation="Ein PEG um 1 gilt oft als angemessen, deutlich darüber als teuer im Verhältnis zum Wachstum, "
                        "darunter als günstig. Es hängt stark von der Wachstumsschätzung ab und taugt wenig bei "
                        "schwankenden oder negativen Gewinnen.",
         example="KGV 24, erwartetes Wachstum 12 % pro Jahr → PEG 2."),
    Term("ev_ebitda", "EV/EBITDA", "Setzt den Unternehmenswert ins Verhältnis zum operativen Ergebnis vor "
                                   "Abschreibungen.",
         full="Unternehmenswert zu EBITDA (Enterprise Value / EBITDA)",
         formula="(Marktkapitalisierung + Nettoverschuldung) ÷ EBITDA", unit="Faktor, z. B. 11",
         interpretation="Berücksichtigt auch die Schulden und eignet sich besser als das KGV, um Unternehmen mit "
                        "unterschiedlicher Verschuldung zu vergleichen. Auch hier zählt der Vergleich in derselben "
                        "Branche."),
    Term("ebitda", "EBITDA", "Das Ergebnis des laufenden Geschäfts vor Zinsen, Steuern und Abschreibungen.",
         full="Earnings Before Interest, Taxes, Depreciation and Amortization",
         formula="Betriebsergebnis + Abschreibungen", unit="Währung des Unternehmens, meist in Millionen",
         interpretation="Gut zum Vergleichen, weil Finanzierung und Steuern keine Rolle spielen. Es blendet aber aus, "
                        "wie viel das Unternehmen investieren muss."),
    Term("free_cashflow", "Free Cashflow", "Das Geld, das nach allen nötigen Investitionen übrig bleibt.",
         full="Freier Cashflow",
         formula="Operativer Cashflow − Investitionen", unit="Währung des Unternehmens, meist in Millionen",
         interpretation="Daraus können Dividenden, Aktienrückkäufe und Schuldentilgung bezahlt werden. Dauerhaft "
                        "negativ kann auf Geldbedarf hindeuten; bei stark wachsenden Firmen ist das aber oft normal."),
    Term("eps", "Gewinn je Aktie", "Der Teil des Jahresgewinns, der auf eine einzelne Aktie entfällt.",
         full="Earnings per Share (EPS)",
         formula="Jahresüberschuss ÷ Anzahl der Aktien", unit="Währung des Unternehmens je Aktie"),
    Term("marge", "Marge", "Wie viel vom Umsatz als Gewinn übrig bleibt.",
         full="Operative Marge (Gewinnmarge)",
         formula="Betriebsergebnis ÷ Umsatz × 100", unit="Prozent",
         interpretation="Eine höhere und steigende Marge spricht für Preismacht und Effizienz."),
    Term("umsatzwachstum", "Umsatzwachstum", "Um wie viel Prozent der Umsatz gegenüber dem Vorjahreszeitraum gestiegen ist.",
         formula="(Umsatz jetzt ÷ Umsatz im Vorjahreszeitraum − 1) × 100", unit="Prozent"),
    Term("nettoverschuldung", "Nettoverschuldung", "Die Schulden eines Unternehmens abzüglich seiner flüssigen Mittel.",
         formula="Finanzschulden − liquide Mittel", unit="Währung des Unternehmens, meist in Millionen",
         interpretation="Negativ heißt: Das Unternehmen hat mehr Geld als Schulden."),
    Term("marktkapitalisierung", "Marktkapitalisierung", "Der Börsenwert des ganzen Unternehmens.",
         formula="Aktienkurs × Anzahl ausstehender Aktien", unit="Währung der Aktie, meist in Milliarden"),
    Term("dividendenrendite", "Dividendenrendite", "Die jährliche Dividende im Verhältnis zum Aktienkurs.",
         formula="Dividende je Aktie pro Jahr ÷ Aktienkurs × 100", unit="Prozent",
         interpretation="Eine sehr hohe Rendite kann ein Warnsignal sein, wenn der Kurs stark gefallen ist.",
         example="Dividende 3 € bei Kurs 100 € → 3 %."),
    Term("kgv_erwartet", "KGV (erwartet)",
         "Das KGV auf Basis des von Analysten geschätzten Gewinns der nächsten zwölf Monate.",
         full="Forward P/E", formula="Aktienkurs ÷ erwarteter Gewinn je Aktie", unit="Faktor",
         interpretation="Liegt es unter dem normalen KGV, rechnen die Analysten mit steigenden Gewinnen. Es ist eine "
                        "Schätzung und kann sich ändern."),
    Term("kennzahlen", "Kennzahlen (Fundamentaldaten)",
         "Zahlen aus den Geschäftsberichten eines Unternehmens, die zeigen, wie groß, profitabel und gesund es ist und "
         "wie teuer die Aktie dafür ist. Quelle ist Yahoo Finance; Stand und Berichtszeitraum stehen unter der Karte.",
         interpretation="Eine Kennzahl allein sagt wenig. Sinnvoll ist der Vergleich mit Firmen derselben Branche und mit "
                        "früheren Jahren. Fehlt ein Wert („–“), hat Yahoo ihn nicht geliefert; „negativ“ steht dort, wo ein "
                        "Verlust die Kennzahl unbrauchbar macht."),
    Term("kbv", "KBV (Kurs-Buchwert-Verhältnis)",
         "Wie der Kurs im Verhältnis zum bilanziellen Eigenkapital je Aktie steht.",
         full="Price/Book, P/B", formula="Kurs ÷ Eigenkapital je Aktie", unit="Faktor",
         interpretation="Unter 1 kostet die Aktie weniger als das Eigenkapital in der Bilanz. Für Firmen mit wenig "
                        "Sachwerten (Software) ist das KBV wenig aussagekräftig."),
    Term("bruttomarge", "Bruttomarge",
         "Der Anteil des Umsatzes, der nach den direkten Herstellungskosten übrig bleibt.",
         formula="(Umsatz − Herstellungskosten) ÷ Umsatz", unit="Prozent",
         interpretation="Eine hohe, stabile Bruttomarge spricht für Preismacht oder ein starkes Produkt."),
    Term("nettomarge", "Nettomarge",
         "Der Anteil des Umsatzes, der am Ende als Gewinn nach Steuern und Zinsen bei den Aktionären bleibt.",
         formula="Gewinn nach Steuern ÷ Umsatz", unit="Prozent",
         interpretation="Von 100 Umsatz bleiben bei 10 % Nettomarge 10 als Gewinn. Rot ist ein Verlust."),
    Term("eigenkapitalrendite", "Eigenkapitalrendite",
         "Wie viel Gewinn das Unternehmen mit dem Geld seiner Eigentümer in einem Jahr erwirtschaftet.",
         full="Return on Equity, ROE", formula="Gewinn nach Steuern ÷ Eigenkapital", unit="Prozent",
         interpretation="Hohe Werte sprechen für ein gutes Geschäft, können aber auch von hohen Schulden kommen: "
                        "gemeinsam mit dem Verschuldungsgrad lesen."),
    Term("gewinnwachstum", "Gewinnwachstum",
         "Um wie viel der Gewinn des letzten Quartals über dem desselben Quartals im Vorjahr liegt.",
         formula="Gewinn Quartal ÷ Gewinn Vorjahresquartal − 1", unit="Prozent",
         interpretation="Springt stark, wenn der Vorjahreswert klein war. Ein einzelnes Quartal ist kein Trend."),
    Term("umsatz", "Umsatz",
         "Alle Einnahmen aus dem Verkauf von Waren und Leistungen in den letzten zwölf Monaten.",
         unit="Berichtswährung des Unternehmens", interpretation="Der Umsatz ist nicht der Gewinn."),
    Term("bargeld", "Bargeld",
         "Zahlungsmittel und kurzfristig verfügbare Anlagen in der Bilanz.",
         unit="Berichtswährung des Unternehmens", interpretation="Im Vergleich zu den Schulden zu lesen."),
    Term("schulden", "Schulden",
         "Alle verzinslichen Verbindlichkeiten (Kredite, Anleihen) laut letzter Bilanz.",
         unit="Berichtswährung des Unternehmens",
         interpretation="Viel Bargeld bei wenig Schulden ist ein Puffer, hohe Schulden bei wenig Cashflow ein Risiko."),
    Term("verschuldungsgrad", "Verschuldungsgrad",
         "Wie viel Schulden auf einen Euro Eigenkapital kommen.",
         full="Debt/Equity", formula="Schulden ÷ Eigenkapital", unit="Prozent",
         interpretation="100 % heißt: gleich viel Schulden wie Eigenkapital. Was normal ist, hängt von der Branche ab "
                        "(Banken und Versorger sind höher verschuldet)."),
    Term("current_ratio", "Liquidität (Current Ratio)",
         "Ob das Unternehmen seine kurzfristigen Rechnungen aus kurzfristigen Mitteln bezahlen kann.",
         formula="Umlaufvermögen ÷ kurzfristige Verbindlichkeiten", unit="Faktor",
         interpretation="Über 1 reicht das Geld für das nächste Jahr, unter 1 wird es eng."),
    Term("ausschuettungsquote", "Ausschüttungsquote",
         "Welcher Teil des Gewinns als Dividende ausgezahlt wird.",
         full="Payout Ratio", formula="Dividende ÷ Gewinn", unit="Prozent",
         interpretation="Über 100 % heißt: mehr Dividende als Gewinn, das hält nicht dauerhaft."),
    Term("beta", "Beta",
         "Wie stark die Aktie im Vergleich zum Gesamtmarkt schwankt.",
         unit="Faktor",
         interpretation="1 heißt: so stark wie der Markt. Über 1 schwankt sie stärker, unter 1 schwächer. Beruht auf "
                        "vergangenen Kursen."),
    Term("empfehlung", "Empfehlungen der Analysten",
         "Wie viele Analysten die Aktie zum Kaufen, Halten oder Verkaufen einstufen. Gezeigt wird die Verteilung, "
         "keine Gesamtnote.",
         full="Analysten-Ratings", unit="Anzahl der Analysten",
         interpretation="Mit Vorsicht lesen: Analysten und ihre Banken haben Geschäftsbeziehungen zu den Unternehmen, "
                        "deshalb sind „Verkaufen“-Urteile selten und „Halten“ heißt oft „eher verkaufen“. "
                        "Aussagekräftiger als das Niveau ist, ob sich die Verteilung verändert. Bei weniger als fünf "
                        "Analysten sagt sie kaum etwas."),
    Term("gewinnueberraschung", "Gewinnüberraschung",
         "Wie weit der tatsächliche Gewinn je Aktie eines Quartals von dem abwich, was Analysten im Durchschnitt "
         "erwartet hatten.",
         full="Earnings Surprise", formula="(Gewinn tatsächlich − Gewinn erwartet) ÷ |Gewinn erwartet|", unit="Prozent",
         interpretation="Positiv heißt: besser als erwartet. Das bewegt den Kurs oft stärker als der Gewinn selbst. "
                        "Viele Firmen übertreffen die Erwartung regelmäßig, weil sie diese vorher lenken; entscheidend "
                        "ist deshalb, wie deutlich sie übertroffen wird und ob das Wachstum dahinter stimmt.",
         example="Erwartet 1,89, tatsächlich 2,02 → +6,9 %."),
    Term("kursziel", "Kursziel", "Der Preis, den ein Analyst für die Aktie in etwa zwölf Monaten erwartet.",
         unit="Währung der Aktie",
         interpretation="Eine Einschätzung, keine Garantie. Viele Analysten liegen oft daneben; wichtiger als eine "
                        "einzelne Zahl ist, wie sich der Durchschnitt über die Zeit verändert."),
    Term("konsens", "Konsens", "Der Durchschnitt der Schätzungen vieler Analysten, zum Beispiel für Umsatz und "
                               "Gewinn je Aktie.",
         interpretation="Er gilt als das, was der Markt erwartet. Entscheidend für den Kurs ist oft, ob die echten Zahlen "
                        "darüber oder darunter liegen."),
    Term("guidance", "Guidance", "Die Prognose, die das Unternehmen selbst für sein nächstes Quartal oder Jahr abgibt.",
         full="Unternehmensausblick (Guidance)",
         interpretation="Eine Anhebung wird meist positiv gesehen, eine Senkung negativ, oft stärker als die "
                        "Quartalszahlen selbst."),
)


def term(key):
    """Der Begriff zu einem Schlüssel; KeyError, wenn es ihn nicht gibt."""
    return GLOSSARY[key]


def link(text, key):
    """Ein Text als anklickbarer Verweis auf einen Begriff, für Beschriftungen mit Rich Text."""
    if key not in GLOSSARY:
        raise KeyError(key)
    return f'<a href="{LINK_PREFIX}{key}">{html.escape(text)}</a>'


def key_of_link(url):
    """Der Schlüssel aus einem Verweis von link(); None, wenn es keiner dieser Verweise ist."""
    url = str(url)
    if url.startswith(LINK_PREFIX) and url[len(LINK_PREFIX):] in GLOSSARY:
        return url[len(LINK_PREFIX):]
    return None
