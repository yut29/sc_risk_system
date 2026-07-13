# Entwurf: E-Mail-Antwort an Baris — 2026-07-07

> Bezug: Baris' Feedback-Mail vom 2026-06-30
> Status: **Entwurf** — bitte prüfen und anpassen

---

**An**: Baris.Albayrak@faps.fau.de
**Betreff**: Re: AW: AW: Kurzes Update: Masterprojekt – Yutong Liu

---

Hi Baris,

vielen Dank für dein ausführliches Feedback — die Hinweise waren sehr hilfreich, ich gehe die Punkte gerade sorgfältig durch. Kurzer Zwischenstand:

**TierWeight:** Die Inkonsistenz ist behoben — Tabelle, Beispiele und Code verwenden jetzt einheitlich die distanzbasierten Werte (1,0 / 0,6 / 0,35 / 0,15).

**Kanten & Kapazitätsdaten:** Ich habe die zwei manuell eingefügten "realen" Kanten sowie die drei ergänzten Kapazitätswerte (Upstream-Imputation) wieder entfernt. Beides ließ sich fachlich nicht sauber begründen (NAATBatt enthält keine echten Lieferbeziehungen; der Materialmix der drei Anlagen machte einen einheitlichen Medianwert unplausibel) und der Anteil war mit 2 Kanten bzw. 0,8 % der Anlagen ohnehin vernachlässigbar. Jetzt sind alle 1.537 Kanten einheitlich als simuliert (Keyword-Matching + geografische Nähe) ausgewiesen, und Kapazität ist binär: bekannt (NAATBatt-Originalwert) oder unbekannt — nicht Nullkapazität.

**Datenklassifikation:** Wie vorgeschlagen unterscheide ich jetzt durchgängig drei Datentypen:

| Kategorie | Beschreibung | Beispiele |
|---|---|---|
| ① Reale Daten | Direkt aus NAATBatt, unverändert | Unternehmensname, Adresse, Segment, Produkttyp, Koordinaten, production_capacity_raw (wo vorhanden), production_units |
| ② Synthetisch erzeugte Daten | Fehlende Informationen werden durch nachvollziehbare Regeln ergänzt | Alle Kanten im Wissensgraphen (Keyword-Matching + geografische Nähe; NAATBatt enthält keine echten Lieferbeziehungen) |
| ③ Modellannahmen & Vereinfachungen | Bewusste Designentscheidungen zur Modellierung fehlender oder komplexer Faktoren | lead_time_weeks, import_dependency, supplier_concentration, import_origin_region |

Zusätzlich habe ich den Vulnerability-Faktor "SingleSource" in "SupplierConcentration" umbenannt und klarer als Marktstrukturrisiko definiert (Quellen: USGS MCS 2026, IEA GCMO 2025), damit auch hier begrifflich sauber zwischen Importabhängigkeit (geopolitisches Risiko) und Anbieterkonzentration (Marktrisiko) getrennt ist.

Außerdem steht inzwischen ein erstes Streamlit-Interface, über das sich die generierten Risikoberichte anzeigen lassen — die Testphase dafür (Szenario-Läufe, Stabilität) steht aber noch aus.

Melde mich, sobald ich die restlichen Punkte (Kapazitätsbehandlung im Detail, Vulnerability-Definitionen in der Doku) final durchdokumentiert habe.

Beste Grüße
Yutong

---

> **Entwurfsnotizen:**
> - SupplierConcentration-Umbenennung war nicht explizit von dir erwähnt, ergänzt weil sie zu deinem Punkt "Definition der Vulnerability-Faktoren" passt — ggf. kürzen falls zu viel Detail
> - Kapazität ist jetzt bewusst binär (bekannt/unbekannt) statt mit Confidence-Faktor, weil die einzige "imputed"-Kategorie (3 Anlagen) verworfen wurde — falls Baris nach dem Confidence-Faktor fragt, kurz erklären warum er sich erübrigt hat
